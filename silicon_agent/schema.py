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
        "locked_candidates": ["taps", "data_width", "coef_width", "cutoff"],
        "fields": [
            {"name": "taps", "type": "int", "min": 2, "max": 256, "hint": "filter length (task parameter)"},
            {"name": "data_width", "type": "choice", "choices": [8, 12, 16, 18], "hint": "sample bits"},
            {"name": "coef_width", "type": "choice", "choices": [8, 12, 16, 18], "hint": "coefficient bits"},
            {"name": "cutoff", "type": "float", "min": 0.01, "max": 0.49, "hint": "lowpass cutoff as a fraction of the sample rate (coefficients are generated from it)"},
            {"name": "acc_width", "type": "int", "min": 16, "max": 64, "hint": "accumulator bits; min = dw+cw+log2(taps)(+1 if symmetric)"},
            {"name": "symmetric", "type": "bool", "hint": "share one multiplier per h[k]==h[N-1-k] pair"},
            {"name": "fold", "type": "int", "min": 1, "max": 32, "hint": "taps per multiplier; 1 = transposed, >1 = time-multiplexed (must divide unique taps)"},
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
}

OBJECTIVES = ["maximize_throughput", "minimize_lut", "minimize_dsp", "maximize_fmax", "minimize_latency"]
