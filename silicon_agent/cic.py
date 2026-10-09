"""CIC (Cascaded Integrator-Comb) decimating filter (Hogenauer).

N integrators at the input rate -> decimate by R -> N combs (differential delay M=1).
Signed fixed point; every register is reg_width = data_width + ceil(N*log2(R)) bits
(two's complement, wrapping mod 2^reg_width); the output takes the top out_width bits.

Knobs:
  pipe_integ  bool  register between the integrator stages (default true): stage j adds the previous
                    stage's *registered* output (integ[j] <= integ[j] + integ[j-1]), the standard FPGA
                    CIC form -- the critical path is a single B-bit adder, and the decimation sample s is
                    the registered integ[N-1] (the R-th input is NOT yet in the accumulator). false = a
                    combinational chain (i_next[j] = integ[j] + i_next[j-1]); s is the combinational
                    i_next[N-1] (the R-th input IS in the accumulator). The two forms differ by one
                    sample of phase; the golden model mirrors the selected form bit-exactly.
  pipe_comb   bool  register between the comb stages: the comb runs freely by the clock with a valid
                    shift chain (c_v[j]); each stage's delay register updates only when its valid bit is
                    set. The N-th difference appears N+1 cycles after its decimation tick, independent of
                    input pauses.
  reg_in      bool  register the input sample (+1 cycle latency)

Interface: in_valid / in_ready (always 1) / in_data -> out_valid / out_data; one output per R valid inputs.
There are no multipliers (DSP is always 0): the datapath is N integrator adders and N comb
subtractors, each reg_width bits wide. The golden model mirrors the exact synchronous register
semantics and the mod-2^B wraparound, so the behavioral check is bit-exact, not an error bound.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass


def reg_width(a: "CicArch") -> int:
    """Register width per Hogenauer: data_width + N*log2(R*M) with M=1, rounded up."""
    return a.data_width + int(math.ceil(a.stages * math.log2(a.decim)))


@dataclass(frozen=True)
class CicArch:
    data_width: int = 16      # task: input sample bits
    stages: int = 3           # task: N, 1..5
    decim: int = 8            # task: R, 2..64
    out_width: int = 16       # task: output bits (top bits of the register)
    pipe_comb: bool = False   # register between combs
    reg_in: bool = False      # register the input
    pipe_integ: bool = True   # register between integrator stages

    def validate(self) -> list[str]:
        errs = []
        if not 2 <= self.data_width <= 32:
            errs.append("data_width must be in 2..32")
        if not 1 <= self.stages <= 5:
            errs.append("stages (N) must be in 1..5")
        if not 2 <= self.decim <= 64:
            errs.append("decim (R) must be in 2..64")
        if not 1 <= self.out_width <= reg_width(self):
            errs.append(f"out_width must be in 1..{reg_width(self)} (the register width)")
        return errs

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CicArch":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def digest(self, backend: str = "ecp5") -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:12]


def throughput(a: CicArch) -> float:
    """Input samples per cycle (the stream the core accepts); the output rate is 1/R of this."""
    return 1.0


def latency_cycles(a: CicArch) -> int:
    """Cycles from the decimation boundary (the R-th valid input of a frame) to its output."""
    if a.pipe_comb:
        return (1 if a.reg_in else 0) + a.stages + 2
    return (1 if a.reg_in else 0) + 1


# ---------------------------------------------------------------- golden (bit-exact)
def _trunc(v: int, B: int, OW: int) -> int:
    """Top OW bits of a signed B-bit value, returned as a signed OW-bit two's complement int."""
    s = v >> (B - OW)
    s &= (1 << OW) - 1
    return s - (1 << OW) if s & (1 << (OW - 1)) else s


def golden(a: CicArch, xs: list[int]) -> list[int]:
    """One output per R valid inputs, mirroring the RTL exactly. pipe_integ=true samples the registered
    integ[N-1] BEFORE the R-th input is added (so a decimation frame accumulates R-1 inputs); pipe_integ=false
    samples the combinational i_next[N-1] AFTER the R-th input (R inputs). Both then apply N comb differences
    (mod-2^B) and take the top out_width bits. pipe_comb only delays the comb by N+1 cycles: the output
    *sequence* is identical, so both modes return the same samples."""
    N, R, B, OW = a.stages, a.decim, reg_width(a), a.out_width
    mask = (1 << B) - 1
    sign = 1 << (B - 1)

    def wrap(v: int) -> int:
        v &= mask
        return v - (1 << B) if v & sign else v

    def comb(prev: int) -> int:
        for j in range(N):
            new = wrap(prev - comb_d[j])
            comb_d[j] = prev
            prev = new
        return prev

    integ = [0] * N
    comb_d = [0] * N
    cnt = 0
    out = []
    for x in xs:
        if a.pipe_integ:
            # sample the registered accumulator BEFORE this input is added
            cnt += 1
            if cnt == R:
                cnt = 0
                out.append(_trunc(comb(integ[N - 1]), B, OW))
            nn = [0] * N
            nn[0] = wrap(integ[0] + x)
            for j in range(1, N):
                nn[j] = wrap(integ[j] + integ[j - 1])
            integ = nn
        else:
            # combinational chain: this input is already in i_next before the sample
            nn = [0] * N
            nn[0] = wrap(integ[0] + x)
            for j in range(1, N):
                nn[j] = wrap(integ[j] + nn[j - 1])
            integ = nn
            cnt += 1
            if cnt == R:
                cnt = 0
                out.append(_trunc(comb(integ[N - 1]), B, OW))
    return out


