"""IIR filter as a cascade of second-order sections (biquads), transposed direct form II.

  Each section:  y[n] = b0 x[n] + s1[n-1]
                 s1[n] = b1 x[n] - a1 y[n] + s2[n-1]
                 s2[n] = b2 x[n] - a2 y[n]
  (TDF-II: two state registers per section, the recursive path is y -> a1/a2 multiplies -> the state adders.)

Coefficients: a Butterworth lowpass designed from `cutoff` and `order` (2 * sections), bilinear transform, split
into biquads from conjugate pole pairs; quantized to `coef_width` bits with `coef_frac` fractional bits (a1/a2
need |a| < 2 so coef_frac <= coef_width - 2). The state is `state_width` bits with the same fractional
alignment; the recursive products are rounded (arithmetic shift of the fractional bits) -- the golden model
mirrors the exact fixed-point arithmetic, so the testbench checks bit-exactly rather than "close to float".

Knobs:
  sections     task   number of biquads (filter order / 2)
  data_width, coef_width, cutoff   task
  coef_frac    int    fractional bits of the coefficients (precision of the poles; <= coef_width-2)
  state_width  int    width of the section state / internal signal (headroom + precision)
  pipe_mul     int    0..1 register after the feed-forward (b) multipliers (the recursive a-path cannot be
                      pipelined without changing the filter -- it is the inherent critical path)
  dsp_regs     str    DSP-internal registers of the multipliers (feed-forward only; "out" on the a-path would break
                      the recursion, so the a multipliers are always combinational)

Interface: in_valid / in_ready / in_data -> out_valid / out_data (data_width bits, saturated).
"""

from __future__ import annotations

import cmath
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field

from .fir import DSP_REG_MODES, _style


# ---------------------------------------------------------------- design: Butterworth -> biquads (float), then quantize
def butter_biquads(sections: int, cutoff: float) -> list[tuple[float, float, float, float, float]]:
    """(b0, b1, b2, a1, a2) per section, a0 = 1, for a Butterworth lowpass of order 2*sections with -3 dB at `cutoff`
    (fraction of fs), by bilinear transform with prewarping; poles paired into biquads from the outermost pair in."""
    N = 2 * sections
    wc = 2 * math.tan(math.pi * cutoff)          # prewarped analog cutoff (fs = 2)
    poles = [wc * cmath.exp(1j * math.pi * (2 * k + N + 1) / (2 * N)) for k in range(N)]
    out = []
    # bilinear: z = (2 + s) / (2 - s); each conjugate pair -> one biquad; gain so that H(1) = 1
    pairs = sorted([p for p in poles if p.imag >= 0], key=lambda p: -abs(p.real))
    for p in pairs:
        zp = (2 + p) / (2 - p)
        a1 = -2 * zp.real
        a2 = abs(zp) ** 2
        # numerator (1 + z^-1)^2, scaled so DC gain is 1: (1 + a1 + a2) / 4
        g = (1 + a1 + a2) / 4
        out.append((g, 2 * g, g, a1, a2))
    return out


def quantize(biquads, coef_width: int, coef_frac: int) -> list[tuple[int, int, int, int, int]]:
    lo, hi = -(1 << (coef_width - 1)), (1 << (coef_width - 1)) - 1
    q = lambda v: max(lo, min(hi, int(round(v * (1 << coef_frac)))))
    return [tuple(q(v) for v in bq) for bq in biquads]


