"""IR field schemas and starting-design presets for the web form.

The IR dataclasses own validation; this is the *presentation* of the same rules:
type, range/choices, a one-line hint, and which fields each backend ignores.
"""

from __future__ import annotations

from .fir import lowpass_taps

SHARED = [
    {"name": "pipe_mul", "type": "int", "min": 0, "max": 3, "hint": "fabric register stages after the multiplier"},
    {"name": "reg_in", "type": "bool", "hint": "register the inputs at the boundary"},
    {"name": "dsp_regs", "type": "choice", "choices": ["none", "out", "in_out", "in_pipe_out"], "hint": "DSP-internal pipeline registers"},
    {"name": "acc_in_dsp", "type": "bool", "hint": "accumulate inside the DSP post-adder (Vivado devices)", "backends": ["xc7s50"]},
]

SCHEMAS = {
    "matmul": {
        "title": "systolic INT matrix-multiply",
        "locked_candidates": ["data_width"],
        "fields": [
            {"name": "pe_rows", "type": "int", "min": 1, "max": 32, "hint": "PE rows; throughput = rows x cols MAC/cycle"},
            {"name": "pe_cols", "type": "int", "min": 1, "max": 32, "hint": "PE columns"},
            {"name": "data_width", "type": "choice", "choices": [4, 8, 12, 16], "hint": "operand bits (usually locked by the task)"},
            {"name": "acc_width", "type": "int", "min": 16, "max": 48, "hint": "accumulator bits; min = 2*data_width+8"},
            *SHARED,
        ],
        "presets": {
            "naive (unpipelined)": {"pe_rows": 8, "pe_cols": 8, "data_width": 8, "acc_width": 32, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none", "acc_in_dsp": True},
            "balanced": {"pe_rows": 4, "pe_cols": 8, "data_width": 8, "acc_width": 24, "pipe_mul": 0, "reg_in": True, "dsp_regs": "in_pipe_out", "acc_in_dsp": False},
        },
        "constraints_default": {"fmax_mhz": 200, "max_dsp": 64, "max_lut": 8000},
        "locked_default": {"data_width": 8},
    },
    "fir": {
        "title": "fixed-coefficient FIR filter",
        "locked_candidates": ["taps", "data_width", "coef_width", "cutoff", "coef_port"],
        "fields": [
            {"name": "taps", "type": "int", "min": 2, "max": 256, "hint": "filter length (task parameter)"},
            {"name": "data_width", "type": "choice", "choices": [8, 12, 16, 18], "hint": "sample bits"},
            {"name": "coef_width", "type": "choice", "choices": [8, 12, 16, 18], "hint": "coefficient bits"},
            {"name": "cutoff", "type": "float", "min": 0.01, "max": 0.49, "hint": "lowpass cutoff as a fraction of the sample rate (coefficients are generated from it)"},
            {"name": "acc_width", "type": "int", "min": 16, "max": 64, "hint": "accumulator bits; min = dw+cw+log2(taps)(+1 if symmetric)"},
            {"name": "symmetric", "type": "bool", "hint": "share one multiplier per h[k]==h[N-1-k] pair"},
            {"name": "fold", "type": "int", "min": 1, "max": 32, "hint": "taps per multiplier; 1 = transposed, >1 = time-multiplexed (must divide unique taps)"},
            {"name": "coef_port", "type": "bool", "hint": "coefficients writable at runtime (coef_we/addr/data port; AXI-Lite in the wrapper)"},
            {"name": "sum_tree", "type": "bool", "hint": "fold>1: registered balanced reduction of the partial sums (+log2(multipliers) cycles) instead of one combinational adder chain"},
            {"name": "pipe_in", "type": "bool", "hint": "fold>1: register the multiplier operands after the pre-adder and tap mux (+1 cycle)"},
            *SHARED,
        ],
        "presets": {
            "naive (transposed)": {"taps": 32, "data_width": 16, "coef_width": 16, "acc_width": 40, "symmetric": False, "fold": 1, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none", "acc_in_dsp": True},
            "folded x4 symmetric": {"taps": 32, "data_width": 16, "coef_width": 16, "acc_width": 38, "symmetric": True, "fold": 4, "pipe_mul": 0, "reg_in": True, "dsp_regs": "in_pipe_out", "acc_in_dsp": True},
        },
        "constraints_default": {"fmax_mhz": 150, "max_dsp": 8, "max_lut": 3000},
        "locked_default": {"taps": 32, "data_width": 16, "coef_width": 16, "cutoff": 0.2},
    },
    "conv2d": {
        "title": "streaming 2-D convolution (line buffers + window + adder tree)",
        "locked_candidates": ["k", "width", "data_width", "coef_width"],
        "fields": [
            {"name": "k", "type": "choice", "choices": [3, 5, 7], "hint": "kernel size (task parameter)"},
            {"name": "width", "type": "int", "min": 16, "max": 2048, "hint": "image width in pixels (line-buffer depth)"},
            {"name": "data_width", "type": "choice", "choices": [8, 12, 16], "hint": "pixel bits"},
            {"name": "coef_width", "type": "choice", "choices": [8, 12, 16], "hint": "kernel coefficient bits"},
            {"name": "acc_width", "type": "int", "min": 16, "max": 48, "hint": "adder-tree/output bits; min = dw+cw+log2(k*k)+1"},
            {"name": "line_mem", "type": "choice", "choices": ["bram", "lut"], "hint": "line buffers in block RAM or LUT shift registers"},
            {"name": "par_rows", "type": "int", "min": 1, "max": 7, "hint": "window rows multiplied per cycle (divides k); throughput = par_rows/k px/cycle"},
            {"name": "tree_regs", "type": "int", "min": 0, "max": 3, "hint": "register levels inside the adder tree"},
            {"name": "pipe_mul", "type": "int", "min": 0, "max": 3, "hint": "fabric register stages after the multipliers"},
            {"name": "dsp_regs", "type": "choice", "choices": ["none", "out", "in_out", "in_pipe_out"], "hint": "multiplier register stages"},
        ],
        "presets": {
            "naive (parallel)": {"k": 3, "width": 640, "data_width": 8, "coef_width": 8, "acc_width": 24, "line_mem": "bram", "par_rows": 3, "tree_regs": 0, "pipe_mul": 0, "dsp_regs": "none"},
            "row-serial": {"k": 3, "width": 640, "data_width": 8, "coef_width": 8, "acc_width": 24, "line_mem": "bram", "par_rows": 1, "tree_regs": 1, "pipe_mul": 1, "dsp_regs": "out"},
        },
        "constraints_default": {"fmax_mhz": 100, "max_dsp": 16, "max_lut": 2000},
        "locked_default": {"k": 3, "width": 640, "data_width": 8, "coef_width": 8},
    },
    "decim": {
        "title": "decimating FIR (polyphase rate change by M: one output per M inputs, N/M multipliers at full input rate)",
        "locked_candidates": ["taps", "decim", "data_width", "coef_width", "cutoff"],
        "fields": [
            {"name": "taps", "type": "int", "min": 2, "max": 256, "hint": "filter length, a multiple of decim (task parameter)"},
            {"name": "decim", "type": "int", "min": 2, "max": 16, "hint": "rate change M: one output per M input samples (task parameter)"},
            {"name": "data_width", "type": "choice", "choices": [8, 12, 16, 18], "hint": "sample bits"},
            {"name": "coef_width", "type": "choice", "choices": [8, 12, 16, 18], "hint": "coefficient bits"},
            {"name": "cutoff", "type": "float", "min": 0.005, "max": 0.49, "hint": "lowpass cutoff as a fraction of the INPUT sample rate (anti-alias: <= 0.5/decim)"},
            {"name": "acc_width", "type": "int", "min": 16, "max": 64, "hint": "accumulator bits; min = dw+cw+log2(taps)"},
            {"name": "fold", "type": "int", "min": 1, "max": 8, "hint": "cycles per accepted input sample (throughput 1/fold inputs/cycle); multipliers = ceil(taps / (decim*fold))"},
            {"name": "sum_tree", "type": "bool", "hint": "register the K-way reduction of a sweep step (+1 cycle)"},
            *SHARED,
        ],
        "presets": {
            "full rate x4": {"taps": 32, "decim": 4, "data_width": 16, "coef_width": 16, "acc_width": 40, "fold": 1, "pipe_mul": 1, "reg_in": True, "dsp_regs": "in_pipe_out", "acc_in_dsp": True, "sum_tree": False},
            "half rate x4": {"taps": 32, "decim": 4, "data_width": 16, "coef_width": 16, "acc_width": 40, "fold": 2, "pipe_mul": 1, "reg_in": True, "dsp_regs": "in_pipe_out", "acc_in_dsp": True, "sum_tree": True},
        },
        "constraints_default": {"fmax_mhz": 150, "max_dsp": 8, "max_lut": 3000},
        "locked_default": {"taps": 32, "decim": 4, "data_width": 16, "coef_width": 16, "cutoff": 0.1},
    },
    "nco": {
        "title": "NCO / DDS with complex mixer (phase accumulator + quarter-wave sine table + I/Q multiply: a digital down-converter front end)",
        "locked_candidates": ["data_width", "phase_width", "sin_width", "out_width"],
        "fields": [
            {"name": "data_width", "type": "choice", "choices": [8, 12, 16, 18], "hint": "input sample bits"},
            {"name": "phase_width", "type": "int", "min": 16, "max": 48, "hint": "phase accumulator bits (frequency resolution fs/2^phase_width)"},
            {"name": "sin_width", "type": "choice", "choices": [8, 10, 12, 14, 16, 18], "hint": "sine sample bits (~6 dB spur floor per bit)"},
            {"name": "out_width", "type": "int", "min": 8, "max": 36, "hint": "I/Q output bits (top bits of the product)"},
            {"name": "lut_addr_width", "type": "int", "min": 6, "max": 16, "hint": "bits of phase addressing the quarter-wave table (depth 2^(w-2)); phase-truncation spurs ~ -6*w dBc"},
            {"name": "dither", "type": "bool", "hint": "1-bit LFSR phase dither below the table address (spreads truncation spurs)"},
            {"name": "table_mem", "type": "choice", "choices": ["bram", "lut"], "hint": "sine table in block RAM or logic"},
            {"name": "pipe_lut", "type": "int", "min": 0, "max": 2, "hint": "register stages after the table read"},
            {"name": "pipe_mix", "type": "int", "min": 0, "max": 3, "hint": "fabric register stages after the mixer multipliers"},
            {"name": "reg_in", "type": "bool", "hint": "register the input sample"},
            {"name": "dsp_regs", "type": "choice", "choices": ["none", "out", "in_out", "in_pipe_out"], "hint": "DSP-internal registers of the mixer multipliers"},
        ],
        "presets": {
            "bram table": {"data_width": 16, "phase_width": 32, "sin_width": 16, "out_width": 16, "lut_addr_width": 12, "dither": False, "table_mem": "bram", "pipe_lut": 1, "pipe_mix": 1, "reg_in": True, "dsp_regs": "in_pipe_out"},
            "small lut table": {"data_width": 12, "phase_width": 24, "sin_width": 12, "out_width": 12, "lut_addr_width": 8, "dither": True, "table_mem": "lut", "pipe_lut": 0, "pipe_mix": 1, "reg_in": True, "dsp_regs": "none"},
        },
        "constraints_default": {"fmax_mhz": 150, "max_dsp": 2, "max_lut": 1000},
        "locked_default": {"data_width": 16, "phase_width": 32, "sin_width": 16, "out_width": 16},
    },
    "iir": {
        "title": "IIR lowpass as a cascade of biquads (Butterworth from cutoff + sections; transposed direct form II, bit-exact fixed point)",
        "locked_candidates": ["sections", "data_width", "coef_width", "cutoff"],
        "fields": [
            {"name": "sections", "type": "int", "min": 1, "max": 8, "hint": "biquads (filter order / 2)"},
            {"name": "data_width", "type": "choice", "choices": [8, 12, 16, 18, 24], "hint": "sample bits (in and out)"},
            {"name": "coef_width", "type": "choice", "choices": [12, 16, 18, 24], "hint": "coefficient bits"},
            {"name": "cutoff", "type": "float", "min": 0.01, "max": 0.45, "hint": "-3 dB point as a fraction of the sample rate"},
            {"name": "coef_frac", "type": "int", "min": 4, "max": 22, "hint": "fractional bits of the coefficients (<= coef_width-2); pole precision -- sharp lowpass needs more"},
            {"name": "state_width", "type": "int", "min": 10, "max": 48, "hint": "section state bits (>= data_width+2): headroom and rounding noise"},
            {"name": "pipe_mul", "type": "int", "min": 0, "max": 1, "hint": "register after the feed-forward multipliers (the recursive path cannot be pipelined)"},
            {"name": "reg_in", "type": "bool", "hint": "register the input"},
            {"name": "dsp_regs", "type": "choice", "choices": ["none", "out", "in_out", "in_pipe_out"], "hint": "DSP-internal registers of the feed-forward multipliers"},
        ],
        "presets": {
            "4th order": {"sections": 2, "data_width": 16, "coef_width": 18, "cutoff": 0.1, "coef_frac": 15, "state_width": 24, "pipe_mul": 0, "reg_in": True, "dsp_regs": "none"},
            "2nd order compact": {"sections": 1, "data_width": 12, "coef_width": 16, "cutoff": 0.1, "coef_frac": 14, "state_width": 18, "pipe_mul": 0, "reg_in": True, "dsp_regs": "none"},
        },
        "constraints_default": {"fmax_mhz": 50, "max_dsp": 10, "max_lut": 2000},
        "locked_default": {"sections": 2, "data_width": 16, "coef_width": 18, "cutoff": 0.1},
    },
    "cordic": {
        "title": "CORDIC sin/cos (rotate) or magnitude/atan2 (vector)",
        "locked_candidates": ["mode", "width"],
        "fields": [
            {"name": "mode", "type": "choice", "choices": ["rotate", "vector"], "hint": "rotate: sin/cos; vector: magnitude/atan2"},
            {"name": "width", "type": "int", "min": 8, "max": 32, "hint": "data path bits"},
            {"name": "iterations", "type": "int", "min": 4, "max": 34, "hint": "micro-rotations (~ output precision bits)"},
            {"name": "unroll", "type": "int", "min": 1, "max": 34, "hint": "micro-rotations per pipeline register (divides iterations)"},
            {"name": "fold", "type": "int", "min": 1, "max": 34, "hint": "1 = pipelined; = iterations: one iterative engine"},
            {"name": "guard_bits", "type": "int", "min": 0, "max": 4, "hint": "extra LSBs through the rotation"},
            {"name": "scale_comp", "type": "bool", "hint": "pre-scale by 1/K (one multiplier) for unity gain"},
        ],
        "presets": {"pipelined": {"mode": "rotate", "width": 16, "iterations": 16, "unroll": 1, "fold": 1, "guard_bits": 2, "scale_comp": False},
                    "iterative": {"mode": "rotate", "width": 16, "iterations": 16, "unroll": 1, "fold": 16, "guard_bits": 2, "scale_comp": False}},
        "constraints_default": {"fmax_mhz": 150, "max_dsp": 2, "max_lut": 3000},
        "locked_default": {"mode": "rotate", "width": 16},
    },
    "cic": {
        "title": "CIC (Hogenauer) decimating filter: N integrators -> decimate by R -> N combs, signed fixed point, no multipliers",
        "locked_candidates": ["data_width", "stages", "decim", "out_width"],
        "fields": [
            {"name": "data_width", "type": "int", "min": 2, "max": 32, "hint": "input sample bits (task parameter)"},
            {"name": "stages", "type": "int", "min": 1, "max": 5, "hint": "integrator/comb stages N (task parameter)"},
            {"name": "decim", "type": "int", "min": 2, "max": 64, "hint": "rate change R: one output per R inputs (task parameter)"},
            {"name": "out_width", "type": "int", "min": 1, "max": 32, "hint": "output bits = top bits of the B-bit register (task parameter)"},
            {"name": "pipe_integ", "type": "bool", "hint": "register between the integrator stages (default true): one B-bit adder per cycle on the critical path"},
            {"name": "pipe_comb", "type": "bool", "hint": "register between the comb stages: valid shift chain, +N+2 cycles latency"},
            {"name": "reg_in", "type": "bool", "hint": "register the input sample (+1 cycle)"},
        ],
        "presets": {
            "combinational comb": {"data_width": 16, "stages": 3, "decim": 8, "out_width": 16, "pipe_comb": False, "reg_in": False, "pipe_integ": True},
            "pipelined comb": {"data_width": 16, "stages": 3, "decim": 8, "out_width": 16, "pipe_comb": True, "reg_in": True, "pipe_integ": True},
        },
        "constraints_default": {"fmax_mhz": 150, "max_lut": 3000},
        "locked_default": {"data_width": 16, "stages": 3, "decim": 8, "out_width": 16},
    },
    "fft": {
        "title": "streaming N-point FFT (radix-2 SDF pipeline)",
        "locked_candidates": ["n", "data_width"],
        "fields": [
            {"name": "n", "type": "choice", "choices": [16, 32, 64, 128, 256, 512, 1024, 2048, 4096], "hint": "transform length"},
            {"name": "data_width", "type": "int", "min": 8, "max": 24, "hint": "input bits (real and imaginary)"},
            {"name": "tw_width", "type": "int", "min": 8, "max": 18, "hint": "twiddle bits"},
            {"name": "growth", "type": "choice", "choices": ["scale", "grow"], "hint": "scale: /2 per stage; grow: +1 bit per stage"},
            {"name": "tw_mem", "type": "choice", "choices": ["lut", "bram"], "hint": "twiddle ROM placement"},
            {"name": "delay_mem", "type": "choice", "choices": ["auto", "lut", "bram"], "hint": "delay lines in LUT shift registers or BRAM"},
            {"name": "bram_min", "type": "int", "min": 16, "max": 512, "hint": "auto: lines at least this deep use BRAM"},
            {"name": "mul_regs", "type": "int", "min": 0, "max": 3, "hint": "registers after each complex multiplier"},
            {"name": "bf_regs", "type": "int", "min": 0, "max": 1, "hint": "register the butterfly output"},
            {"name": "dsp_regs", "type": "choice", "choices": ["none", "out", "in_out", "in_pipe_out"], "hint": "multiplier register stages"},
            {"name": "bit_reverse", "type": "bool", "hint": "natural-order output (adds an N-sample buffer)"},
            {"name": "cmul", "type": "choice", "choices": ["four", "three", "lut"], "hint": "complex multiplier: 4 DSPs, 3 DSPs (Gauss) or LUT-only"},
        ],
        "presets": {"naive": {"n": 256, "data_width": 16, "tw_width": 16, "growth": "scale", "tw_mem": "lut", "delay_mem": "auto", "bram_min": 64, "mul_regs": 0, "bf_regs": 1, "dsp_regs": "none", "bit_reverse": False}},
        "constraints_default": {"fmax_mhz": 100, "max_dsp": 40, "max_lut": 6000},
        "locked_default": {"n": 256, "data_width": 16},
    },
    "graph": {
        "title": "datapath graph: chain of fir / decim / nco / iir / conv2d / cordic / fft nodes with FIFOs",
        "locked_candidates": ["nodes"],
        "nested": "nodes",
        "fields": [
            {"name": "fifo_depth", "type": "choice", "choices": [4, 8, 16, 32, 64], "hint": "inter-node FIFO depth (must exceed upstream latency + 2)"},
            {"name": "adapt", "type": "choice", "choices": ["truncate", "round", "saturate"], "hint": "how a node's output word is fitted into the next node's input width"},
        ],
        "presets": {"fir -> cordic": {"nodes": [
            {"kind": "fir", "ir": {"taps": 8, "data_width": 12, "coef_width": 12, "cutoff": 0.4, "acc_width": 28, "symmetric": False, "fold": 1, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}},
            {"kind": "cordic", "ir": {"mode": "rotate", "width": 12, "iterations": 12, "unroll": 1, "fold": 1, "guard_bits": 2, "scale_comp": False}}],
            "fifo_depth": 32, "adapt": "truncate"}},
        "constraints_default": {"fmax_mhz": 100, "max_dsp": 16, "max_lut": 4000},
        "locked_default": {},
    },
    "fir_chain": {
        "title": "cascade of FIR stages with FIFOs between them (composite datapath)",
        "locked_candidates": ["stages"],
        "nested": "stages",   # the IR is a list of FIR IRs; the form edits it as JSON
        "fields": [
            {"name": "fifo_depth", "type": "choice", "choices": [4, 8, 16, 32, 64], "hint": "inter-stage FIFO depth"},
            {"name": "round_mode", "type": "choice", "choices": ["truncate", "round"], "hint": "how a stage's accumulator is reduced to the next stage's data width"},
        ],
        "presets": {
            "anti-alias + shaping (8 -> 32 taps)": {"stages": [
                {"taps": 8, "data_width": 16, "coef_width": 16, "cutoff": 0.4, "acc_width": 36, "symmetric": False, "fold": 1, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"},
                {"taps": 32, "data_width": 16, "coef_width": 16, "cutoff": 0.2, "acc_width": 40, "symmetric": False, "fold": 1, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}],
                "fifo_depth": 16, "round_mode": "truncate"},
        },
        "constraints_default": {"fmax_mhz": 100, "max_dsp": 16, "max_lut": 3000},
        "locked_default": {},
    },
}

OBJECTIVES = ["maximize_throughput", "minimize_lut", "minimize_dsp", "maximize_fmax", "minimize_latency", "minimize_power", "minimize_area"]
