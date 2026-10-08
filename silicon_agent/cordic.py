"""CORDIC: fixed-point rotation / vectoring engine (sin/cos, atan2, magnitude) as a streaming pipeline.

Knobs:
  mode        str rotate|vector    rotate: (x,y,angle) -> (x cos - y sin, y cos + x sin): sin/cos from (K,0,theta).
                                   vector: (x,y) -> (magnitude*K, 0, atan2(y,x)).
  width       int 8..32            data path bits (x, y, z are all `width` bits, two's complement)
  iterations  int 4..32            CORDIC micro-rotations (precision ~ iterations bits)
  unroll      int                  stages computed per pipeline register: 1 = one micro-rotation per stage
                                   (iterations stages, 1 result/cycle); iterations = fully combinational (1 stage);
                                   must divide iterations
  fold        int 1|iterations     1 = pipelined (1 sample/cycle); iterations = one iterative engine
                                   (1 sample per `iterations` cycles, ~1/iterations the registers)
  guard_bits  int 0..4             extra LSBs carried through the rotation to limit rounding error
  scale_comp  bool                 pre-scale inputs by 1/K (0.6073) with a constant multiply so outputs are unity-gain

Interface: in_valid / in_ready / in_x / in_y / in_z  ->  out_valid / out_x / out_y / out_z. Angles are in units of
2^(width-1) = pi (so the z range [-2^(width-1), 2^(width-1)) is [-pi, pi)). The golden model mirrors the exact
integer arithmetic (shift-add with the same rounding), so simulation checks bit-exactness, not an error bound.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass


def atan_table(iterations: int, width: int) -> list[int]:
    """atan(2^-i) in angle units where pi = 2^(width-1)."""
    return [int(round(math.atan(2.0 ** -i) / math.pi * (1 << (width - 1)))) for i in range(iterations)]


def cordic_gain(iterations: int) -> float:
    k = 1.0
    for i in range(iterations):
        k *= math.sqrt(1 + 2.0 ** (-2 * i))
    return k


@dataclass(frozen=True)
class CordicArch:
    mode: str = "rotate"
    width: int = 16
    iterations: int = 16
    unroll: int = 1
    fold: int = 1
    guard_bits: int = 2
    scale_comp: bool = False

    def validate(self) -> list[str]:
        errs = []
        if self.mode not in ("rotate", "vector"):
            errs.append("mode must be rotate or vector")
        if not 8 <= self.width <= 32:
            errs.append("width must be in 8..32")
        if not 4 <= self.iterations <= 32:
            errs.append("iterations must be in 4..32")
        if self.iterations > self.width + 2:
            errs.append("iterations beyond width+2 add nothing")
        if self.unroll < 1 or self.iterations % self.unroll:
            errs.append("unroll must divide iterations")
        if self.fold not in (1, self.iterations):
            errs.append("fold must be 1 (pipelined) or equal to iterations (single iterative engine)")
        if self.fold > 1 and self.unroll != 1:
            errs.append("the iterative engine does one micro-rotation per cycle (unroll must be 1)")
        if not 0 <= self.guard_bits <= 4:
            errs.append("guard_bits must be in 0..4")
        return errs

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CordicArch":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def digest(self, backend: str = "ecp5") -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:12]


def throughput(a: CordicArch) -> float:
    return round(1.0 / a.fold, 4)


def latency_cycles(a: CordicArch) -> int:
    if a.fold > 1:
        return a.iterations + 2
    return a.iterations // a.unroll + 1 + (1 if a.scale_comp else 0)


# ---------------------------------------------------------------- golden (bit-exact)
def golden(a: CordicArch, xs: list[int], ys: list[int], zs: list[int]) -> list[tuple[int, int, int]]:
    W, G, N = a.width, a.guard_bits, a.iterations
    WG = W + G
    lo, hi = -(1 << (WG - 1)), (1 << (WG - 1)) - 1
    atans = atan_table(N, W)
    out = []
    for x0, y0, z0 in zip(xs, ys, zs):
        if a.scale_comp:
            k = int(round((1 << (W - 1)) / cordic_gain(N)))
            x0 = (x0 * k) >> (W - 1)
            y0 = (y0 * k) >> (W - 1)
        x, y, z = x0 << G, y0 << G, z0
        for i in range(N):
            d = (z >= 0) if a.mode == "rotate" else (y < 0)
            xs_, ys_ = x >> i, y >> i   # arithmetic shift (floor), same as >>> in RTL
            if d:
                x, y, z = x - ys_, y + xs_, z - atans[i]
            else:
                x, y, z = x + ys_, y - xs_, z + atans[i]
            x = max(lo, min(hi, x)); y = max(lo, min(hi, y))
            z = ((z + (1 << (W - 1))) % (1 << W)) - (1 << (W - 1))
        out.append((x >> G, y >> G, z))
    return out


# ---------------------------------------------------------------- emitter
def emit_cordic(a: CordicArch, backend: str = "ecp5") -> str:
    W, G, N, U = a.width, a.guard_bits, a.iterations, a.unroll
    WG = W + G
    atans = atan_table(N, W)
    at_lit = ", ".join(f"W'(sd'({v}))" for v in atans)
    dsel = "(z_s >= 0)" if a.mode == "rotate" else "(y_s < 0)"
    k = int(round((1 << (W - 1)) / cordic_gain(N)))
    prescale = (f"""    // 1/K pre-scale (K = {cordic_gain(N):.5f}) so rotate outputs are unity gain; the product is formed
    // at 2W bits (SystemVerilog sizes a*b to max width, so both operands are widened first)
    logic signed [W-1:0] xk, yk; logic vk; logic signed [W-1:0] zk;
    logic signed [2*W-1:0] xp, yp;
    assign xp = (2*W)'(in_x) * (2*W)'(sd'({k}));
    assign yp = (2*W)'(in_y) * (2*W)'(sd'({k}));
    always_ff @(posedge clk) begin
        xk <= W'(xp >>> (W-1));
        yk <= W'(yp >>> (W-1));
        zk <= in_z; vk <= in_valid & ~rst;
    end
""" if a.scale_comp else """    logic signed [W-1:0] xk, yk, zk; logic vk;
    assign xk = in_x; assign yk = in_y; assign zk = in_z; assign vk = in_valid & ~rst;
""")
    if a.fold == 1:
        S = N // U
        stages = []
        for s_ in range(S):
            steps = []
            for j in range(U):
                i = s_ * U + j
                steps.append(f"""            if ({dsel.replace("z_s", f"z{j}").replace("y_s", f"y{j}")}) begin x{j + 1} = x{j} - (y{j} >>> {i}); y{j + 1} = y{j} + (x{j} >>> {i}); z{j + 1} = z{j} - ATAN[{i}]; end
            else begin x{j + 1} = x{j} + (y{j} >>> {i}); y{j + 1} = y{j} - (x{j} >>> {i}); z{j + 1} = z{j} + ATAN[{i}]; end""")
            decl = ", ".join(f"x{j}, y{j}" for j in range(U + 1)); zdecl = ", ".join(f"z{j}" for j in range(U + 1))
            stages.append(f"""    always_comb begin : c{s_}
            logic signed [WG-1:0] {decl}; logic signed [W-1:0] {zdecl};
            x0 = x_q[{s_}]; y0 = y_q[{s_}]; z0 = z_q[{s_}];
{chr(10).join(steps)}
            x_c[{s_}] = x{U}; y_c[{s_}] = y{U}; z_c[{s_}] = z{U};
        end
    always_ff @(posedge clk) begin x_q[{s_ + 1}] <= x_c[{s_}]; y_q[{s_ + 1}] <= y_c[{s_}]; z_q[{s_ + 1}] <= z_c[{s_}]; v_q[{s_ + 1}] <= v_q[{s_}] & ~rst; end""")
        core = f"""    // ---- pipelined: {S} stage(s) of {U} micro-rotation(s)
    logic signed [WG-1:0] x_q [0:{S}], y_q [0:{S}], x_c [0:{S - 1}], y_c [0:{S - 1}];
    logic signed [W-1:0]  z_q [0:{S}], z_c [0:{S - 1}];
    logic v_q [0:{S}];
    always_ff @(posedge clk) begin x_q[0] <= WG'(xk) <<< G; y_q[0] <= WG'(yk) <<< G; z_q[0] <= zk; v_q[0] <= vk & ~rst; end
    initial for (int i = 0; i <= {S}; i++) v_q[i] = 0;
{chr(10).join(stages)}
    assign in_ready  = ~rst;
    assign out_valid = v_q[{S}] & ~rst;
    assign out_x = W'(x_q[{S}] >>> G); assign out_y = W'(y_q[{S}] >>> G); assign out_z = z_q[{S}];
"""
    else:
        core = f"""    // ---- iterative: one micro-rotation per cycle, {N} cycles per sample
    logic signed [WG-1:0] x_r, y_r; logic signed [W-1:0] z_r; logic [$clog2(N+1)-1:0] i_r; logic busy;
    assign in_ready = ~rst & ~busy;
    logic signed [WG-1:0] x_s, y_s, x_n, y_n; logic signed [W-1:0] z_s, z_n;
    always_comb begin
        x_s = x_r; y_s = y_r; z_s = z_r;
        if ({dsel}) begin x_n = x_s - (y_s >>> i_r); y_n = y_s + (x_s >>> i_r); z_n = z_s - ATAN[i_r]; end
        else           begin x_n = x_s + (y_s >>> i_r); y_n = y_s - (x_s >>> i_r); z_n = z_s + ATAN[i_r]; end
    end
    always_ff @(posedge clk) begin
        if (rst) begin busy <= 0; i_r <= '0; out_valid <= 0; end
        else begin
            out_valid <= 0;
            if (!busy) begin
                if (vk) begin x_r <= WG'(xk) <<< G; y_r <= WG'(yk) <<< G; z_r <= zk; i_r <= '0; busy <= 1; end
            end else begin
                x_r <= x_n; y_r <= y_n; z_r <= z_n;
                if (i_r == N-1) begin busy <= 0; out_valid <= 1; out_x <= W'(x_n >>> G); out_y <= W'(y_n >>> G); out_z <= z_n; end
                else i_r <= i_r + 1;
            end
        end
    end
"""
    return f"""// Generated by silicon-agent -- do not edit. IR: {a.to_dict()}
`default_nettype none
typedef logic signed [63:0] sd;

module cordic_top #(
    parameter int W  = {W},
    parameter int G  = {G},
    parameter int N  = {N}
) (
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 in_valid,
    input  wire signed [W-1:0]  in_x,
    input  wire signed [W-1:0]  in_y,
    input  wire signed [W-1:0]  in_z,
    output logic                in_ready,
    output logic                out_valid,
    output logic signed [W-1:0] out_x,
    output logic signed [W-1:0] out_y,
    output logic signed [W-1:0] out_z
);
    localparam int WG = W + G;
    localparam logic signed [W-1:0] ATAN [0:N-1] = '{{{at_lit}}};
{prescale}
{core}endmodule
`default_nettype wire
"""
