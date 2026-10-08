"""FIR filter IR and deterministic SystemVerilog emitter.

One IR, three structures selected by `symmetric` and `fold`:

  fold=1, symmetric=false   transposed form. x[n] is broadcast to N multipliers;
                            partial sums travel through a register chain, so every
                            adder is local and latency is independent of N.
  fold=1, symmetric=true    transposed form with coefficient sharing: h[k]==h[N-1-k],
                            so one product h[k]*x[n] is injected at chain stages k
                            and N-1-k. ceil(N/2) multipliers, N adders.
  fold=F>1                  direct form, time-multiplexed: a delay line holds the
                            last N samples; U/F physical MACs each sweep F taps in
                            F cycles (coefficient ROM indexed by the step counter).
                            One sample accepted every F cycles (in_ready), throughput 1/F.
                            With symmetric=true the pre-add halves the taps swept.

Shared knobs (same meaning as the matmul design): pipe_mul = fabric registers after
the multiplier, dsp_regs = DSP-internal registers, acc_in_dsp = accumulate inside
the DSP post-adder (xc7s50), reg_in = input register.

Interface: in_valid/in_data/in_ready -> out_valid/out_data. Exactly one output per
accepted sample, no flush needed, latency-insensitive -- the testbench compares
output streams only.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field

from .ir import DSP_REG_MODES


def lowpass_taps(n: int, cutoff: float = 0.2, width: int = 16) -> list[int]:
    """Windowed-sinc lowpass (Hamming), symmetric, unity DC gain, quantized to `width`-bit signed."""
    m = n - 1
    h = []
    for i in range(n):
        x = i - m / 2
        s = 2 * cutoff if x == 0 else math.sin(2 * math.pi * cutoff * x) / (math.pi * x)
        w = 0.54 - 0.46 * math.cos(2 * math.pi * i / m) if m else 1.0
        h.append(s * w)
    scale = (1 << (width - 1)) - 1
    q = [int(round(v / sum(h) * scale)) for v in h]
    lo, hi = -(1 << (width - 1)), (1 << (width - 1)) - 1
    q = [max(lo, min(hi, v)) for v in q]
    # keep exact symmetry after rounding
    for i in range(n // 2):
        q[n - 1 - i] = q[i]
    return q


@dataclass(frozen=True)
class FirArch:
    taps: int = 32
    data_width: int = 16
    coef_width: int = 16
    cutoff: float = 0.2  # normalized lowpass cutoff (fraction of fs) used to generate the coefficients
    acc_width: int = 40
    symmetric: bool = False
    fold: int = 1
    pipe_mul: int = 1
    reg_in: bool = True
    dsp_regs: str = "none"
    acc_in_dsp: bool = True
    coef_port: bool = False  # true: coefficients live in a register file with a write port (coef_we/coef_addr/coef_data); false: constants
    sum_tree: bool = False  # fold>1: reduce the M partial sums in a registered balanced tree (one stage per level) instead of one combinational chain
    pipe_in: bool = False  # fold>1: register the multiplier operands after the symmetric pre-adder and the tap mux (one cycle, no throughput change)
    coefs: tuple[int, ...] = field(default_factory=lambda: tuple(lowpass_taps(32)))

    @property
    def unique_taps(self) -> int:
        return (self.taps + 1) // 2 if self.symmetric else self.taps

    @property
    def multipliers(self) -> int:
        return math.ceil(self.unique_taps / self.fold)

    @property
    def mul_latency(self) -> int:
        return self.pipe_mul + DSP_REG_MODES[self.dsp_regs] + (1 if (self.fold > 1 and self.pipe_in) else 0)

    @property
    def sum_levels(self) -> int:
        """Register stages of the final reduction (fold>1 only): log2 of the multiplier count when sum_tree is on."""
        return math.ceil(math.log2(self.multipliers)) if (self.fold > 1 and self.sum_tree and self.multipliers > 1) else 0

    @property
    def min_acc_width(self) -> int:
        return self.data_width + self.coef_width + math.ceil(math.log2(max(self.taps, 2))) + (1 if self.symmetric else 0)

    def validate(self) -> list[str]:
        errs = []
        if not 2 <= self.taps <= 256:
            errs.append("taps must be in 2..256")
        if self.data_width not in (8, 12, 16, 18):
            errs.append("data_width must be one of 8, 12, 16, 18")
        if self.coef_width not in (8, 12, 16, 18):
            errs.append("coef_width must be one of 8, 12, 16, 18")
        if self.coef_port and self.unique_taps < 2:
            errs.append("coef_port needs at least 2 unique taps")
        if not 0.01 <= self.cutoff <= 0.49:
            errs.append("cutoff must be in 0.01..0.49 (fraction of the sample rate)")
        if not self.min_acc_width <= self.acc_width <= 64:
            errs.append(f"acc_width must be in {self.min_acc_width}..64 for these widths/taps")
        if not 1 <= self.fold <= 32:
            errs.append("fold must be in 1..32")
        if self.fold > 1 and self.unique_taps % self.fold:
            errs.append(f"fold must divide the number of unique taps ({self.unique_taps})")
        if not 0 <= self.pipe_mul <= 3:
            errs.append("pipe_mul must be in 0..3")
        if self.dsp_regs not in DSP_REG_MODES:
            errs.append(f"dsp_regs must be one of {sorted(DSP_REG_MODES)}")
        if len(self.coefs) != self.taps:
            errs.append(f"coefs has {len(self.coefs)} entries but taps={self.taps}")
        elif self.symmetric and any(self.coefs[i] != self.coefs[self.taps - 1 - i] for i in range(self.taps // 2)):
            errs.append("symmetric=true but the coefficients are not symmetric")
        lo, hi = -(1 << (self.coef_width - 1)), (1 << (self.coef_width - 1)) - 1
        if any(not lo <= c <= hi for c in self.coefs):
            errs.append(f"coefficients exceed coef_width={self.coef_width}")
        return errs

    def to_dict(self) -> dict:
        d = asdict(self)
        d["coefs"] = list(self.coefs)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FirArch":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "coefs"}
        taps = int(known.get("taps", 32))
        cw = int(known.get("coef_width", 16))
        known["cutoff"] = float(known.get("cutoff", 0.2))
        coefs = d.get("coefs")
        known["coefs"] = tuple(int(c) for c in coefs) if coefs else tuple(lowpass_taps(taps, cutoff=known["cutoff"], width=cw))
        return cls(**known)

    def digest(self, backend: str = "ecp5") -> str:
        d = self.to_dict()
        d.pop("cutoff", None)  # the coefficients already carry it; keeps older cache entries valid
        if _style(backend) != "vivado":
            d.pop("acc_in_dsp", None)
        for k in ("sum_tree", "pipe_in"):
            if not d.get(k):
                d.pop(k, None)  # knobs added later; off = the RTL older entries were measured on
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


def throughput(a: FirArch) -> float:
    return round(1.0 / a.fold, 4)


def latency_cycles(a: FirArch) -> int:
    base = (1 if a.reg_in else 0) + a.mul_latency + 2
    return base if a.fold == 1 else base + a.fold + 1 + a.sum_levels


# ---------------------------------------------------------------- emitter helpers
def _coef_literal(cs: list[int]) -> str:
    return ", ".join(f"CW'(sd'({c}))" for c in cs)


def _style(backend: str) -> str:
    from .backends import BACKENDS
    return BACKENDS[backend].emit_style if backend in BACKENDS else "behavioral"


def _ecp5_dsp_body(mode: str) -> str:
    inr = "CLK0" if mode in ("in_out", "in_pipe_out") else "NONE"
    pipe = "CLK0" if mode == "in_pipe_out" else "NONE"
    outr = "CLK0" if mode in ("out", "in_out", "in_pipe_out") else "NONE"
    A = ",".join(f".A{i}(a_ext[{i}])" for i in range(18))
    B = ",".join(f".B{i}(b_ext[{i}])" for i in range(18))
    C = ",".join(f".C{i}(1'b0)" for i in range(18))
    P = ",".join(f".P{i}(p_full[{i}])" for i in range(36))
    return f"""    logic [17:0] a_ext, b_ext;
    logic [35:0] p_full;
    assign a_ext = {{{{18-XW{{x[XW-1]}}}}, x}};
    assign b_ext = {{{{18-CW{{h[CW-1]}}}}, h}};
    MULT18X18D #(
        .REG_INPUTA_CLK("{inr}"), .REG_INPUTB_CLK("{inr}"), .REG_PIPELINE_CLK("{pipe}"), .REG_OUTPUT_CLK("{outr}"),
        .REG_INPUTA_CE("CE0"), .REG_INPUTB_CE("CE0"), .REG_PIPELINE_CE("CE0"), .REG_OUTPUT_CE("CE0"),
        .REG_INPUTA_RST("RST0"), .REG_INPUTB_RST("RST0"), .REG_PIPELINE_RST("RST0"), .REG_OUTPUT_RST("RST0"),
        .GSR("DISABLED"), .RESETMODE("SYNC"), .CAS_MATCH_REG("FALSE"), .SOURCEB_MODE("B_SHIFT"), .MULT_BYPASS("DISABLED")
    ) u_mult (
        {A},
        {B},
        {C},
        .SIGNEDA(1'b1), .SIGNEDB(1'b1), .SOURCEA(1'b0), .SOURCEB(1'b0),
        .CLK0(clk), .CE0(1'b1), .RST0(1'b0),
        {P}
    );
    assign m = p_full[XW+CW-1:0];
