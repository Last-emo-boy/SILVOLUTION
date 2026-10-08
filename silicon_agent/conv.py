"""2-D convolution (K x K window, single channel) over a raster-scan pixel stream: the BRAM-heavy design.

Architecture knobs (the IR the planner edits):
  k           int 3|5|7        kernel size (task parameter)
  width       int 16..2048     image width in pixels (task parameter; sets line-buffer depth)
  data_width  int 8|12|16      pixel bits (task parameter)
  coef_width  int 8|12|16      kernel coefficient bits (task parameter)
  acc_width   int              output/accumulator bits (>= dw+cw+ceil(log2(K*K)))
  line_mem    str bram|lut     line buffers in block RAM (K-1 rows of `width` pixels) or in LUT-based shift registers
  par_rows    int 1..K         rows of the window multiplied per cycle: K -> fully parallel (K*K multipliers,
                               1 pixel/cycle), 1 -> one row per cycle (K multipliers, 1/K pixel/cycle)
  tree_regs   int 0..3         pipeline registers in the adder tree
  pipe_mul    int 0..3         fabric register stages after the multipliers
  dsp_regs    str              DSP-internal registers, as in the other designs (emitted as fabric stages
                               on every device: the coefficient side is a ROM/mux, not a DSP cascade)

Window orientation: correlation form, out = sum_{r,c} coef[r*K+c] * px[R-K+1+r][C-K+1+c] (no kernel flip).

Interface: latency-insensitive stream of pixels in (in_valid/in_ready), one output per *valid interior* pixel
(out_valid/out_data); the first K-1 rows and K-1 columns of every frame produce no output (no padding), so a
frame of W x H pixels yields (W-K+1) x (H-K+1) outputs. `frame_start` marks the first pixel of a frame.
Coefficients are fixed at generation time (a task parameter) -- a Gaussian blur by default.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field

from .ir import DSP_REG_MODES


def gaussian_kernel(k: int, width: int = 8) -> list[int]:
    """Binomial (Pascal) kernel, row-major, quantized to `width`-bit signed and scaled so the sum is 2^(width-1)-1."""
    row = [math.comb(k - 1, i) for i in range(k)]
    g = [a * b for a in row for b in row]
    total = sum(g)
    scale = (1 << (width - 1)) - 1
    q = [max(1, int(round(v / total * scale))) for v in g]
    return q


@dataclass(frozen=True)
class ConvArch:
    k: int = 3
    width: int = 64
    data_width: int = 8
    coef_width: int = 8
    acc_width: int = 24
    line_mem: str = "bram"
    par_rows: int = 3
    tree_regs: int = 1
    pipe_mul: int = 0
    dsp_regs: str = "none"
    coefs: tuple[int, ...] = field(default_factory=lambda: tuple(gaussian_kernel(3)))

    @property
    def multipliers(self) -> int:
        return self.k * self.par_rows

    @property
    def mul_latency(self) -> int:
        return self.pipe_mul + DSP_REG_MODES[self.dsp_regs]

    @property
    def min_acc_width(self) -> int:
        return self.data_width + self.coef_width + math.ceil(math.log2(self.k * self.k)) + 1

    def validate(self) -> list[str]:
        errs = []
        if self.k not in (3, 5, 7):
            errs.append("k must be 3, 5 or 7")
        if not 16 <= self.width <= 2048:
            errs.append("width must be in 16..2048")
        if self.data_width not in (8, 12, 16):
            errs.append("data_width must be one of 8, 12, 16")
        if self.coef_width not in (8, 12, 16):
            errs.append("coef_width must be one of 8, 12, 16")
        if not self.min_acc_width <= self.acc_width <= 48:
            errs.append(f"acc_width must be in {self.min_acc_width}..48 for these widths and k")
        if self.line_mem not in ("bram", "lut"):
            errs.append("line_mem must be bram or lut")
        if not 1 <= self.par_rows <= self.k or self.k % self.par_rows:
            errs.append(f"par_rows must divide k ({self.k})")
        if not 0 <= self.tree_regs <= 3:
            errs.append("tree_regs must be in 0..3")
        if not 0 <= self.pipe_mul <= 3:
            errs.append("pipe_mul must be in 0..3")
        if self.dsp_regs not in DSP_REG_MODES:
            errs.append(f"dsp_regs must be one of {sorted(DSP_REG_MODES)}")
        if len(self.coefs) != self.k * self.k:
            errs.append(f"coefs has {len(self.coefs)} entries but k*k={self.k * self.k}")
        lo, hi = -(1 << (self.coef_width - 1)), (1 << (self.coef_width - 1)) - 1
        if any(not lo <= c <= hi for c in self.coefs):
            errs.append(f"coefficients exceed coef_width={self.coef_width}")
        return errs

    def to_dict(self) -> dict:
        d = asdict(self)
        d["coefs"] = list(self.coefs)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ConvArch":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "coefs"}
        k = int(known.get("k", 3))
        cw = int(known.get("coef_width", 8))
        coefs = d.get("coefs")
        known["coefs"] = tuple(int(c) for c in coefs) if coefs else tuple(gaussian_kernel(k, cw))
        return cls(**known)

    def digest(self, backend: str = "ecp5") -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:12]


def throughput(a: ConvArch) -> float:
    return round(a.par_rows / a.k, 4)


def latency_cycles(a: ConvArch) -> int:
    """Accept of the window's last pixel -> out_valid, in cycles."""
    steps = a.k // a.par_rows
    return 2 + a.mul_latency + a.tree_regs + steps + 1