# ---------------------------------------------------------------- emitter
def emit_cic(a: CicArch, backend: str = "ecp5") -> str:
    DW, N, R, OW = a.data_width, a.stages, a.decim, a.out_width
    B = reg_width(a)
    PW = max(1, int(math.ceil(math.log2(R))))
    SHIFT = B - OW
    reg_in = 1 if a.reg_in else 0
    pipe_integ = 1 if a.pipe_integ else 0
    if a.pipe_comb:
        comb = f"""    // ---- N combs (M=1), pipelined in the tick domain, running freely by the clock. c_v[j] is the valid
    //      bit of the tick entering stage j; stage j updates its delay register only while its tick flows
    //      through, so a pause in the input stream leaves the comb state untouched. Each tick traverses the N
    //      stages one per cycle: the N-th difference of dec_tick t lands on out_data N+1 cycles after t.
    logic signed [B-1:0] c_x [N+1];   // c_x[0] = decimated sample; c_x[j+1] = stage j's output
    logic                c_v [N+1];
    logic signed [B-1:0] comb_d [N];
    always_ff @(posedge clk) begin
        if (rst) begin
            for (int j = 0; j <= N; j++) c_v[j] <= 1'b0;
            for (int j = 0; j < N; j++) comb_d[j] <= '0;
            out_valid <= 1'b0;
            out_data  <= '0;
        end else begin
            c_v[0] <= dec_tick;
            if (dec_tick) c_x[0] <= s;
            for (int j = 0; j < N; j++) begin
                c_v[j+1] <= c_v[j];
                if (c_v[j]) begin
                    comb_d[j] <= c_x[j];
                    c_x[j+1]  <= c_x[j] - comb_d[j];
                end
            end
            out_valid <= c_v[N];
            if (c_v[N]) out_data <= OW'(c_x[N] >>> SHIFT);
        end
    end"""
    else:
        comb = f"""    // ---- N combs (M=1), combinational within one output tick: out = in - in_delayed ----
    logic signed [B-1:0] comb_d [N];
    logic signed [B-1:0] c_comb [N];
    always_comb begin
        c_comb[0] = s - comb_d[0];
        for (int j = 1; j < N; j++) c_comb[j] = c_comb[j-1] - comb_d[j];
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            for (int j = 0; j < N; j++) comb_d[j] <= '0;
            out_valid <= 1'b0;
            out_data  <= '0;
        end else begin
            out_valid <= dec_tick;
            if (dec_tick) begin
                comb_d[0] <= s;
                for (int j = 1; j < N; j++) comb_d[j] <= c_comb[j-1];
                out_data <= OW'(c_comb[N-1] >>> SHIFT);
            end
        end
    end"""
    return f"""// Generated by silicon-agent -- do not edit. IR: {json.dumps(a.to_dict())}
`default_nettype none

module cic_top #(
    parameter int DW = {DW},
    parameter int N  = {N},
    parameter int R  = {R},
    parameter int OW = {OW},
    parameter int B  = {B}
) (
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 in_valid,
    input  wire signed [DW-1:0] in_data,
    output logic                in_ready,
    output logic                out_valid,
    output logic signed [OW-1:0] out_data
);
    localparam int PW = {PW};
    localparam int SHIFT = {SHIFT};
    assign in_ready = 1'b1;

    // ---- input register (optional) ----
    logic signed [DW-1:0] x0;
    logic                 v;
    generate
        if ({reg_in}) begin : g_reg_in
            always_ff @(posedge clk) begin x0 <= in_data; v <= in_valid & ~rst; end
        end else begin : g_no_reg_in
            assign x0 = in_data;
            assign v  = in_valid & ~rst;
        end
    endgenerate

    // ---- N integrators at the input rate ----
    logic signed [B-1:0] integ [N];
    logic signed [B-1:0] i_next [N];
    wire signed [B-1:0] s;
    generate
        if ({pipe_integ}) begin : g_pipe_integ
            // standard FPGA CIC: each stage adds the previous stage's *registered* output; s is the
            // registered integ[N-1], so the decimation sample lags by one input (R-1 inputs per frame)
            always_ff @(posedge clk) begin
                if (rst) begin
                    for (int j = 0; j < N; j++) integ[j] <= '0;
                end else if (v) begin
                    integ[0] <= integ[0] + x0;
                    for (int j = 1; j < N; j++) integ[j] <= integ[j] + integ[j-1];
                end
            end
            assign s = integ[N-1];
        end else begin : g_comb_integ
            // combinational chain: stage j adds the previous stage's *current* output; s is the
            // combinational i_next[N-1] and already includes the R-th input
            always_comb begin
                i_next[0] = integ[0] + x0;
                for (int j = 1; j < N; j++) i_next[j] = integ[j] + i_next[j-1];
            end
            always_ff @(posedge clk) begin
                if (rst) begin
                    for (int j = 0; j < N; j++) integ[j] <= '0;
                end else if (v) begin
                    for (int j = 0; j < N; j++) integ[j] <= i_next[j];
                end
            end
            assign s = i_next[N-1];
        end
    endgenerate

    // ---- decimation: sample the last integrator on every R-th valid input ----
    logic [PW-1:0] ph;
    always_ff @(posedge clk) begin
        if (rst) ph <= '0;
        else if (v) ph <= (ph == PW'(R-1)) ? '0 : ph + 1'b1;
    end
    wire dec_tick = v && (ph == PW'(R-1));

{comb}
endmodule
`default_nettype wire
"""