@dataclass(frozen=True)
class IirArch:
    sections: int = 2
    data_width: int = 16
    coef_width: int = 18
    cutoff: float = 0.1
    coef_frac: int = 15
    state_width: int = 24
    pipe_mul: int = 0
    dsp_regs: str = "none"
    reg_in: bool = True
    coefs: tuple = field(default_factory=tuple)   # quantized (b0,b1,b2,a1,a2) per section; derived from the task fields when empty

    @property
    def multipliers(self) -> int:
        return 5 * self.sections

    def validate(self) -> list[str]:
        errs = []
        if not 1 <= self.sections <= 8:
            errs.append("sections must be in 1..8")
        if self.data_width not in (8, 12, 16, 18, 24):
            errs.append("data_width must be one of 8, 12, 16, 18, 24")
        if self.coef_width not in (12, 16, 18, 24):
            errs.append("coef_width must be one of 12, 16, 18, 24")
        if not 0.01 <= self.cutoff <= 0.45:
            errs.append("cutoff must be in 0.01..0.45")
        if not 4 <= self.coef_frac <= self.coef_width - 2:
            errs.append(f"coef_frac must be in 4..{self.coef_width - 2} (a1 reaches |2| for sharp lowpass)")
        if not self.data_width + 2 <= self.state_width <= 48:
            errs.append(f"state_width must be in {self.data_width + 2}..48")
        if self.pipe_mul not in (0, 1):
            errs.append("pipe_mul must be 0 or 1")
        if self.dsp_regs not in DSP_REG_MODES:
            errs.append(f"dsp_regs must be one of {sorted(DSP_REG_MODES)}")
        if self.coefs and len(self.coefs) != self.sections:
            errs.append("coefs must have one (b0,b1,b2,a1,a2) per section")
        # stability after quantization: |a2| < 1 and |a1| < 1 + a2
        for bq in self.coefs:
            b0, b1, b2, a1, a2 = (v / (1 << self.coef_frac) for v in bq)
            if not (abs(a2) < 1 and abs(a1) < 1 + a2):
                errs.append(f"section unstable after quantization: a1={a1:.4f} a2={a2:.4f} (raise coef_frac)")
                break
        return errs

    def to_dict(self) -> dict:
        d = asdict(self)
        d["coefs"] = [list(bq) for bq in self.coefs]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "IirArch":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "coefs"}
        known["cutoff"] = float(known.get("cutoff", 0.1))
        sec = int(known.get("sections", 2)); cw = int(known.get("coef_width", 18)); cf = int(known.get("coef_frac", 15))
        coefs = d.get("coefs")
        if coefs:
            known["coefs"] = tuple(tuple(int(v) for v in bq) for bq in coefs)
        else:
            known["coefs"] = tuple(quantize(butter_biquads(sec, known["cutoff"]), cw, min(cf, cw - 2)))
        return cls(**known)

    def digest(self, backend: str = "ecp5") -> str:
        d = self.to_dict()
        d.pop("cutoff", None)
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


def throughput(a: IirArch) -> float:
    return 1.0


def latency_cycles(a: IirArch) -> int:
    per = 1 + a.pipe_mul + DSP_REG_MODES[a.dsp_regs]
    return (1 if a.reg_in else 0) + a.sections * per + 1


# ---------------------------------------------------------------- golden (bit-exact fixed point)
def _sat(v: int, w: int) -> int:
    lo, hi = -(1 << (w - 1)), (1 << (w - 1)) - 1
    return max(lo, min(hi, v))


def _rnd(v: int, f: int) -> int:
    """Arithmetic shift right by f (floor), as the RTL's >>> does."""
    return v >> f


def golden(a: IirArch, xs: list[int]) -> list[int]:
    """Section by section, TDF-II with state_width-bit states; products are rounded to the state scale after each
    multiply (the RTL truncates the coef_frac fractional bits of every product), the output is saturated to
    data_width. The section input is the previous section's saturated output (state_width intermediates between
    sections would differ from the RTL, which passes data_width between sections)."""
    F, SW, DW = a.coef_frac, a.state_width, a.data_width
    states = [[0, 0] for _ in a.coefs]
    out = []
    for x in xs:
        v = x
        for k, (b0, b1, b2, a1, a2) in enumerate(a.coefs):
            s1, s2 = states[k]
            y = _sat(_rnd(b0 * v, F) + s1, SW)
            yo = _sat(y, DW)
            n1 = _sat(_rnd(b1 * v, F) - _rnd(a1 * yo, F) + s2, SW)
            n2 = _sat(_rnd(b2 * v, F) - _rnd(a2 * yo, F), SW)
            states[k] = [n1, n2]
            v = yo
        out.append(v)
    return out


