"""Decimating FIR (rate change by M): one output per M input samples.

  y[n] = sum_k h[k] x[nM + M-1 - k],   h of length N (a multiple of M), the output sampled at each frame end.

Structure: a delay line of the last N inputs shifts on every accept. When the M-th sample of a frame is accepted,
the delay line is snapshotted into a work register and the N products are swept over the next W = M*fold cycles
on K = ceil(N / W) multipliers (so the sweep of one output overlaps the arrival of the next frame; the core
accepts one sample every `fold` cycles). Per output N multiplications are done -- the polyphase saving over a
full-rate FIR is that the sweep has M times as many cycles, so K is 1/M of a full-rate direct form at the same
fold. With fold=1 and M=4 a 32-tap decimator needs 8 multipliers.

Knobs:
  taps, data_width, coef_width, cutoff   task parameters (the lowpass is designed for the output band)
  decim        int 2..16                  M, the rate change (task parameter)
  acc_width    int MIN..64                MIN = dw + cw + ceil(log2 N)
  fold         int 1..8                   cycles per accepted input sample (throughput 1/fold input samples/cycle)
  pipe_mul     int 0..3                   fabric register stages after the multipliers
  reg_in       bool                       register the input sample
  dsp_regs     str                        DSP-internal registers (explicit MULT18X18D on ECP5; behavioral elsewhere)
  sum_tree     bool                       register the K-way reduction of a sweep step (one stage)

Interface: in_valid / in_ready / in_data -> out_valid / out_data, one output per M accepted inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field

from .fir import DSP_REG_MODES, _coef_literal, _delay_chain, _mult_module, _style, lowpass_taps


@dataclass(frozen=True)
class DecimArch:
    taps: int = 32
    decim: int = 4
    data_width: int = 16
    coef_width: int = 16
    cutoff: float = 0.1
    acc_width: int = 40
    fold: int = 1
    pipe_mul: int = 1
    reg_in: bool = True
    dsp_regs: str = "none"
    acc_in_dsp: bool = True
    sum_tree: bool = False
    coefs: tuple[int, ...] = field(default_factory=lambda: tuple(lowpass_taps(32, cutoff=0.1)))

    @property
    def window(self) -> int:
        """Cycles available per output: M accepts of `fold` cycles each."""
        return self.decim * self.fold

    @property
    def multipliers(self) -> int:
        return math.ceil(self.taps / self.window)

    @property
    def steps(self) -> int:
        """Sweep steps per output (<= window)."""
        return math.ceil(self.taps / self.multipliers)

    @property
    def mul_latency(self) -> int:
        return self.pipe_mul + DSP_REG_MODES[self.dsp_regs]

    @property
    def min_acc_width(self) -> int:
        return self.data_width + self.coef_width + math.ceil(math.log2(max(self.taps, 2)))

    def validate(self) -> list[str]:
        errs = []
        if not 2 <= self.decim <= 16:
            errs.append("decim must be in 2..16")
        if not 2 <= self.taps <= 256 or self.taps % max(self.decim, 1):
            errs.append(f"taps must be in 2..256 and a multiple of decim ({self.decim})")
        if self.data_width not in (8, 12, 16, 18) or self.coef_width not in (8, 12, 16, 18):
            errs.append("data_width/coef_width must be one of 8, 12, 16, 18")
        if not 0.005 <= self.cutoff <= 0.49:
            errs.append("cutoff must be in 0.005..0.49 (fraction of the input sample rate)")
        if not self.min_acc_width <= self.acc_width <= 64:
            errs.append(f"acc_width must be in {self.min_acc_width}..64")
        if not 1 <= self.fold <= 8:
            errs.append("fold must be in 1..8")
        if not 0 <= self.pipe_mul <= 3:
            errs.append("pipe_mul must be in 0..3")
        if self.dsp_regs not in DSP_REG_MODES:
            errs.append(f"dsp_regs must be one of {sorted(DSP_REG_MODES)}")
        if len(self.coefs) != self.taps:
            errs.append(f"coefs has {len(self.coefs)} entries but taps={self.taps}")
        lo, hi = -(1 << (self.coef_width - 1)), (1 << (self.coef_width - 1)) - 1
        if any(not lo <= c <= hi for c in self.coefs):
            errs.append(f"coefficients exceed coef_width={self.coef_width}")
        return errs

    def to_dict(self) -> dict:
        d = asdict(self)
        d["coefs"] = list(self.coefs)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DecimArch":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "coefs"}
        taps = int(known.get("taps", 32))
        cw = int(known.get("coef_width", 16))
        known["cutoff"] = float(known.get("cutoff", 0.1))
        coefs = d.get("coefs")
        known["coefs"] = tuple(int(c) for c in coefs) if coefs else tuple(lowpass_taps(taps, cutoff=known["cutoff"], width=cw))
        return cls(**known)

    def digest(self, backend: str = "ecp5") -> str:
        d = self.to_dict()
        d.pop("cutoff", None)
        if _style(backend) != "vivado":
            d.pop("acc_in_dsp", None)
        if not d.get("sum_tree"):
            d.pop("sum_tree", None)
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


def throughput(a: DecimArch) -> float:
    """Input samples per cycle (the stream the core accepts); the output rate is this / decim."""
    return round(1.0 / a.fold, 4)


def latency_cycles(a: DecimArch) -> int:
    # from the frame's last accept: (reg_in) + snapshot + sweep steps + multiplier pipeline + (sum_tree) + output register
    return (1 if a.reg_in else 0) + 1 + a.steps + a.mul_latency + (1 if a.sum_tree else 0) + 1


# ---------------------------------------------------------------- golden
def golden(a: DecimArch, xs: list[int]) -> list[int]:
    """One output per full frame of M: y[n] = sum_k h[k] x[nM + M-1 - k], samples before the stream are 0."""
    N, M = a.taps, a.decim
    h = list(a.coefs)
    out = []
    for n in range(len(xs) // M):
        m = n * M + M - 1
        out.append(sum(h[k] * xs[m - k] for k in range(N) if m - k >= 0))
    return out


# ---------------------------------------------------------------- emitter
def emit_decim(a: DecimArch, backend: str = "ecp5") -> str:
    N, M, F, K, S = a.taps, a.decim, a.fold, a.multipliers, a.steps
    DW, CW, AW, ML = a.data_width, a.coef_width, a.acc_width, a.mul_latency
    reg_in = 1 if a.reg_in else 0
    acc_attr = '(* use_dsp = "yes" *)' if a.acc_in_dsp else '(* use_dsp = "no" *)'
    jw = max(1, math.ceil(math.log2(max(F, 2))))
    pw = max(1, math.ceil(math.log2(max(M, 2))))
    sw = max(1, math.ceil(math.log2(max(S + 1, 2))))
    coef_lit = _coef_literal(list(a.coefs))
    svpipe = _delay_chain(ML, [("sv_m", "sw_v"), ("sl_m", "sw_last")])
    header = f"// Generated by silicon-agent -- do not edit. IR: {json.dumps(a.to_dict())}\n`default_nettype none\n\ntypedef logic signed [63:0] sd;\n\n"
    # the FIR multiplier module (behavioral / explicit DSP by style) is reused: same ports (clk, x, h, prod)
    fir_like = type("A", (), {})()
    for k in ("data_width", "coef_width", "pipe_mul", "dsp_regs", "acc_in_dsp"):
        setattr(fir_like, k, getattr(a, k))
    fir_like.symmetric = False
    mult = _mult_module(fir_like, backend)
    # products of one sweep step, reduced K-way (optionally registered), then accumulated over the S steps
    if a.sum_tree and K > 1:
        reduction = """    logic signed [AW-1:0] psum_c, psum;
    logic                 ps_v, ps_l;
    always_comb begin
        psum_c = '0;
        for (int k = 0; k < K; k++) psum_c += AW'(prod[k]);
    end
    always_ff @(posedge clk) begin psum <= psum_c; ps_v <= sv_m & ~rst; ps_l <= sl_m; end"""
    else:
        reduction = """    logic signed [AW-1:0] psum;
    always_comb begin
        psum = '0;
        for (int k = 0; k < K; k++) psum += AW'(prod[k]);
    end
    wire ps_v = sv_m & ~rst; wire ps_l = sl_m;"""
    body = f"""module decim_top #(
    parameter int N  = {N},
    parameter int M  = {M},
    parameter int F  = {F},
    parameter int K  = {K},
    parameter int S  = {S},
    parameter int DW = {DW},
    parameter int CW = {CW},
    parameter int AW = {AW}
) (
    input  wire                   clk,
    input  wire                   rst,
    input  wire                   in_valid,
    input  wire signed [DW-1:0]   in_data,
    output logic                  in_ready,
    output logic                  out_valid,
    output logic signed [AW-1:0]  out_data
);
    localparam int ML = {ML};
    localparam int JW = {jw};
    localparam int PW = {pw};
    localparam int SW = {sw};
    localparam logic signed [CW-1:0] COEFS [N] = '{{{coef_lit}}};

    // ---- handshake: one sample every F cycles; ph counts the sample's place in its frame of M ----
    logic          busy;
    logic          pend;
    logic [JW-1:0] j;
    logic [PW-1:0] ph;
    logic          accept;
    assign in_ready = ~busy & ~pend;
    assign accept   = in_valid & in_ready & ~rst;

    logic signed [DW-1:0] x0;
    logic                 v0;
    generate
        if ({reg_in}) begin : g_reg_in
            always_ff @(posedge clk) begin x0 <= in_data; v0 <= accept; pend <= accept; end
        end else begin : g_no_reg_in
            assign x0   = in_data;
            assign v0   = accept;
            assign pend = 1'b0;
        end
    endgenerate

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0; j <= '0; ph <= '0;
        end else if (v0) begin
            busy <= (F > 1);
            j    <= '0;
            ph   <= (ph == PW'(M - 1)) ? '0 : ph + 1'b1;
        end else if (busy) begin
            j <= j + 1'b1;
            if (j == JW'(F - 2)) busy <= 1'b0;
        end
    end
    wire frame_end = v0 && (ph == PW'(M - 1));    // this accept completes a frame

    // ---- delay line: x_d[k] = x[m-k] ----
    logic signed [DW-1:0] x_d [N];
    always_ff @(posedge clk) begin
        if (rst) begin
            for (int k = 0; k < N; k++) x_d[k] <= '0;
        end else if (v0) begin
            x_d[0] <= x0;
            for (int k = 1; k < N; k++) x_d[k] <= x_d[k-1];
        end
    end

    // ---- at a frame end the line is snapshotted and swept over S steps while the next frame streams in ----
    logic signed [DW-1:0] snap [N];
    logic [SW-1:0] st;
    logic          sweeping;
    always_ff @(posedge clk) begin
        if (rst) begin
            sweeping <= 1'b0; st <= '0;
        end else if (frame_end) begin
            // the snapshot includes the sample being accepted now (x0 lands in x_d[0] this edge)
            snap[0] <= x0;
            for (int k = 1; k < N; k++) snap[k] <= x_d[k-1];
            sweeping <= 1'b1; st <= '0;
        end else if (sweeping) begin
            st <= st + 1'b1;
            if (st == SW'(S - 1)) sweeping <= 1'b0;
        end
    end
    wire sw_v    = sweeping;
    wire sw_last = sweeping && (st == SW'(S - 1));

    // ---- step valid / last travel alongside the multiplier pipeline ----
    logic sv_m, sl_m;
{svpipe}

    // ---- K multipliers: step st handles taps st*K .. st*K+K-1 (taps beyond N multiply by zero) ----
    logic signed [DW+CW-1:0] prod [K];
    generate
        for (genvar k = 0; k < K; k++) begin : g_mac
            logic signed [DW-1:0] xm;
            logic signed [CW-1:0] hm;
            wire [31:0] t = st * K + k;
            assign xm = (t < N) ? snap[t] : DW'(0);
            assign hm = (t < N) ? COEFS[t] : CW'(0);
            fir_mult #(.XW(DW), .CW(CW)) u_m (.clk(clk), .x(xm), .h(hm), .prod(prod[k]));
        end
    endgenerate

{reduction}

    // ---- accumulate the S step sums; emit at the last step ----
    {acc_attr} logic signed [AW-1:0] acc;
    always_ff @(posedge clk) begin
        if (rst) begin
            acc <= '0; out_valid <= 1'b0; out_data <= '0;
        end else begin
            out_valid <= 1'b0;
            if (ps_v) begin
                if (ps_l) begin
                    out_data  <= acc + psum;
                    out_valid <= 1'b1;
                    acc       <= '0;
                end else begin
                    acc <= acc + psum;
                end
            end
        end
    end
endmodule
"""
    return header + mult + "\n" + body + "\n`default_nettype wire\n"
