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


PROMPTS = {
    "matmul": {"what": MATMUL_WHAT, "ir": MATMUL_IR, "ir_json": MATMUL_IR_JSON, "facts": MATMUL_FACTS},
    "fir": {"what": FIR_WHAT, "ir": FIR_IR, "ir_json": FIR_IR_JSON, "facts": FIR_FACTS},
}
