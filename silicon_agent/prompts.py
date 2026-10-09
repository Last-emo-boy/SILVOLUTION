"""Per-design planner knowledge: what the hardware is, the IR schema, and device facts.

Kept out of loop.py so the loop stays generic. Device facts carry *measured*
numbers from real runs where we have them -- that is what lets the planner
reason instead of guess.
"""

from __future__ import annotations

MATMUL_WHAT = "an output-stationary systolic INT matrix-multiply accelerator"

MATMUL_IR = """IR fields and legal ranges:
  pe_rows    int 1..32   rows of processing elements (throughput = pe_rows*pe_cols MACs/cycle)
  pe_cols    int 1..32
  data_width int in {4,8,12,16}  operand width (bits)
  acc_width  int 2*data_width+8 .. 48   accumulator width
  pipe_mul   int 0..3    fabric register stages between the multiplier output and the accumulator
  reg_in     bool        register the A/B inputs at the array boundary
  dsp_regs   str in {"none","out","in_out","in_pipe_out"}  DSP-internal pipeline registers
                         (none = combinational multiplier; each extra stage adds 1 cycle latency, no fabric cost)
  acc_in_dsp bool        xc7s50 only (ignored on ecp5): true = accumulate inside the DSP48E1 post-adder
                         (saves LUTs, but the M->P add is one unbreakable stage); false = fabric CARRY4 adder
                         after the DSP P register (costs ~acc_width/4 LUTs per PE, lets the DSP run at its registered Fmax)"""

MATMUL_IR_JSON = '{"pe_rows":..,"pe_cols":..,"data_width":..,"acc_width":..,"pipe_mul":..,"reg_in":..,"dsp_regs":"..","acc_in_dsp":..}'

MATMUL_FACTS = {
    "ecp5": """ECP5 facts: each PE uses one MULT18X18D (DSP) when data_width<=16; the accumulator is a LUT4 carry chain whose
delay grows linearly with acc_width (about 0.07 ns per bit plus routing); routing between the DSP output and the
carry chain is the usual timing limiter unless both dsp_regs and pipe_mul isolate it; reg_in breaks the IO->array
path; the drain mux that selects one of pe_rows accumulator rows onto c_row grows with pe_rows and can become critical
at high Fmax. LUT4 count scales ~ pe_rows*pe_cols*acc_width. The 45F has 44k LUT4, 72 MULT18X18D, 108 DP16KD.
Realistic Fmax range on this device: 60-250 MHz.""",
    "xc7s50": """Spartan-7 facts: each PE uses one DSP48E1 (25x18 multiplier with internal A/B/M/P registers) when data_width<=16;
dsp_regs maps onto those internal registers (out=PREG, in_out=A/BREG+PREG, in_pipe_out=A/BREG+MREG+PREG).
Measured on this exact design (8x4 INT8, acc 32): dsp_regs=none -> 190 MHz with acc_in_dsp=true, 151 MHz with false;
dsp_regs=in_pipe_out -> 277 MHz with acc_in_dsp=true (critical path is the 2.3 ns DSP post-adder M->P stage, which no
other field can shorten), 299 MHz with acc_in_dsp=false (critical path moves to the fabric CARRY4 accumulator, which
acc_width then controls). So: unpipelined designs want acc_in_dsp=true; fully pipelined high-Fmax designs want false.
pipe_mul>0 on this device makes Vivado map the extra product register into a second DSP48E1 per PE (doubles DSP use) -- avoid it.
The one-hot drain select to c_row and the skew shift chains are long high-fanout nets and show up as LUT->FDRE routing
paths at >250 MHz; fewer pe_rows shortens them. LUT6 count scales ~ pe_rows*pe_cols*acc_width/2 when acc_in_dsp=false,
much less when true. The XC7S50 has 32.6k LUT6, 120 DSP48E1, 75 BRAM36. Realistic Fmax range: 100-400 MHz.""",
}


FIR_WHAT = "a fixed-coefficient FIR filter (the coefficient set is part of the task and never changes)"