# ---------------------------------------------------------------- emitter
def emit_conv(a: ConvArch, backend: str = "ecp5") -> str:
    K, W, DW, CW, AW, PR = a.k, a.width, a.data_width, a.coef_width, a.acc_width, a.par_rows
    STEPS = K // PR
    M = K * PR
    lat_mul = a.mul_latency
    from .backends import BACKENDS
    style = BACKENDS[backend].emit_style if backend in BACKENDS else "behavioral"
    use_dsp = '(* use_dsp = "yes" *)\n' if style == "vivado" else ""
    coef_lit = ", ".join(f"cw_t'({c})" for c in a.coefs)
    levels = math.ceil(math.log2(M)) if M > 1 else 0
    reg_at = {round((i + 1) * levels / (a.tree_regs + 1)) for i in range(a.tree_regs)} if a.tree_regs and levels else set()
    reg_at = {max(1, min(levels, r)) for r in reg_at}
    tree_lat = len(reg_at)
    D = lat_mul + tree_lat                       # mv -> tv pipeline depth
    step_at = "cur_step" if D == 0 else f"step_q[{D - 1}]"

    # the per-line output must be a plain register (not an element of an unpacked array, which yosys would
    # treat as another memory and then fail to find the BRAM's output flop)
    lb_decl = "\n".join(f"    logic [DW-1:0] lb_q{r};" for r in range(K - 1))
    lb_wires = "\n".join(f"    assign lb_q[{r}] = lb_q{r};" for r in range(K - 1))
    if a.line_mem == "bram":
        per_line = []
        for r in range(K - 1):
            din = "in_data" if r == 0 else f"lb_q{r - 1}"
            waddr = "cur_col" if r == 0 else "col_prev"
            per_line.append(f"""    (* ram_style = "block" *) logic [DW-1:0] lb_mem{r} [0:W-1];
    always_ff @(posedge clk) if (accept) lb_mem{r}[{waddr}] <= {din};
    always_ff @(posedge clk) if (accept) lb_q{r} <= lb_mem{r}[cur_col];""")
        lines = f"""    // K-1 line buffers, one simple-dual-port block RAM each (one write port, one read port).
    // Line 0 stores the incoming pixel at its column; line r>0 stores what line r-1 delivered for the
    // previous column, under that previous column. Every line is read at the current column, so
    // lb_q[r] is this column's pixel r+1 rows ago (read-before-write: the read sees the old content).
{lb_decl}
{chr(10).join(per_line)}
{lb_wires}
"""
    else:
        per_line = []
        for r in range(K - 1):
            din = "in_data" if r == 0 else f"lb_sr{r - 1}[W-1]"
            per_line.append(f"""    logic [DW-1:0] lb_sr{r} [0:W-1];
    always_ff @(posedge clk) if (accept) begin
        lb_sr{r}[0] <= {din};
        for (int i = 1; i < W; i++) lb_sr{r}[i] <= lb_sr{r}[i-1];
        lb_q{r} <= lb_sr{r}[W-1];
    end""")
        lines = f"""    // K-1 line buffers as LUT/FF shift registers: W stages + the output register per line
{lb_decl}
{chr(10).join(per_line)}
{lb_wires}
"""
    mul_body = (f"""    logic signed [PW-1:0] m_q [0:{lat_mul - 1}];
    always_ff @(posedge clk) begin
        m_q[0] <= x * h;
        for (int s = 1; s < {lat_mul}; s++) m_q[s] <= m_q[s-1];
    end
    assign m = m_q[{lat_mul - 1}];
""" if lat_mul > 0 else "    assign m = x * h;\n")
    mv_pipe = ("    wire mv_lat = mv;\n" if lat_mul == 0 else
               f"    logic [{lat_mul - 1}:0] mv_d;\n    always_ff @(posedge clk) mv_d <= " +
               (f"{{mv_d[{lat_mul - 2}:0], mv}};\n" if lat_mul > 1 else "mv;\n") + f"    wire mv_lat = mv_d[{lat_mul - 1}];\n")
    step_pipe = ("" if D == 0 else f"""    logic [$clog2(STEPS+1)-1:0] step_q [0:{D - 1}];
    always_ff @(posedge clk) begin
        step_q[0] <= cur_step;
        for (int s = 1; s < {D}; s++) step_q[s] <= step_q[s-1];
    end
""")
    if STEPS == 1:
        stepping = """    assign in_ready = ~rst;
    assign cur_step = '0;
    assign mv = win_v;
    assign rsel = '0;
"""
        emit_out = "            if (tv) begin out_data <= tsum; out_valid <= 1; end\n"
    else:
        stepping = f"""    // one accept per STEPS cycles: the window must stay still while its rows are multiplied in turn
    logic [$clog2(STEPS+1)-1:0] istep, ostep;
    always_ff @(posedge clk) begin
        if (rst) begin istep <= '0; ostep <= '0; end
        else begin
            if (accept) istep <= 1;
            else if (istep != 0) istep <= (istep == STEPS-1) ? '0 : istep + 1;
            if (win_v) ostep <= 1;
            else if (ostep != 0) ostep <= (ostep == STEPS-1) ? '0 : ostep + 1;
        end
    end
    assign in_ready = ~rst & (istep == 0);
    assign cur_step = win_v ? '0 : ostep;
    assign mv = win_v | (ostep != 0);
    assign rsel = cur_step * PR;
"""
        emit_out = f"""            if (tv) begin
                if ({step_at} == 0) acc <= tsum;
                else if ({step_at} == STEPS-1) begin out_data <= acc + tsum; out_valid <= 1; end
                else acc <= acc + tsum;
            end
"""
    return f"""// Generated by silicon-agent -- do not edit. IR: {a.to_dict()}
`default_nettype none

module conv2d_top #(
    parameter int K  = {K},
    parameter int W  = {W},
    parameter int DW = {DW},
    parameter int CW = {CW},
    parameter int AW = {AW},
    parameter int PR = {PR}
) (
    input  wire                  clk,
    input  wire                  rst,
    input  wire                  in_valid,
    input  wire                  frame_start,
    input  wire signed [DW-1:0]  in_data,
    output logic                 in_ready,
    output logic                 out_valid,
    output logic signed [AW-1:0] out_data
);
    localparam int PW = DW + CW;
    localparam int M  = K * PR;
    localparam int STEPS = K / PR;
    localparam int CB = $clog2(W);
    typedef logic signed [CW-1:0] cw_t;
    localparam cw_t COEF [0:K*K-1] = '{{{coef_lit}}};

    // ------------------------------------------------------------ handshake & raster position
    logic accept;
    assign accept = in_valid & in_ready;
    logic [CB-1:0] col, cur_col, cur_col_n, col_prev;
    logic [$clog2(K+1)-1:0] row_cnt;        // rows completed, saturating at K-1
    assign cur_col   = frame_start ? '0 : col;
    assign cur_col_n = (cur_col == W-1) ? '0 : cur_col + 1;
    logic signed [DW-1:0] px;
    logic                 px_v, row_ok_d;
    logic [CB-1:0]        col_d;
    always_ff @(posedge clk) begin
        if (rst) begin col <= '0; row_cnt <= '0; px_v <= 0; col_prev <= '0; end
        else begin
            px_v <= accept;
            if (accept) begin
                col <= cur_col_n;
                col_prev <= cur_col;
                px <= in_data;
                col_d <= cur_col;
                row_ok_d <= ~frame_start & (row_cnt == K-1);
                if (frame_start) row_cnt <= '0;
                else if (cur_col == W-1 && row_cnt != K-1) row_cnt <= row_cnt + 1;
            end
        end
    end

    logic [DW-1:0] lb_q [0:K-2];
{lines}
    // ------------------------------------------------------------ K x K window: win[r][c], r=0 oldest row, c=K-1 newest column
    logic signed [DW-1:0] win [0:K-1][0:K-1];
    always_ff @(posedge clk) begin
        if (px_v) begin
            for (int r = 0; r < K; r++)
                for (int c = 0; c < K-1; c++) win[r][c] <= win[r][c+1];
            for (int r = 0; r < K-1; r++) win[r][K-1] <= lb_q[K-2-r];
            win[K-1][K-1] <= px;
        end
    end
    logic win_v;
    always_ff @(posedge clk) begin
        if (rst) win_v <= 0;
        else     win_v <= px_v & row_ok_d & (col_d >= K-1);
    end

    // ------------------------------------------------------------ row stepping
    logic [$clog2(K+1)-1:0] rsel;
    logic [$clog2(STEPS+1)-1:0] cur_step;
    logic mv;
{stepping}
    // ------------------------------------------------------------ multipliers ({M} = {PR} row(s) x {K} taps)
    logic signed [PW-1:0] prod [0:M-1];
    genvar gr, gc;
    generate for (gr = 0; gr < PR; gr++) begin : g_r
        for (gc = 0; gc < K; gc++) begin : g_c
            logic signed [DW-1:0] x; logic signed [CW-1:0] h; logic signed [PW-1:0] m;
            always_comb begin
                x = win[rsel + gr][gc];
                h = COEF[(rsel + gr) * K + gc];
            end
            conv_mult #(.DW(DW), .CW(CW)) u_m (.clk(clk), .x(x), .h(h), .m(m));
            assign prod[gr*K + gc] = m;
        end
    end endgenerate
{mv_pipe}{step_pipe}
    // ------------------------------------------------------------ adder tree ({M} inputs, {tree_lat} register level(s))
    logic signed [AW-1:0] tsum;
    logic                 tv;
{_tree(M, reg_at, levels)}
    // ------------------------------------------------------------ accumulate over STEPS partial sums, emit
    logic signed [AW-1:0] acc;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; acc <= '0; end
        else begin
            out_valid <= 0;
{emit_out}        end
    end
endmodule

{use_dsp}module conv_mult #(parameter int DW = 8, parameter int CW = 8) (
    input wire clk, input wire signed [DW-1:0] x, input wire signed [CW-1:0] h, output logic signed [DW+CW-1:0] m);
    localparam int PW = DW + CW;
{mul_body}endmodule
`default_nettype wire
"""


