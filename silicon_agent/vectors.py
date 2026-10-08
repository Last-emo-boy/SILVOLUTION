"""Vector-based testbenches for netlist simulation (xsim).

cocotb drives the behavioral RTL under iverilog. The post-synthesis and
post-implementation netlists (functional and SDF-annotated timing) are
simulated with Vivado's xsim, which cocotb cannot attach to in this build.
So each design also provides:

  vectors(ir, seed) -> (stim_lines, expected_lines)   same golden model as the cocotb test
  TB_<design>                                         a SystemVerilog testbench that replays
                                                      stim.hex, checks exp.hex, prints RESULT

Stimulus is driven on the falling edge and outputs are sampled 3/4 period after
the rising edge, so the same bench works with SDF delays at any clock period.
Netlist sims start with glbl.GSR asserted for 100 ns, so reset is held longer.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

TB_DIR = Path(__file__).resolve().parent / "tb"
sys.path.insert(0, str(TB_DIR))

from test_conv import golden as conv_golden, make_frames  # noqa: E402
from test_fir import golden as fir_golden  # noqa: E402
from test_matmul import golden as mm_golden, pack, to_signed  # noqa: E402


# ---------------------------------------------------------------- matmul
def matmul_vectors(ir, seed: int = 1, tiles: int = 4) -> tuple[list[str], list[str]]:
    from .emit import min_k_full_rate
    R, C, W, AW = ir.pe_rows, ir.pe_cols, ir.data_width, ir.acc_width
    rng = random.Random(seed)
    lo, hi = -(1 << (W - 1)), (1 << (W - 1)) - 1
    min_k = min_k_full_rate(ir)
    stim, exp = [], []
    for t in range(tiles):
        K = [min_k, min_k + 3, 2 * min_k, min_k][t % 4]
        A = [[rng.randint(lo, hi) for _ in range(K)] for _ in range(R)]
        B = [[rng.randint(lo, hi) for _ in range(C)] for _ in range(K)]
        if t == 0:
            A = [[lo] * K for _ in range(R)]
            B = [[lo] * C for _ in range(K)]
        for k in range(K):
            last = 1 if k == K - 1 else 0
            stim.append(f"{2 | last:x} {pack([A[i][k] for i in range(R)], W):0{max(1, R * W // 4)}x} {pack([B[k][j] for j in range(C)], W):0{max(1, C * W // 4)}x}")
        if t % 2 == 1:
            stim.extend(["0 0 0"] * 3)
        for row in mm_golden(A, B):
            v = pack([to_signed(x, AW) for x in row], AW)
            exp.append(f"{v & ((1 << C * AW) - 1):0{max(1, C * AW // 4)}x}")
    return stim, exp


TB_MATMUL = """`timescale 1ns/1ps
module tb;
  parameter int R = {R}, C = {C}, W = {W}, AW = {AW};
  parameter string STIM = "stim.hex", EXP = "exp.hex";
  parameter real PERIOD = {period};
  logic clk = 0, rst = 1, in_valid = 0, in_last = 0;
  logic [R*W-1:0] a_col = 0; logic [C*W-1:0] b_row = 0;
  wire busy, out_valid; wire [$clog2(R+1)-1:0] out_row; wire [C*AW-1:0] c_row;
  matmul_top dut(.clk, .rst, .in_valid, .in_last, .a_col, .b_row, .busy, .out_valid, .out_row, .c_row);
  always #(PERIOD/2) clk = ~clk;
  int fd, n_exp, n_got, errors, code; logic [3:0] flags; logic [R*W-1:0] av; logic [C*W-1:0] bv;
  logic [C*AW-1:0] expv [0:8191];
  initial begin
    fd = $fopen(EXP, "r"); n_exp = 0;
    while (!$feof(fd)) begin code = $fscanf(fd, "%h\\n", expv[n_exp]); if (code == 1) n_exp++; end
    $fclose(fd);
  end
  always @(posedge clk) begin #(PERIOD*0.75); if (out_valid && !rst) begin
    if (c_row !== expv[n_got]) begin errors++; if (errors < 5) $display("MISMATCH row %0d got %h exp %h at %t", n_got, c_row, expv[n_got], $time); end
    n_got++;
  end end
  initial begin
    n_got = 0; errors = 0;
    repeat (3) @(posedge clk); #120; @(negedge clk); rst = 0;
    fd = $fopen(STIM, "r");
    while (!$feof(fd)) begin
      code = $fscanf(fd, "%h %h %h\\n", flags, av, bv);
      if (code != 3) break;
      @(negedge clk);
      in_valid = flags[1]; in_last = flags[0]; a_col = av; b_row = bv;
    end
    @(negedge clk); in_valid = 0; in_last = 0;
    repeat (200) @(posedge clk);
    if (n_got != n_exp) begin errors++; $display("COUNT got %0d expected %0d", n_got, n_exp); end
    $display("RESULT %s rows=%0d errors=%0d", errors ? "FAIL" : "PASS", n_got, errors);
    $finish;
  end
endmodule
"""


# ---------------------------------------------------------------- fir
def fir_vectors(ir, seed: int = 1, n: int = 120) -> tuple[list[str], list[str]]:
    DW, AW, h = ir.data_width, ir.acc_width, list(ir.coefs)
    rng = random.Random(seed)
    lo, hi = -(1 << (DW - 1)), (1 << (DW - 1)) - 1
    xs = [hi] * len(h) + [lo] * len(h) + [rng.randint(lo, hi) for _ in range(n)]
    ys = fir_golden(xs, h)
    gaps = [0 if i >= len(xs) - n // 2 else rng.choice([0, 0, 1, 3]) for i in range(len(xs))]
    stim = [f"{g:x} {x & ((1 << DW) - 1):0{max(1, DW // 4)}x}" for g, x in zip(gaps, xs)]
    exp = [f"{y & ((1 << AW) - 1):0{max(1, (AW + 3) // 4)}x}" for y in ys]
    return stim, exp


TB_FIR = """`timescale 1ns/1ps
module tb;
  parameter int DW = {DW}, AW = {AW};
  parameter string STIM = "stim.hex", EXP = "exp.hex";
  parameter real PERIOD = {period};
  logic clk = 0, rst = 1, in_valid = 0;
  logic [DW-1:0] in_data = 0;
  wire in_ready, out_valid; wire [AW-1:0] out_data;
  fir_top dut(.clk, .rst, .in_valid, .in_data, .in_ready, .out_valid, .out_data);
  always #(PERIOD/2) clk = ~clk;
  int fd, n_exp, n_got, errors, code, gap; logic [DW-1:0] xv; logic rdy;
  logic [AW-1:0] expv [0:8191];
  initial begin
    fd = $fopen(EXP, "r"); n_exp = 0;
    while (!$feof(fd)) begin code = $fscanf(fd, "%h\\n", expv[n_exp]); if (code == 1) n_exp++; end
    $fclose(fd);
  end
  always @(posedge clk) begin #(PERIOD*0.75); if (out_valid && !rst) begin
    if (out_data !== expv[n_got]) begin errors++; if (errors < 5) $display("MISMATCH sample %0d got %h exp %h at %t", n_got, out_data, expv[n_got], $time); end
    n_got++;
  end end
  initial begin
    n_got = 0; errors = 0;
    repeat (3) @(posedge clk); #120; @(negedge clk); rst = 0;
    fd = $fopen(STIM, "r");
    while (!$feof(fd)) begin
      code = $fscanf(fd, "%h %h\\n", gap, xv);
      if (code != 2) break;
      if (gap) begin in_valid = 0; repeat (gap) @(negedge clk); end
      @(negedge clk); in_valid = 1; in_data = xv;
      // hold until the DUT accepts: in_ready is a function of state only, so its value
      // just before a rising edge is what that edge samples
      forever begin #(PERIOD*0.45); rdy = in_ready; @(posedge clk); if (rdy) break; @(negedge clk); end
    end
    in_valid = 0;
    repeat (300) @(posedge clk);
    if (n_got != n_exp) begin errors++; $display("COUNT got %0d expected %0d", n_got, n_exp); end
    $display("RESULT %s samples=%0d errors=%0d", errors ? "FAIL" : "PASS", n_got, errors);
    $finish;
  end