FIR_IR = """IR fields and legal ranges (taps, data_width, coef_width and the coefficients are fixed by the task):
  symmetric  bool        exploit h[k]==h[N-1-k]: share one multiplier between the two taps. Halves multipliers
                         (ceil(N/2)), adds a pre-adder (+1 bit) in the folded form. Only legal if the coefficient
                         set is symmetric (the task says so).
  fold       int 1..32   taps per physical multiplier. fold=1: transposed form, 1 sample/cycle, U multipliers
                         (U = N or ceil(N/2) with symmetric). fold=F>1: direct form, each of U/F MACs sweeps F taps
                         through a coefficient ROM, accepts one sample every F cycles (throughput 1/F). F must divide U.
  acc_width  int MIN..64 accumulator width; MIN = data_width+coef_width+ceil(log2 N)+(1 if symmetric). Carry-chain
                         length, so it trades LUTs and timing.
  pipe_mul   int 0..3    fabric register stages after the multiplier
  reg_in     bool        register the input sample
  dsp_regs   str in {"none","out","in_out","in_pipe_out"}  DSP-internal pipeline registers
  acc_in_dsp bool        xc7s50 only: accumulate inside the DSP48E1 post-adder (true) or in a fabric CARRY4 adder (false)
  coef_port  bool        task parameter (never change): coefficients in a writable register file instead of constants;
                         costs U*coef_width FFs + a decoder, and the multipliers can no longer be constant-folded
  sum_tree   bool        fold>1 only: reduce the M=U/F partial sums in a registered balanced tree (ceil(log2 M) register
                         stages, +ceil(log2 M) cycles latency, no throughput change) instead of one combinational M-way
                         adder chain into the output register. When the critical path starts at part[*] and ends at
                         out_data (the post-accumulator reduction), this is the knob that shortens it; pipe_mul/dsp_regs
                         sit before the accumulator and cannot.
  pipe_in    bool        fold>1 only: register the multiplier operands after the symmetric pre-adder and the F:1 tap mux
                         (+1 cycle latency, no throughput change). When the critical path runs x_d -> pre-adder carry
                         chain -> tap mux -> the DSP input (ends on u_m, before any multiplier register), this is the knob;
                         reg_in only registers the boundary sample and dsp_regs/pipe_mul start at the multiplier.

Structure summary: multipliers = ceil(U / fold); throughput = 1/fold samples per cycle; the transposed form's adders
are all local (mult -> add -> register), so its Fmax does not degrade with N; the folded form adds a tap-operand
mux (U/F : 1) and a coefficient ROM in front of each multiplier, which costs LUTs and routing per fold step."""

FIR_IR_JSON = '{"symmetric":..,"fold":..,"acc_width":..,"pipe_mul":..,"reg_in":..,"dsp_regs":"..","acc_in_dsp":..,"sum_tree":..,"pipe_in":..}'

FIR_FACTS = {
    "gatemate": """GateMate facts, measured on a 16-tap 12x12 FIR: the multiplier is a cascade of ~20 CPE_MULT cells (CC_MULT) at
0.2-0.6 ns each, about 9 ns for 12x12 plus routing; nothing in the IR pipelines inside that cascade (dsp_regs stages are
fabric registers around it), so the unpipelined multiply caps Fmax near 55-60 MHz for 12-bit operands (56.6 MHz with
symmetric fold=1, reg_in, dsp_regs in_out; the seed spread is tight). Narrower operands shorten the cascade; a register
knob cannot. Each P&R takes ~1 min per seed.""",
    "ecp5": """ECP5 facts, measured on a 32-tap 16x16 lowpass, acc 40: with dsp_regs=none the multipliers are behavioral
constants, so yosys constant-folds zero taps and shares equal coefficients itself -- the symmetric flag then changes
nothing (12 DSP either way on that filter, ~230 MHz, critical path = accumulator carry chain). With dsp_regs != none
each multiplier is an explicit MULT18X18D: non-symmetric fold=1 -> 32 DSP, 187.9 MHz; symmetric fold=1 -> 16 DSP,
220.0 MHz; symmetric fold=4 -> 4 DSP, 122.1 MHz (the 4:1 operand mux + coefficient ROM in front of the DSP becomes
critical); symmetric fold=4 dsp_regs=out -> 87.3 MHz. So: to go *below* what yosys's own sharing gives you, fold;
folding trades 1/F throughput for 1/F DSPs and loses Fmax to the operand mux; in_pipe_out is the right dsp_regs
whenever an explicit DSP is in play. The 45F has 44k LUT4, 72 MULT18X18D. Realistic Fmax range: 60-250 MHz.""",
    "xc7s50": """Spartan-7 facts, measured on this 32-tap 16x16 filter: with acc_in_dsp=true Vivado absorbs the WHOLE transposed
form into DSP48E1s -- multipliers, the per-tap adds via the PCIN cascade, even the sum registers -- so fold=1 gave
495 MHz and 1 LUT but 45 DSP48E1 (acc_width=40 needs extra slices beyond the 32 multipliers). That is the fastest
FIR structure on this device and costs ~1.4 DSP per tap. With acc_in_dsp=false the adds are fabric CARRY4 chains
after the DSP P register: 1 DSP per multiplier, LUTs ~ U*acc_width/2, Fmax set by the carry chain.
Folded form (fold>1): the tap-operand mux + coefficient ROM in front of each DSP is LUT6 logic; measured
symmetric fold=2 and fold=4 both land near 150 MHz regardless of acc_in_dsp -- the mux/partial-sum combine, not
the DSP, is the wall, so folding on this device buys DSPs but caps Fmax around 150-170 MHz. For >250 MHz keep
fold=1. dsp_regs: a fully registered DSP48E1 runs above 400 MHz; prefer dsp_regs over pipe_mul (pipe_mul may
spend a second DSP). The XC7S50 has 32.6k LUT6, 120 DSP48E1. Realistic Fmax range: 120-500 MHz.""",
}