def _tree(M: int, reg_at: set, levels: int) -> str:
    """Balanced binary adder tree over prod[0..M-1] into tsum, registered at the levels in reg_at;
    tv follows mv_lat through the same number of registers."""
    if M == 1:
        return "    assign tsum = AW'(prod[0]);\n    assign tv = mv_lat;\n"
    out, n, cur, lvl, valid = [], M, "prod", 0, "mv_lat"
    while n > 1:
        lvl += 1
        m = (n + 1) // 2
        nxt = f"t{lvl}"
        out.append(f"    logic signed [AW-1:0] {nxt} [0:{m - 1}];")
        reg = lvl in reg_at
        body = [f"{nxt}[{i}] {'<=' if reg else '='} AW'({cur}[{2 * i}])" + (f" + AW'({cur}[{2 * i + 1}])" if 2 * i + 1 < n else "") + ";" for i in range(m)]
        if reg:
            out.append("    always_ff @(posedge clk) begin")
            out += ["        " + b for b in body]
            out.append("    end")
            out.append(f"    logic v{lvl}; always_ff @(posedge clk) v{lvl} <= {valid};")
            valid = f"v{lvl}"
        else:
            out.append("    always_comb begin")
            out += ["        " + b for b in body]
            out.append("    end")
        cur, n = nxt, m
    out.append(f"    assign tsum = {cur}[0];")
    out.append(f"    assign tv = {valid};")
    return "\n".join(out) + "\n"