endmodule
"""


# ---------------------------------------------------------------- conv2d
def conv_vectors(ir, seed: int = 1, n_rows: int | None = None) -> tuple[list[str], list[str]]:
    K, W, DW, AW = ir.k, ir.width, ir.data_width, ir.acc_width
    rng = random.Random(seed)
    frames = make_frames(rng, K, W, DW, n_rows or K + 3, 2)
    ys = conv_golden(frames, K, W, list(ir.coefs))
    stim = []
    for px in frames:
        for i, x in enumerate(px):
            gap = rng.choice([0, 0, 0, 1, 2]) if i % 7 == 0 else 0
            stim.append(f"{gap:x} {1 if i == 0 else 0:x} {x & ((1 << DW) - 1):0{max(1, DW // 4)}x}")
    exp = [f"{y & ((1 << AW) - 1):0{max(1, (AW + 3) // 4)}x}" for y in ys]
    return stim, exp


TB_CONV = """`timescale 1ns/1ps
module tb;
  parameter int DW = {DW}, AW = {AW};
  parameter string STIM = "stim.hex", EXP = "exp.hex";
  parameter real PERIOD = {period};
  logic clk = 0, rst = 1, in_valid = 0, frame_start = 0;
  logic [DW-1:0] in_data = 0;
  wire in_ready, out_valid; wire [AW-1:0] out_data;
  conv2d_top dut(.clk, .rst, .in_valid, .frame_start, .in_data, .in_ready, .out_valid, .out_data);
  always #(PERIOD/2) clk = ~clk;
  int fd, n_exp, n_got, errors, code, gap, fs; logic [DW-1:0] xv; logic rdy;
  logic [AW-1:0] expv [0:65535];
  initial begin
    fd = $fopen(EXP, "r"); n_exp = 0;
    while (!$feof(fd)) begin code = $fscanf(fd, "%h\\n", expv[n_exp]); if (code == 1) n_exp++; end
    $fclose(fd);
  end
  always @(posedge clk) begin #(PERIOD*0.75); if (out_valid && !rst) begin
    if (out_data !== expv[n_got]) begin errors++; if (errors < 5) $display("MISMATCH sample %0d got %h exp %h at %t", n_got, out_data, expv[n_got], $time); end
    n_got++;
  end end
  initial begin
    n_got = 0; errors = 0;
    repeat (3) @(posedge clk); #120; @(negedge clk); rst = 0;
    fd = $fopen(STIM, "r");
    while (!$feof(fd)) begin
      code = $fscanf(fd, "%h %h %h\\n", gap, fs, xv);
      if (code != 3) break;
      if (gap) begin in_valid = 0; repeat (gap) @(negedge clk); end
      @(negedge clk); in_valid = 1; frame_start = fs[0]; in_data = xv;
      forever begin #(PERIOD*0.45); rdy = in_ready; @(posedge clk); if (rdy) break; @(negedge clk); end
    end
    in_valid = 0; frame_start = 0;
    repeat (300) @(posedge clk);
    if (n_got != n_exp) begin errors++; $display("COUNT got %0d expected %0d", n_got, n_exp); end
    $display("RESULT %s samples=%0d errors=%0d", errors ? "FAIL" : "PASS", n_got, errors);
    $finish;
  end
endmodule
"""