CONV_WHAT = "a streaming single-channel K x K 2-D convolution (line buffers + sliding window + multiply-adder tree)"

CONV_IR = """IR fields and legal ranges:
  k          int in {3,5,7}   kernel size (task parameter)
  width      int 16..2048     image width in pixels (task parameter; each line buffer holds `width` pixels)
  data_width int in {8,12,16} pixel bits (task parameter)
  coef_width int in {8,12,16} kernel coefficient bits (task parameter)
  acc_width  int >= data_width+coef_width+ceil(log2(k*k))+1 .. 48   output/adder-tree width
  line_mem   str in {"bram","lut"}  the K-1 line buffers in block RAM (1 BRAM per line for 8-bit x <=2048 pixels) or
                                     as LUT/FF shift registers (width*(K-1)*data_width flip-flops -- only sane for tiny widths)
  par_rows   int, divides k   rows of the window multiplied per cycle: k -> fully parallel, k*k multipliers, 1 pixel/cycle;
                              1 -> k multipliers, 1/k pixel/cycle (the window is held while its rows are multiplied in turn)
  tree_regs  int 0..3         pipeline register levels inside the adder tree (each adds 1 cycle latency, splits the tree depth)
  pipe_mul   int 0..3         fabric register stages after each multiplier
  dsp_regs   str in {"none","out","in_out","in_pipe_out"}  multiplier register stages (coefficients are constants, so on
                              every device these are fabric/DSP output registers; there is no DSP cascade here)

Structure summary: multipliers = k*par_rows; throughput = par_rows/k pixels per cycle; the adder tree has
ceil(log2(k*par_rows)) levels of acc_width-bit adders, which is the usual critical path when tree_regs=0;
with par_rows<k a partial-sum accumulator follows the tree. BRAM read latency is already pipelined and is not on the
critical path. The window shift registers cost k*k*data_width FFs regardless of par_rows."""

CONV_IR_JSON = '{"acc_width":..,"line_mem":"..","par_rows":..,"tree_regs":..,"pipe_mul":..,"dsp_regs":".."}'

CONV_FACTS = {
    "ecp5": """ECP5 facts, measured on a 3x3 8-bit kernel, width 640, acc 24: line_mem=bram uses 2 DP16KD; par_rows=3 (9
multipliers, 1 px/cycle) with dsp_regs=out, tree_regs=1 -> 140.7 MHz, 283 LUT, 0 DSP (yosys folds the constant 8x8
products into LUTs: cheaper than MULT18X18D for small constants); par_rows=1 (3 multipliers) -> 48.8 MHz because the
partial-sum accumulator loop (acc + tsum) has no register and the MULT18X18D path lands in it -- add pipe_mul/tree_regs
to split it. A 5x5 at width 320, par_rows=5, tree_regs=2 -> 121 MHz, 680 LUT, 9 DSP, 4 BRAM. Critical path with
tree_regs<=1 is the adder tree (LUT4 + carry chain ~1 ns per level plus routing).""",
}


CHAIN_WHAT = "a two-to-four-stage cascade of fixed-coefficient FIR filters connected by valid/ready FIFOs (a datapath, not one block)"