"""


def _coef_snippets(a: FirArch, U: int, coef_lit: str) -> tuple[str, str]:
    """(extra ports, storage declaration) for the coefficient set: constants, or a write-port register file."""
    if not a.coef_port:
        return "", f"    localparam logic signed [CW-1:0] COEFS [U] = '{{{coef_lit}}};"
    ports = """,
    input  wire                   coef_we,      // write one coefficient: COEFS[coef_addr] <= coef_data
    input  wire [$clog2(U)-1:0]   coef_addr,
    input  wire signed [CW-1:0]   coef_data"""
    store = f"""    // runtime-writable coefficients (reset to the generated lowpass); U = {U} unique taps
    logic signed [CW-1:0] COEFS [U];
    localparam logic signed [CW-1:0] COEFS_INIT [U] = '{{{coef_lit}}};
    always_ff @(posedge clk) begin
        if (rst) for (int i = 0; i < U; i++) COEFS[i] <= COEFS_INIT[i];
        else if (coef_we) COEFS[coef_addr] <= coef_data;
    end"""
    return ports, store


def _mult_module(a: FirArch, backend: str) -> str:
    """`fir_mult`: prod = x * h with the configured DSP/fabric register stages.
    h may be a constant parameter (parallel forms) or a runtime input (folded ROM)."""
    lat = DSP_REG_MODES[a.dsp_regs]
    style = _style(backend)
    attr = '(* use_dsp = "yes" *)\n' if style == "vivado" else ""
    if lat == 0:
        body = "    assign m = x * h;\n"
    elif style == "ecp5":
        # yosys neither packs pipeline registers into MULT18X18D nor reliably infers it for
        # constant-coefficient products, so instantiate the primitive explicitly (as matmul does)
        body = _ecp5_dsp_body(a.dsp_regs)
    elif lat == 1:
        body = """    logic signed [XW+CW-1:0] m_r;
    always_ff @(posedge clk) m_r <= x * h;
    assign m = m_r;