# ---------------------------------------------------------------- emitter
def _section(k: int, a: IirArch, bq: tuple, backend: str) -> str:
    """One TDF-II biquad as a module; the a-path multiplies are combinational (the recursion), the b-path may carry
    pipe_mul/dsp_regs stages (their latency is matched on the state side by delaying the state add)."""
    b0, b1, b2, a1, a2 = bq
    DW, CW, SW, F = a.data_width, a.coef_width, a.state_width, a.coef_frac
    PL = a.pipe_mul + DSP_REG_MODES[a.dsp_regs]
    lit = lambda v: f"{CW}'sd{v}" if v >= 0 else f"-{CW}'sd{-v}"
    # feed-forward products, optionally registered PL times (same shape as the FIR multiplier pipeline)
    def ffp(name: str, coef: int) -> str:
        if PL == 0:
            return f"    wire signed [DW+CW-1:0] {name} = x * {lit(coef)};"
        regs = "".join(f" {name}_q[{i}] <= {name}_q[{i-1}];" for i in range(1, PL))
        return f"    logic signed [DW+CW-1:0] {name}_q [{PL}];\n    always_ff @(posedge clk) begin {name}_q[0] <= x * {lit(coef)};{regs} end\n    wire signed [DW+CW-1:0] {name} = {name}_q[{PL - 1}];"
    vchain = "" if PL == 0 else f"    logic v_q [{PL}];\n    always_ff @(posedge clk) if (rst) begin {' '.join(f'v_q[{i}] <= 0;' for i in range(PL))} end else begin v_q[0] <= in_valid;{''.join(f' v_q[{i}] <= v_q[{i-1}];' for i in range(1, PL))} end\n"
    vd = "in_valid" if PL == 0 else f"v_q[{PL - 1}]"
    return f"""module iir_sec{k} #(parameter int DW = {DW}, CW = {CW}, SW = {SW}, F = {F}) (
    input  wire clk, input wire rst, input wire in_valid, input wire signed [DW-1:0] in_data,
    output logic out_valid, output logic signed [DW-1:0] out_data);
    wire signed [DW-1:0] x = in_data;
{ffp("p0", b0)}
{ffp("p1", b1)}
{ffp("p2", b2)}
{vchain}    // the input sample delayed to the feed-forward latency (the state add must see the matching x)
    logic signed [SW-1:0] s1, s2;
    // y = b0 x + s1  (rounded, saturated to the state width, then to the data width for the output and recursion)
    localparam logic signed [SW+1:0] SMAX = (SW+2)'((1 << (SW-1)) - 1);
    localparam logic signed [SW+1:0] SMIN = -(SW+2)'((1 << (SW-1)));
    localparam logic signed [SW+1:0] DMAX = (SW+2)'((1 << (DW-1)) - 1);
    localparam logic signed [SW+1:0] DMIN = -(SW+2)'((1 << (DW-1)));
    function automatic logic signed [SW-1:0] satw(input logic signed [SW+1:0] v);
        if (v > SMAX) return SW'(SMAX);
        if (v < SMIN) return SW'(SMIN);
        return SW'(v);
    endfunction
    function automatic logic signed [DW-1:0] satd(input logic signed [SW+1:0] v);
        if (v > DMAX) return DW'(DMAX);
        if (v < DMIN) return DW'(DMIN);
        return DW'(v);
    endfunction
    wire signed [SW+1:0]  y_full = (SW+2)'(p0 >>> F) + (SW+2)'(s1);
    wire signed [SW-1:0]  y_sat  = satw(y_full);
    wire signed [DW-1:0]  yo     = satd((SW+2)'(y_sat));
    // the recursive products: combinational (pipelining them would change the filter)
    wire signed [DW+CW-1:0] ra1 = yo * {lit(a1)};
    wire signed [DW+CW-1:0] ra2 = yo * {lit(a2)};
    wire signed [SW+1:0] n1_full = (SW+2)'(p1 >>> F) - (SW+2)'(ra1 >>> F) + (SW+2)'(s2);
    wire signed [SW+1:0] n2_full = (SW+2)'(p2 >>> F) - (SW+2)'(ra2 >>> F);
    always_ff @(posedge clk) begin
        if (rst) begin
            s1 <= '0; s2 <= '0; out_valid <= 1'b0; out_data <= '0;
        end else begin
            out_valid <= {vd};
            if ({vd}) begin
                s1 <= satw(n1_full);
                s2 <= satw(n2_full);
                out_data <= yo;
            end
        end
    end
endmodule
"""


def emit_iir(a: IirArch, backend: str = "ecp5") -> str:
    DW = a.data_width
    header = f"// Generated by silicon-agent -- do not edit. IR: {json.dumps(a.to_dict())}\n`default_nettype none\n\n"
    secs = "\n".join(_section(k, a, bq, backend) for k, bq in enumerate(a.coefs))
    N = a.sections
    # one hardware section per biquad, chained (one sample per cycle)
    chain = []
    for k in range(N):
        src_v = "v0" if k == 0 else f"v_{k - 1}"
        src_d = "x0" if k == 0 else f"d_{k - 1}"
        chain.append(f"    logic v_{k}; logic signed [DW-1:0] d_{k};\n    iir_sec{k} u_s{k} (.clk(clk), .rst(rst), .in_valid({src_v}), .in_data({src_d}), .out_valid(v_{k}), .out_data(d_{k}));")
    body = "\n".join(chain) + f"\n    assign in_ready = 1'b1;\n    always_ff @(posedge clk) begin out_valid <= v_{N - 1} & ~rst; out_data <= d_{N - 1}; end"
    return f"""{header}{secs}
module iir_top #(parameter int DW = {DW}) (
    input  wire                   clk,
    input  wire                   rst,
    input  wire                   in_valid,
    input  wire signed [DW-1:0]   in_data,
    output logic                  in_ready,
    output logic                  out_valid,
    output logic signed [DW-1:0]  out_data
);
    logic signed [DW-1:0] x0; logic v0;
    generate
        if ({1 if a.reg_in else 0}) begin : g_reg_in
            always_ff @(posedge clk) begin x0 <= in_data; v0 <= in_valid & in_ready & ~rst; end
        end else begin : g_no_reg_in
            assign x0 = in_data; assign v0 = in_valid & in_ready & ~rst;
        end
    endgenerate
{body}
endmodule
`default_nettype wire
"""