CHAIN_IR = """IR: {"stages": [<FIR IR>, <FIR IR>, ...], "fifo_depth": 4|8|16|32|64, "round_mode": "truncate"|"round"}
Each stage is a complete FIR IR (see below); the locked task fields of every stage (taps, data_width, coef_width,
cutoff) are fixed -- you edit each stage's acc_width, symmetric, fold, pipe_mul, reg_in, dsp_regs (and acc_in_dsp on
Vivado). Stage i's accumulator is reduced to stage i+1's data_width (top bits, truncate or round) through a FIFO.

Per-stage FIR fields:
  acc_width  int   >= data_width+coef_width+log2(taps)(+1 if symmetric) .. 64
  symmetric  bool  share one multiplier per h[k]==h[N-1-k] pair
  fold       int   taps per multiplier (1 = transposed, 1 sample/cycle; F>1 = time-multiplexed, 1/F samples/cycle)
  pipe_mul   int 0..3, reg_in bool, dsp_regs str in {"none","out","in_out","in_pipe_out"}

Chain rules: throughput = min over stages (a folded stage throttles everything upstream through the FIFO);
DSP = sum over stages; Fmax = min over stages, and the critical path names the stage (u_s0.* / u_s1.*). The cheap
stage (few taps) rarely limits anything, so fold it hard; spend DSPs where taps are many. fifo_depth only matters
when stages have different fold values (deeper = smoother, costs LUT RAM)."""

CHAIN_IR_JSON = '{"stages":[{"acc_width":..,"symmetric":..,"fold":..,"pipe_mul":..,"reg_in":..,"dsp_regs":".."},{...}],"fifo_depth":..,"round_mode":".."}'

CHAIN_FACTS = {
    "ecp5": """ECP5 facts, measured on an 8-tap -> 16-tap 12-bit chain: stage0 fold=1 dsp_regs=out + stage1 symmetric fold=2
dsp_regs=out reg_in -> 101 MHz, 790 LUT, 12 MULT18X18D, critical path inside stage1's folded MAC (u_s1.g_mac[0]);
16-bit 8-tap -> 32-tap chain with stage1 symmetric fold=4 in_pipe_out -> 118 MHz, 1239 LUT, 7 DSP. Everything in the
FIR facts below applies per stage.\n""" + FIR_FACTS["ecp5"],
    "gowin": """Gowin GW1NR-9, measured on the 8-tap -> 16-tap 12-bit chain above: 110.8 MHz, 943 LUT, 7 MULT18X18.""",
}


DECIM_WHAT = "a polyphase decimating FIR (anti-alias lowpass + downsample by M in one block: one output per M input samples)"

DECIM_IR = """IR fields and legal ranges (taps, decim, data_width, coef_width, cutoff and the coefficients are fixed by the task):
  acc_width  int MIN..64  accumulator width; MIN = data_width+coef_width+ceil(log2 taps). Carry-chain length.
  fold       int 1..8     cycles per accepted input sample (throughput = 1/fold input samples per cycle; the output
                          rate is that / decim). The N taps of one output are swept over decim*fold cycles on
                          K = ceil(taps / (decim*fold)) multipliers, so fold trades input rate for multipliers.
  pipe_mul   int 0..3     fabric register stages after the multipliers
  reg_in     bool         register the input sample
  dsp_regs   str in {"none","out","in_out","in_pipe_out"}  DSP-internal pipeline registers
  acc_in_dsp bool         xc7s50 only
  sum_tree   bool         register the K-way reduction of one sweep step (+1 cycle); the knob when the critical
                          path runs from the multiplier outputs through the K-input adder into the accumulator

Structure summary: a delay line of the last N inputs; at each frame end (every M accepts) it is snapshotted and
the N products are swept over S = ceil(N/K) steps while the next frame streams in. Per output: N multiplications
on K multipliers -- the polyphase saving is that K is 1/M of a full-rate FIR's multiplier count at the same fold.
Critical paths: the K-way adder into the accumulator (sum_tree), the accumulator carry chain (acc_width), the
snapshot mux into the multipliers (pipe_mul/dsp_regs do not touch it)."""

DECIM_IR_JSON = '{"acc_width":..,"fold":..,"pipe_mul":..,"reg_in":..,"dsp_regs":"..","acc_in_dsp":..,"sum_tree":..}'

DECIM_FACTS = {
    "ecp5": """ECP5, measured on a 32-tap decimate-by-4, 12x12, acc 30, fold 1, dsp_regs in_pipe_out: 8 MULT18X18D, 845 LUT,
105.9 MHz; the critical path is the 8-way adder (LUT4 + carry chain) from the DSP outputs into out_data/acc --
sum_tree is the first move, then acc_width.""",
    "ice40": """iCE40, measured on a 32-tap decimate-by-4, 12x12, acc 30, fold 2, sum_tree, dsp_regs out: 4 SB_MAC16, 610 LC,
43.6 MHz; limiter is the registered 4-way sum (psum) -- the accumulator carry chain and the LUT4 adder tree.""",
}

NCO_WHAT = "an NCO/DDS with a complex mixer (phase accumulator, quarter-wave sine table, I = x*cos, Q = -x*sin): a digital down-converter front end"