"""
    else:
        body = f"""    logic signed [XW-1:0]    x_r;
    logic signed [CW-1:0]    h_r;
    logic signed [XW+CW-1:0] m_q [{lat - 1}];
    always_ff @(posedge clk) begin
        x_r <= x;
        h_r <= h;
        m_q[0] <= x_r * h_r;
        for (int s = 1; s < {lat - 1}; s++) m_q[s] <= m_q[s-1];
    end
    assign m = m_q[{lat - 2}];
"""
    P = a.pipe_mul
    pipe = ("    assign prod = m;\n" if P == 0 else f"""    logic signed [XW+CW-1:0] pq [{P}];
    always_ff @(posedge clk) begin
        pq[0] <= m;
        for (int s = 1; s < {P}; s++) pq[s] <= pq[s-1];
    end
    assign prod = pq[{P - 1}];
""")
    return f"""{attr}module fir_mult #(
    parameter int XW = 17,
    parameter int CW = 16
) (
    input  wire                     clk,
    input  wire signed [XW-1:0]     x,
    input  wire signed [CW-1:0]     h,
    output logic signed [XW+CW-1:0] prod
);
    logic signed [XW+CW-1:0] m;
{body}{pipe}endmodule
"""


def _delay_chain(ml: int, outs: list[tuple[str, str]]) -> str:
    """`out <= in` delayed by ml cycles (with sync reset) for each (out, in) pair; ml may be 0."""
    if ml == 0:
        return "\n".join(f"    assign {o} = {i};" for o, i in outs)
    lines = []
    for o, i in outs:
        q = f"{o}_q"
        shift = f"{{{q}[{ml - 2}:0], {i}}}" if ml > 1 else i
        lines.append(f"    logic [{ml - 1}:0] {q};")
        lines.append(f"    always_ff @(posedge clk) {q} <= rst ? '0 : {shift};")
        lines.append(f"    assign {o} = {q}[{ml - 1}];")
    return "\n".join(lines)


def emit_fir(a: FirArch, backend: str = "ecp5") -> str:
    header = f"// Generated by silicon-agent -- do not edit. IR: {json.dumps(a.to_dict())}\n`default_nettype none\n\ntypedef logic signed [63:0] sd;\n\n"
    body = _emit_transposed(a, backend) if a.fold == 1 else _emit_folded(a, backend)
    return header + _mult_module(a, backend) + "\n" + body + "\n`default_nettype wire\n"


# ---------------------------------------------------------------- fold == 1: transposed form
def _emit_transposed(a: FirArch, backend: str) -> str:
    N, U, DW, CW, AW = a.taps, a.unique_taps, a.data_width, a.coef_width, a.acc_width
    ML = a.mul_latency
    reg_in = 1 if a.reg_in else 0
    acc_attr = '(* use_dsp = "yes" *)' if a.acc_in_dsp else '(* use_dsp = "no" *)'
    coef_lit = _coef_literal(list(a.coefs[:U]))
    # stage s of the transposed chain adds the product for tap (N-1-s); with sharing,
    # tap t and tap N-1-t use multiplier min(t, N-1-t)
    vpipe = _delay_chain(ML, [("v_m", "v0")])
    if a.symmetric:
        inject = "assign pr[st] = prod[(N-1-st) < st ? (N-1-st) : st];"
    else:
        inject = "assign pr[st] = prod[N-1-st];"
    coef_ports, coef_store = _coef_snippets(a, U, coef_lit)
    return f"""module fir_top #(
    parameter int N  = {N},
    parameter int U  = {U},
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
    output logic signed [AW-1:0]  out_data{coef_ports}
);
    localparam int ML = {ML};
{coef_store}
    assign in_ready = 1'b1;

    // ---- input register (optional) ----
    logic signed [DW-1:0] x0;
    logic                 v0;
    generate
        if ({reg_in}) begin : g_reg_in
            always_ff @(posedge clk) begin x0 <= in_data; v0 <= in_valid & ~rst; end
        end else begin : g_no_reg_in
            assign x0 = in_data;
            assign v0 = in_valid & ~rst;
        end
    endgenerate

    // ---- valid travels alongside the multiplier pipeline ----
    logic v_m;
{vpipe}

    // ---- U multipliers, x broadcast ----
    logic signed [DW+CW-1:0] prod [U];
    generate
        for (genvar k = 0; k < U; k++) begin : g_mul
            fir_mult #(.XW(DW), .CW(CW)) u_m (.clk(clk), .x(x0), .h(COEFS[k]), .prod(prod[k]));
        end
    endgenerate

    // ---- transposed sum chain: sum_r[0] = h[N-1]*x, sum_n[N-1] = y ----
    logic signed [DW+CW-1:0] pr    [N];
    logic signed [AW-1:0]    sum_r [N];
    {acc_attr} logic signed [AW-1:0] sum_n [N];
    generate
        for (genvar st = 0; st < N; st++) begin : g_stage
            {inject}
            if (st == 0) begin : g_first
                assign sum_n[st] = AW'(pr[st]);
            end else begin : g_rest
                assign sum_n[st] = sum_r[st-1] + AW'(pr[st]);
            end
            always_ff @(posedge clk) begin
                if (rst)      sum_r[st] <= '0;
                else if (v_m) sum_r[st] <= sum_n[st];
            end
        end
    endgenerate

    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_data  <= '0;
        end else begin
            out_valid <= v_m;
            out_data  <= sum_n[N-1];
        end
    end
