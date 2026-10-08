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

Structure summary: multipliers = ceil(U / fold); throughput = 1/fold samples per cycle; the transposed form's adders
are all local (mult -> add -> register), so its Fmax does not degrade with N; the folded form adds a tap-operand
mux (U/F : 1) and a coefficient ROM in front of each multiplier, which costs LUTs and routing per fold step."""

FIR_IR_JSON = '{"symmetric":..,"fold":..,"acc_width":..,"pipe_mul":..,"reg_in":..,"dsp_regs":"..","acc_in_dsp":..}'

FIR_FACTS = {
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
}


GRAPH_WHAT = "a datapath: a chain of 2-6 heterogeneous stream nodes (fir, conv2d, cordic, fft) connected by valid/ready FIFOs"

GRAPH_IR = """IR: {"nodes": [{"kind": "fir"|"conv2d"|"cordic"|"fft", "ir": <that design's IR>}, ...], "fifo_depth": 4..64, "adapt": "truncate"|"round"|"saturate"}
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
}


def device_facts(kind: str, backend: str) -> str:
    """Measured facts when we have them for this (design, device); otherwise a generic paragraph built from the
    backend record, with the device numbers the planner needs to reason about budgets. Artix-7 shares the
    Spartan-7 fabric (LUT6, DSP48E1, -1 speed grade), so its measured facts apply with a capacity caveat."""
    from .backends import BACKENDS
    b = BACKENDS[backend]
    text = PROMPTS[kind]["facts"].get(backend)
    if not text and backend == "xc7a35t" and PROMPTS[kind]["facts"].get("xc7s50"):
        text = (f"{b.name}: same LUT6 / DSP48E1 fabric and speed grade as the Spartan-7 below but smaller "
                f"({b.lut} LUT6, {b.dsp} DSP48E1, {b.bram} BRAM). Measured Spartan-7 facts:\n" + PROMPTS[kind]["facts"]["xc7s50"])
    if text:
        return text
    core = GENERIC_FACTS.get(b.emit_style, GENERIC_FACTS["behavioral"]).format(dsp_kind=b.dsp_kind, dsp_width=b.dsp_width, lut_kind=b.lut_kind)
    return (f"{b.name} ({b.toolchain}): {b.lut} {b.lut_kind}, {b.dsp} {b.dsp_kind}, {b.bram} BRAM. Realistic Fmax range: {b.fmax_range}. "
            f"No measured runs of this design on this device yet -- treat the following as structure, not numbers.\n{core}")