NCO_IR = """IR fields and legal ranges (data_width, phase_width, sin_width, out_width are fixed by the task):
  lut_addr_width int 6..16   phase bits addressing the quarter-wave table (depth 2^(w-2) entries of sin_width bits);
                             spur floor from phase truncation ~ -6*w dBc, table size doubles per bit
  dither         bool        1-bit LFSR dither below the table address: trades a tiny noise floor rise for spur-free
  table_mem      str in {"bram","lut"}  block RAM (one BRAM, address-register timing) or logic/LUT RAM (fast, LUTs)
  pipe_lut       int 0..2    register stages after the table read (the BRAM read -> negate -> multiplier path)
  pipe_mix       int 0..3    fabric register stages after the two mixer multipliers
  reg_in         bool        register the input sample and FCW
  dsp_regs       str in {"none","out","in_out","in_pipe_out"}  DSP-internal registers of the mixer multipliers

Structure summary: phase accumulator (phase_width-bit adder: carry chain) -> table address/quadrant (1 stage) ->
table read (1 stage) -> conditional negate -> two multipliers data_width x sin_width -> truncate to out_width.
Throughput is always 1 sample/cycle; the knobs move Fmax and resources only. Critical paths: the phase adder
(phase_width), the table read + negate into the DSP (pipe_lut), the DSP output (dsp_regs/pipe_mix)."""

NCO_IR_JSON = '{"lut_addr_width":..,"dither":..,"table_mem":"..","pipe_lut":..,"pipe_mix":..,"reg_in":..,"dsp_regs":".."}'

NCO_FACTS = {
    "ecp5": """ECP5, measured on 12-bit data, 12-bit sine, 24-bit phase, 10-bit table address, dsp_regs in_pipe_out, pipe_lut 1,
pipe_mix 1: bram table -> 2 MULT18X18D, 132 LUT, 1 DP16KD, 107 MHz (limiter: the BRAM read -> negate -> DSP A input);
lut table (8-bit address) -> 211 LUT, 0 BRAM, 113 MHz. The phase accumulator is a plain carry chain.""",
    "ice40": """iCE40, measured on 12-bit data/sine, 24-bit phase, 8-bit lut table with dither, dsp_regs in_pipe_out: 2 SB_MAC16,
384 LC, 64.5 MHz. The 24-bit phase adder and the logic ROM are the limiters.""",
    "gowin": """Gowin, measured on 12-bit data/sine, 24-bit phase, 10-bit bram table, dsp_regs in_pipe_out, pipe_mix 2: 2 DSP,
114 LUT, 1 BSRAM, 195 MHz.""",
}

IIR_WHAT = "an IIR lowpass filter: a cascade of second-order sections (Butterworth biquads, transposed direct form II, exact fixed point)"

IIR_IR = """IR fields and legal ranges (sections, data_width, coef_width, cutoff and the coefficients are fixed by the task):
  coef_frac   int 4..coef_width-2  fractional bits of the quantized coefficients: pole accuracy (a sharp lowpass has
                                   poles near z=1 and needs many bits or it goes unstable -- validate() rejects that)
  state_width int dw+2..48         bits of the two state registers per section: headroom above data_width + the
                                   rounding noise floor; the carry chains of the state adders scale with it
  pipe_mul    int 0..1             register after the feed-forward (b) multipliers; the recursive (a) multiplies and
                                   the state adders form the loop y -> a1*y, a2*y -> s1/s2 -> y which NO register can
                                   break without changing the filter: it is the intrinsic critical path
  reg_in      bool                 register the input
  dsp_regs    str in {"none","out","in_out","in_pipe_out"}  DSP-internal registers of the feed-forward multipliers only

Structure summary: 5 multipliers per section (b0 b1 b2 a1 a2), one sample per cycle, output saturated to data_width.
Fmax is set by the recursive loop: multiplier (data_width x coef_width) + two adders + saturation in one cycle, so
it is low (tens of MHz on an FPGA) and the levers are coef_width/data_width (task), state_width (shorter carry
chains), and the device. Throughput cannot change; the objectives that move are fmax and resources."""

IIR_IR_JSON = '{"coef_frac":..,"state_width":..,"pipe_mul":..,"reg_in":..,"dsp_regs":".."}'

IIR_FACTS = {
    "ecp5": """ECP5, measured on 2 sections, 16-bit data, 18-bit coefficients (frac 15), state 24, dsp_regs in_pipe_out: 8 MULT18X18D,
584 LUT, 36.2 MHz; the critical path is the recursive loop s1 -> y -> a1*y (carry chain) -> s1.""",
    "gowin": """Gowin, same filter, dsp_regs none: 8 DSP, 505 LUT, 52.8 MHz (the loop again).""",
    "ice40": """iCE40, 12-bit data, 16-bit coefficients (frac 14), state 20, pipe_mul 1: 8 SB_MAC16, 502 LC, 23.7 MHz.""",
}