endmodule
"""


# ---------------------------------------------------------------- fold > 1: direct form, time-multiplexed
_SUM_CHAIN = """    logic part_v;
    always_ff @(posedge clk) part_v <= sv_m & sl_m & ~rst;
    logic signed [AW-1:0] total;
    always_comb begin
        total = '0;
        for (int m = 0; m < M; m++) total += part[m];
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_data  <= '0;
        end else begin
            out_valid <= part_v;
            out_data  <= total;
        end
    end"""


def _sum_tree(levels: int) -> str:
    """Registered balanced reduction of part[0..M-1]: level l holds ceil(M/2^l) sums; each level is one register stage,
    so the longest adder on the output side is one AW-bit add. The valid travels alongside. Level 0 reads `part`
    directly; levels 1..SL are one register array (iverilog refuses an unpacked array driven by both assigns and
    always blocks)."""
    return f"""    localparam int SL = {levels};
    // sum_r[l][i] = sum of part[2^l*i .. 2^l*(i+1)-1] for l >= 1; level SL has one entry
    logic signed [AW-1:0] sum_r [SL+1][M];
    logic                 vld_r [SL+1];
    logic                 part_v;
    always_ff @(posedge clk) part_v <= sv_m & sl_m & ~rst;
    generate
        for (genvar l = 1; l <= SL; l++) begin : g_lvl
            localparam int NI = (M + (1 << l) - 1) >> l;              // entries at this level
            localparam int NP = (M + (1 << (l - 1)) - 1) >> (l - 1);  // entries at the previous level
            if (l == 1) begin : g_v1
                always_ff @(posedge clk) vld_r[l] <= part_v;
            end else begin : g_vn
                always_ff @(posedge clk) vld_r[l] <= vld_r[l-1];
            end
            for (genvar i = 0; i < NI; i++) begin : g_node
                if (l == 1 && 2 * i + 1 < NP) begin : g_pair0
                    always_ff @(posedge clk) sum_r[l][i] <= part[2*i] + part[2*i+1];
                end else if (l == 1) begin : g_pass0
                    always_ff @(posedge clk) sum_r[l][i] <= part[2*i];
                end else if (2 * i + 1 < NP) begin : g_pair
                    always_ff @(posedge clk) sum_r[l][i] <= sum_r[l-1][2*i] + sum_r[l-1][2*i+1];
                end else begin : g_pass
                    always_ff @(posedge clk) sum_r[l][i] <= sum_r[l-1][2*i];
                end
            end
        end
    endgenerate
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_data  <= '0;
        end else begin
            out_valid <= vld_r[SL];
            out_data  <= sum_r[SL][0];
        end
    end"""

def _emit_folded(a: FirArch, backend: str) -> str:
    N, U, F, DW, CW, AW = a.taps, a.unique_taps, a.fold, a.data_width, a.coef_width, a.acc_width
    M = U // F
    ML = a.mul_latency
    reg_in = 1 if a.reg_in else 0
    sym = 1 if a.symmetric else 0
    XW = DW + 1  # pre-add grows by one bit
    acc_attr = '(* use_dsp = "yes" *)' if a.acc_in_dsp else '(* use_dsp = "no" *)'
    coef_lit = _coef_literal(list(a.coefs[:U]))
    jw = max(1, math.ceil(math.log2(F)))
    svpipe = _delay_chain(ML, [("sv_m", "step_v"), ("sl_m", "step_last")])
    coef_ports, coef_store = _coef_snippets(a, U, coef_lit)
    reduction = _sum_tree(a.sum_levels) if a.sum_levels else _SUM_CHAIN
    # pipe_in: the pre-adder + tap mux output is registered; the step valid chain (ML includes this stage) lines up
    operand_regs = ("            always_ff @(posedge clk) begin xm <= t[m*F + int'(j)]; hm <= COEFS[m*F + int'(j)]; end" if a.pipe_in else
                    "            assign xm = t[m*F + int'(j)];\n            assign hm = COEFS[m*F + int'(j)];")
    return f"""module fir_top #(
    parameter int N  = {N},
    parameter int U  = {U},
    parameter int F  = {F},
    parameter int M  = {M},
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
    output logic signed [AW-1:0]  out_data{coef_ports}
);
    localparam int ML = {ML};
    localparam int XW = {XW};
    localparam int JW = {jw};
{coef_store}

    // ---- handshake: in_ready is a pure function of state (no path from in_valid),
    //      so reg_in=0 has no combinational loop. accept -> (optional input reg) ->
    //      v0 shifts the delay line and starts the F-step sweep. ----
    logic          busy;
    logic          pend;          // accepted, sample still in the input register (reg_in=1)
    logic [JW-1:0] j;
    logic          accept;
    logic          step_v;        // a MAC step is issued this cycle
    logic          step_last;
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
            busy <= 1'b0;
            j    <= '0;
        end else if (v0) begin
            busy <= 1'b1;
            j    <= '0;
        end else if (busy) begin
            j <= j + 1'b1;
            if (j == JW'(F - 1)) busy <= 1'b0;
        end
    end
    assign step_v    = busy;
    assign step_last = busy && (j == JW'(F - 1));

    // ---- delay line, shifts on accept; x_d[k] = x[n-k] while sweeping ----
    logic signed [DW-1:0] x_d [N];
    always_ff @(posedge clk) begin
        if (rst) begin
            for (int k = 0; k < N; k++) x_d[k] <= '0;
        end else if (v0) begin
            x_d[0] <= x0;
            for (int k = 1; k < N; k++) x_d[k] <= x_d[k-1];
        end
    end

    // ---- tap operand t[k]: direct, or symmetric pre-add x[n-k] + x[n-(N-1-k)] ----
    logic signed [XW-1:0] t [U];
    generate
        for (genvar k = 0; k < U; k++) begin : g_t
            if ({sym} && (N - 1 - k) != k) begin : g_pair
                assign t[k] = XW'(x_d[k]) + XW'(x_d[N-1-k]);
            end else begin : g_single
                assign t[k] = XW'(x_d[k]);
            end
        end
    endgenerate

    // ---- step valid / last travel alongside the multiplier pipeline ----
    logic sv_m, sl_m;
{svpipe}

    // ---- M MACs, each sweeping taps m*F .. m*F+F-1 ----
    logic signed [XW+CW-1:0] prod [M];
    logic signed [AW-1:0]    acc  [M];
    logic signed [AW-1:0]    part [M];
    {acc_attr} logic signed [AW-1:0] acc_n [M];
    generate
        for (genvar m = 0; m < M; m++) begin : g_mac
            logic signed [XW-1:0] xm;
            logic signed [CW-1:0] hm;
{operand_regs}
            fir_mult #(.XW(XW), .CW(CW)) u_m (.clk(clk), .x(xm), .h(hm), .prod(prod[m]));
            assign acc_n[m] = acc[m] + AW'(prod[m]);
            always_ff @(posedge clk) begin
                if (rst) begin
                    acc[m]  <= '0;
                    part[m] <= '0;
                end else if (sv_m) begin
                    if (sl_m) begin
                        part[m] <= acc_n[m];
                        acc[m]  <= '0;
                    end else begin
                        acc[m]  <= acc_n[m];
                    end
                end
            end
        end
    endgenerate

    // ---- final reduction of the M partial sums ----
{reduction}
endmodule
"""
