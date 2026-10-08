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
        "title": "datapath graph: chain of fir / conv2d / cordic / fft nodes with FIFOs",
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

OBJECTIVES = ["maximize_throughput", "minimize_lut", "minimize_dsp", "maximize_fmax", "minimize_latency", "minimize_power"]