CORDIC_WHAT = "a fixed-point CORDIC engine (rotation: sin/cos; vectoring: magnitude/atan2) as a streaming pipeline or an iterative unit"

CORDIC_IR = """IR fields and legal ranges:
  mode        str rotate|vector   (task parameter)
  width       int 8..32           data path bits (task parameter)
  iterations  int 4..width+2      micro-rotations = output precision in bits; each is one shift-add stage
  unroll      int, divides iterations   micro-rotations per pipeline register: 1 = one per stage (deep pipeline,
                                   high Fmax, iterations registers); iterations = all combinational (one stage, low Fmax)
  fold        int 1 | iterations  1 = pipelined, 1 sample/cycle; iterations = one iterative engine reused every cycle,
                                   1/iterations samples per cycle, ~1/iterations the registers and adders
  guard_bits  int 0..4            extra LSBs through the rotation (precision at the cost of width)
  scale_comp  bool                constant-multiply pre-scale by 1/K (adds one multiplier / DSP and a stage)

Structure summary: no multipliers at all (unless scale_comp); the datapath is three adders of width+guard bits per
micro-rotation plus barrel-free constant shifts. Fmax is set by the adder carry chain of one stage: with unroll=U the
critical path is U chained adders. Resources: pipelined ~ 3*(width+guard)*iterations FFs + the same in LUT adders;
iterative ~ 3*(width+guard) FFs + one adder set + a shift mux (the variable shifter costs LUTs)."""

CORDIC_IR_JSON = '{"iterations":..,"unroll":..,"fold":..,"guard_bits":..,"scale_comp":..}'

CORDIC_FACTS = {
    "ecp5": """ECP5 facts, measured on a 16-bit rotate, 16 iterations, guard 2: unroll=2 pipelined -> 108.6 MHz, 2706 LUT, 467 FF
(two chained 18-bit adders per stage set the period); Gowin GW1NR-9 same design -> 181.8 MHz, 2336 LUT; iCE40 UP5K
-> 32 MHz, 3950 LUT (over half the device). Rule: unroll=1 roughly doubles Fmax over unroll=2 at the cost of 2x FFs;
fold=iterations cuts LUTs ~iterations-fold but throughput too.""",
}

FFT_WHAT = "a streaming N-point complex FFT (radix-2 single-path delay feedback pipeline, one sample per cycle)"

FFT_IR = """IR fields and legal ranges:
  n           int power of 2, 16..4096   (task parameter)
  data_width  int 8..24                  (task parameter)
  tw_width    int 8..18                  twiddle factor bits (precision of the rotations)
  growth      str scale|grow             scale: /2 per stage, output = FFT/N in data_width bits; grow: +1 bit per stage,
                                         exact FFT, output width data_width+log2(n) (wider adders and multipliers)
  tw_mem      str lut|bram               twiddle ROMs (N/2^(s+1) entries per stage) in LUTs or block RAM
  delay_mem   str auto|lut|bram          the delay-feedback lines (N/2, N/4, ..., 1 deep): LUT shift registers or BRAM
  bram_min    int 16..512                with auto: lines at least this deep go to BRAM
  mul_regs    int 0..3                   pipeline registers after each stage's complex multiplier (4 real multipliers)
  bf_regs     int 0|1                    register the butterfly output
  dsp_regs    str in {"none","out","in_out","in_pipe_out"}  multiplier register stages (mul_regs + dsp_regs <= 3)
  bit_reverse bool                       add an N-sample reorder buffer so the output is in natural order (+N latency, +BRAM)
  cmul        str four|three|lut         complex multiplier per stage: 4 real multipliers (4 DSPs), the 3-multiplier Gauss form
                                         (3 DSPs, two extra pre-adders -> one more adder level on the DSP input path) or
                                         LUT-only (0 DSPs, ~3*w*tw LUT4 per real multiplier, slow). The last two stages multiply by 1/-j
                                         and need no multiplier, so DSP count = (log2(n)-2) x
                                         {4, 3, 0}; use three/lut when the device's multiplier budget is the binding bound

Structure summary: log2(n) stages, each a butterfly + delay line + one complex multiplier (except the last stage); the
multiplier costs 4 DSPs when data_width and tw_width fit the DSP (3 with Karatsuba on Vivado); the critical path with
few registers is the complex multiply-add (two products + add + rounding) -- mul_regs/dsp_regs break it. Delay lines
deeper than ~32 are cheaper in BRAM; shallow ones in LUTs. Throughput is always 1 sample/cycle; latency ~ n + stages."""

FFT_IR_JSON = '{"tw_width":..,"growth":"..","tw_mem":"..","delay_mem":"..","bram_min":..,"mul_regs":..,"bf_regs":..,"dsp_regs":".."}'

FFT_FACTS = {
    "ecp5": """ECP5 facts, measured on a 256-point 16-bit FFT, tw 16, scale, delay auto/bram_min 32, mul_regs 1, bf_regs 1,
dsp_regs out: 47.1 MHz, 3695 LUT, 1868 FF, 28 MULT18X18D, 3 DP16KD -- the multiply-add-round of each stage is
unpipelined (mul_regs 1 only registers the output), so the complex multiplier + rounding adder is the critical path.
More mul_regs and in_pipe_out dsp_regs are the first moves; growth=grow widens every adder.""",
    "gowin": """Gowin GW1NR-9 has 20 MULT18X18 in all: a 256-point FFT (8 stages, 6 multiplying) needs 24 with cmul=four and
does not fit; cmul=three needs 18 and fits, cmul=lut needs none but ~3x the LUTs. Delay lines >= 32 deep belong in
BRAM (26 BSRAM). Do not retry a cmul=four shape that was reported as not fitting.""",
}


GRAPH_WHAT = "a datapath: a chain of 2-6 heterogeneous stream nodes (fir, decim, nco, iir, conv2d, cordic, fft) connected by valid/ready FIFOs"

GRAPH_IR = """IR: {"nodes": [{"kind": "fir"|"decim"|"nco"|"iir"|"conv2d"|"cordic"|"fft", "ir": <that design's IR>}, ...], "fifo_depth": 4..64, "adapt": "truncate"|"round"|"saturate"}
The node kinds and each node's task-defining fields are fixed; you edit each node's architecture knobs (the same
knobs those designs expose on their own: fold/symmetric/dsp_regs/... for fir, par_rows/tree_regs/line_mem for conv2d,
unroll/fold/guard_bits for cordic, mul_regs/bf_regs/growth/delay_mem for fft), plus fifo_depth and adapt.
Rules: throughput = min over nodes; resources add up; Fmax = min over nodes and the critical path is prefixed with the
node (u_n0.*, u_n1.*); fifo_depth must exceed the upstream node's latency + 2 (the graph gates the source, nodes have no
back-pressure input), so a deep pipeline upstream needs a deeper FIFO. Spend resources on the node that limits."""

GRAPH_IR_JSON = '{"nodes":[{"kind":"..","ir":{...}},{"kind":"..","ir":{...}}],"fifo_depth":..,"adapt":".."}'

GRAPH_FACTS = {
    "ecp5": """ECP5, measured on fir(8 taps, dsp_regs out) -> cordic(12-bit, 12 iterations, unroll 2): 115 MHz, 1377 LUT, 8 DSP; the
cordic's two chained adders per stage limit. Gowin GW1NR-9 same graph: 124 MHz, 1074 LUT, 3 MULT. Per-node facts below.\n""" + FIR_FACTS["ecp5"],
}


PROMPTS = {
    "matmul": {"what": MATMUL_WHAT, "ir": MATMUL_IR, "ir_json": MATMUL_IR_JSON, "facts": MATMUL_FACTS},
    "graph": {"what": GRAPH_WHAT, "ir": GRAPH_IR, "ir_json": GRAPH_IR_JSON, "facts": GRAPH_FACTS},
    "decim": {"what": DECIM_WHAT, "ir": DECIM_IR, "ir_json": DECIM_IR_JSON, "facts": DECIM_FACTS},
    "nco": {"what": NCO_WHAT, "ir": NCO_IR, "ir_json": NCO_IR_JSON, "facts": NCO_FACTS},
    "iir": {"what": IIR_WHAT, "ir": IIR_IR, "ir_json": IIR_IR_JSON, "facts": IIR_FACTS},
    "cordic": {"what": CORDIC_WHAT, "ir": CORDIC_IR, "ir_json": CORDIC_IR_JSON, "facts": CORDIC_FACTS},
    "fft": {"what": FFT_WHAT, "ir": FFT_IR, "ir_json": FFT_IR_JSON, "facts": FFT_FACTS},
    "fir_chain": {"what": CHAIN_WHAT, "ir": CHAIN_IR, "ir_json": CHAIN_IR_JSON, "facts": CHAIN_FACTS},
    "fir": {"what": FIR_WHAT, "ir": FIR_IR, "ir_json": FIR_IR_JSON, "facts": FIR_FACTS},
    "conv2d": {"what": CONV_WHAT, "ir": CONV_IR, "ir_json": CONV_IR_JSON, "facts": CONV_FACTS},
}

GENERIC_FACTS = {
    "behavioral": """Multipliers are behavioral (`*`); yosys maps them to the device DSP ({dsp_kind}) when the operands fit
({dsp_width}-bit) and the DSP has no usable internal pipeline from this flow, so dsp_regs stages are emitted as fabric
registers (they still break the multiplier output away from the accumulator). Accumulators are {lut_kind} carry chains.
The usual limiter is routing between the DSP output and the carry chain; reg_in breaks the IO->array path.""",
    "ecp5": """Each multiplier is an explicit {dsp_kind}; dsp_regs selects its internal registers. Accumulators are {lut_kind}
carry chains whose delay grows with acc_width.""",
    "vivado": """Vivado infers {dsp_kind} and retimes dsp_regs/pipe_mul stages into it; acc_in_dsp chooses between the DSP
post-adder and a fabric CARRY4 accumulator.""",
    "asic": """ASIC standard cells (OpenROAD, sky130hd, typical corner 1.8 V 25 C): there are no DSPs or LUTs. Multipliers
are synthesized into full/half-adder cells (fa_1/ha_1) by yosys+ABC, so `dsp` is always 0 and dsp_regs stages are plain
flip-flops; a 12x12 multiplier is ~250 cells. `lut` reports combinational standard cells, `ff` sequential cells, and
`area_um2` the placed standard-cell area (what a tapeout pays for) -- minimize_area is the ASIC objective, minimize_lut
tracks it. The limiter is almost always the adder carry chain (ripple fa_1 cells, ~0.45 ns each at this corner) between
registers: acc_width and pipeline knobs (pipe_mul, sum_tree, pipe_in, tree_regs) are the levers; routing is a small part
of the delay (wire delay is folded into each cell's delay in the critical path). One flow (synthesis, floorplan, place,
CTS, route, STA) takes 2-10 minutes; the SDC asks for the run's target so the resizer works toward it.""",
}


def prompts_for(kind: str) -> dict:
    """PROMPTS entry for a design kind; BYO-RTL kinds get one built from the bundle's parameter declarations."""
    if kind in PROMPTS:
        return PROMPTS[kind]
    if kind.startswith("rtl:"):
        from .designs import DESIGNS
        from . import rtl as _rtl
        b = DESIGNS[kind].ir_cls._bundle_ref
        return {"what": f"the user's own RTL `{b['top']}` ({b.get('name', kind)}): " + str(b.get("description", ""))[:300],
                "ir": _rtl.ir_schema(b), "ir_json": "{" + ", ".join(f'"{k}":..' for k in (b.get("params") or {})) + "}", "facts": {}}
    raise KeyError(kind)


def device_facts(kind: str, backend: str) -> str:
    """Measured facts when we have them for this (design, device); otherwise a generic paragraph built from the
    backend record, with the device numbers the planner needs to reason about budgets. Artix-7 shares the
    Spartan-7 fabric (LUT6, DSP48E1, -1 speed grade), so its measured facts apply with a capacity caveat."""
    from .backends import BACKENDS
    b = BACKENDS[backend]
    facts = prompts_for(kind)["facts"]
    text = facts.get(backend)
    if not text and backend == "xc7a35t" and facts.get("xc7s50"):
        text = (f"{b.name}: same LUT6 / DSP48E1 fabric and speed grade as the Spartan-7 below but smaller "
                f"({b.lut} LUT6, {b.dsp} DSP48E1, {b.bram} BRAM). Measured Spartan-7 facts:\n" + facts["xc7s50"])
    if text:
        return text
    style = "asic" if b.vendor == "skywater" else b.emit_style
    core = GENERIC_FACTS.get(style, GENERIC_FACTS["behavioral"]).format(dsp_kind=b.dsp_kind, dsp_width=b.dsp_width, lut_kind=b.lut_kind)
    if style == "asic":
        return f"{b.name} ({b.toolchain}). Realistic Fmax range: {b.fmax_range}. No measured runs of this design on this process yet.\n{core}"
    return (f"{b.name} ({b.toolchain}): {b.lut} {b.lut_kind}, {b.dsp} {b.dsp_kind}, {b.bram} BRAM. Realistic Fmax range: {b.fmax_range}. "
            f"No measured runs of this design on this device yet -- treat the following as structure, not numbers.\n{core}")
