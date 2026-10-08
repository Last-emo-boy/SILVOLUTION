"""FFT: streaming radix-2 single-path delay-feedback (SDF) pipeline, N-point complex, fixed point.

Knobs:
  n           int 16..4096 (power of 2)   transform length (task parameter)
  data_width  int 8..24                   input real/imag bits (task parameter)
  tw_width    int 8..18                   twiddle factor bits
  growth      str scale|grow              scale: divide by 2 at every stage (output = FFT/N, no overflow);
                                          grow: carry log2(N) extra bits so output = exact FFT (wider datapath)
  tw_mem      str lut|bram                twiddle ROM in LUTs or block RAM (per stage, N/2 entries for stage 0)
  delay_mem   str auto|lut|bram           delay-feedback buffers (N/2, N/4, ... deep): block RAM above `bram_min`
  bram_min    int 16..512                 delay lines at least this deep go to BRAM when delay_mem=auto
  mul_regs    int 0..3                    pipeline registers around each stage's complex multiplier
  bf_regs     int 0|1                     register the butterfly output
  dsp_regs    str                         as elsewhere (register stages inside/after the multiplier)
  cmul        str four|three|lut          complex multiplier: 4 real multipliers (DSPs), the 3-multiplier Gauss form
                                          (3 DSPs + 5 adders, one more adder level), or LUT-only (no DSPs at all)

Structure: log2(N) stages, each one radix-2 butterfly with a delay line of N/2^(s+1) samples and (except the last)
one complex multiplier (3 or 4 real multipliers -> DSPs). Throughput 1 sample/cycle in, 1 out, bit-reversed order
(a `bit_reverse` flag reorders at the output with an extra N-sample buffer). Latency ~ N + stage pipeline depth.

Golden model: fixed-point simulation of the exact same datapath (per-stage rounding/scaling), so simulation
checks bit-exactness against the structure the generator emits, not against a float FFT.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass

from .ir import DSP_REG_MODES


def twiddles(n: int, width: int) -> list[tuple[int, int]]:
    """W_n^k = exp(-2 pi i k / n) for k in 0..n/2-1, as (re, im) in Q(width-1) with symmetric saturation."""
    scale = (1 << (width - 1)) - 1
    out = []
    for k in range(n // 2):
        ang = -2 * math.pi * k / n
        out.append((int(round(math.cos(ang) * scale)), int(round(math.sin(ang) * scale))))
    return out


@dataclass(frozen=True)
class FftArch:
    n: int = 256
    data_width: int = 16
    tw_width: int = 16
    growth: str = "scale"
    tw_mem: str = "lut"
    delay_mem: str = "auto"
    bram_min: int = 64
    mul_regs: int = 1
    bf_regs: int = 1
    dsp_regs: str = "none"
    bit_reverse: bool = False
    cmul: str = "four"

    @property
    def stages(self) -> int:
        return int(math.log2(self.n))

    @property
    def out_width(self) -> int:
        return self.data_width + (self.stages if self.growth == "grow" else 0)

    def validate(self) -> list[str]:
        errs = []
        if not (16 <= self.n <= 4096 and self.n & (self.n - 1) == 0):
            errs.append("n must be a power of two in 16..4096")
        if not 8 <= self.data_width <= 24:
            errs.append("data_width must be in 8..24")
        if not 8 <= self.tw_width <= 18:
            errs.append("tw_width must be in 8..18")
        if self.growth not in ("scale", "grow"):
            errs.append("growth must be scale or grow")
        if self.growth == "grow" and self.out_width > 40:
            errs.append("grow would exceed 40 output bits; use scale or a narrower input")
        if self.tw_mem not in ("lut", "bram"):
            errs.append("tw_mem must be lut or bram")
        if self.delay_mem not in ("auto", "lut", "bram"):
            errs.append("delay_mem must be auto, lut or bram")
        if not 16 <= self.bram_min <= 512:
            errs.append("bram_min must be in 16..512")
        if not 0 <= self.mul_regs <= 3:
            errs.append("mul_regs must be in 0..3")
        if self.bf_regs not in (0, 1):
            errs.append("bf_regs must be 0 or 1")
        if self.bf_regs == 0 and self.mul_regs + DSP_REG_MODES.get(self.dsp_regs, 0) == 0:
            errs.append("a stage needs at least one register (bf_regs=1 or mul_regs>0): a combinational cascade is not a pipeline")
        if self.dsp_regs not in DSP_REG_MODES:
            errs.append(f"dsp_regs must be one of {sorted(DSP_REG_MODES)}")
        elif self.mul_regs + DSP_REG_MODES[self.dsp_regs] > 3:
            errs.append("mul_regs + dsp_regs stages must not exceed 3")
        if self.cmul not in ("four", "three", "lut"):
            errs.append("cmul must be four, three or lut")
        return errs

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FftArch":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def digest(self, backend: str = "ecp5") -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:12]


def throughput(a: FftArch) -> float:
    return 1.0


def latency_cycles(a: FftArch) -> int:
    per_stage = a.bf_regs + max(a.mul_regs + DSP_REG_MODES[a.dsp_regs] - 1, 0)
    return a.n - 1 + a.stages * per_stage + (a.n + 1 if a.bit_reverse else 0)


def _delay_is_bram(a: FftArch, depth: int) -> bool:
    return a.delay_mem == "bram" or (a.delay_mem == "auto" and depth >= a.bram_min)


# ---------------------------------------------------------------- golden: same fixed-point datapath
def _sat(v: int, w: int) -> int:
    """Two's-complement wrap to w bits, exactly like the RTL's WOUT'(...) truncation."""
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


def golden(a: FftArch, frames: list[list[tuple[int, int]]]) -> list[tuple[int, int]]:
    """Cycle model of the SDF pipeline on the concatenated frames (back-to-back), bit-exact in fixed point.
    Returns the output stream *including the pipeline's N-1 sample startup*: the testbench drops the first
    N-1 outputs (they are masked as invalid by the hardware) -- here we simply return the meaningful outputs
    of every frame in stream order (bit-reversed positions), which is what the DUT emits in order."""
    N, S, TW = a.n, a.stages, a.tw_width
    stream = [c for f in frames for c in f] + [(0, 0)] * N   # a zero frame flushes the last real frame out
    w = a.data_width
    for s in range(S):
        half = N >> (s + 1)
        tw = twiddles(2 * half, TW)
        w_next = w + 1 if a.growth == "grow" else w
        line = [(0, 0)] * half
        ptr = 0
        out = []
        for t, (xr, xi) in enumerate(stream):
            pos = t % (2 * half); second = pos >= half; k = pos % half
            dr, di = line[ptr]
            if second:
                sr, si = dr + xr, di + xi
                fr, fi = dr - xr, di - xi
                if a.growth == "scale":
                    sr, si, fr, fi = sr >> 1, si >> 1, fr >> 1, fi >> 1
                line[ptr] = (_sat(fr, w_next), _sat(fi, w_next))
                out.append((_sat(sr, w_next), _sat(si, w_next)))
            else:
                line[ptr] = (_sat(xr, w_next), _sat(xi, w_next))
                if k == 0 or s == S - 1:
                    out.append((dr, di))
                else:
                    cr, ci = tw[k]
                    pr = (dr * cr - di * ci + (1 << (TW - 2))) >> (TW - 1)
                    pi = (dr * ci + di * cr + (1 << (TW - 2))) >> (TW - 1)
                    out.append((_sat(pr, w_next), _sat(pi, w_next)))
            ptr = (ptr + 1) % half
        stream, w = out, w_next
    # meaningful outputs start N-1 samples after the first input; take exactly len(frames)*N of them
    outs = stream[N - 1: N - 1 + len(frames) * N]
    if a.bit_reverse:
        nat = []
        for f in range(len(frames)):
            blk = outs[f * N:(f + 1) * N]
            nat.extend(blk[int(f"{i:0{S}b}"[::-1], 2)] for i in range(N))
        return nat
    return outs


SDF_STAGE = r'''// One SDF stage, cycle-exact to this model:
//   d = line[ptr]                      (written HALF valid samples ago)
//   second half of the block: line[ptr] = d - x; out = d + x
//   first half:               line[ptr] = x;     out = d * W   (d is the previous block's a-b)
// The delay line is a HALF-deep circular buffer advanced on every valid sample. Outputs during the first
// half of the very first block are not meaningful (nothing was written); `primed` masks them.
module sdf_stage #(parameter int WIN = 16, WOUT = 16, HALF = 128, LOGH = 7, KW = 7, TW = 16,
                   MREG = 1, BFREG = 1, SCALE = 1, BRAM = 1, LAST = 0, CMUL = 0) (
    input  wire clk, input wire rst, input wire in_valid, input wire signed [WIN-1:0] in_re, in_im, input wire in_first,
    output logic out_valid, output logic signed [WOUT-1:0] out_re, out_im, output logic out_first,
    output logic [KW-1:0] tw_k, input wire signed [TW-1:0] cr, ci);
    localparam int PW = WOUT + TW;
    // block phase: every stage counts from the same frame start (the stream positions line up because each
    // stage emits exactly one sample per input sample); in_first resets the phase, forwarded unchanged
    logic [LOGH:0] pos, cur_pos;
    assign cur_pos = in_first ? '0 : pos;
    logic second; assign second = cur_pos[LOGH];
    logic [KW-1:0] k;
    generate if (LOGH == 0) begin : g_k0 assign k = '0; end else begin : g_kn assign k = cur_pos[LOGH-1:0]; end endgenerate
    assign tw_k = k;
    logic [KW-1:0] ptr;
    always_ff @(posedge clk) begin
        if (rst) begin pos <= '0; ptr <= '0; end
        else if (in_valid) begin
            pos <= cur_pos + 1'b1;
            ptr <= (HALF == 1 || ptr == HALF-1) ? '0 : ptr + 1'b1;
        end
    end
    // delay line read (d) and write (wb) at the same ptr: read-before-write
    logic signed [WOUT-1:0] d_re, d_im, wb_re, wb_im;
    generate if (BRAM) begin : g_bram
        (* ram_style = "block" *) logic [2*WOUT-1:0] mem [0:HALF-1];
        logic [2*WOUT-1:0] q;
        initial begin for (int i = 0; i < HALF; i++) mem[i] = '0; q = '0; end
        logic [KW-1:0] nptr; assign nptr = (HALF == 1 || ptr == HALF-1) ? '0 : ptr + 1'b1;
        // synchronous read of the *next* address so q holds mem[ptr] when the sample at ptr arrives; a frame
        // start re-reads nothing special because the line content is simply whatever was there (masked by primed)
        always_ff @(posedge clk) if (in_valid) begin mem[ptr] <= {wb_re, wb_im}; q <= mem[nptr]; end
        // bypass: if the next address equals the one being written this cycle (HALF == 1), forward the write
        if (HALF == 1) begin : g_h1
            logic [2*WOUT-1:0] q1; always_ff @(posedge clk) if (in_valid) q1 <= {wb_re, wb_im};
            assign {d_re, d_im} = q1;
        end else begin : g_hn
            assign {d_re, d_im} = q;
        end
    end else begin : g_lut
        logic [2*WOUT-1:0] sr [0:HALF-1];
        initial for (int i = 0; i < HALF; i++) sr[i] = '0;
        always_ff @(posedge clk) if (in_valid) begin sr[0] <= {wb_re, wb_im}; for (int i = 1; i < HALF; i++) sr[i] <= sr[i-1]; end
        assign {d_re, d_im} = sr[HALF-1];
    end endgenerate
    // butterfly (second half) with optional /2
    logic signed [WIN:0] sum_re, sum_im, dif_re, dif_im;
    logic signed [WOUT-1:0] s_re, s_im, df_re, df_im;
    always_comb begin
        sum_re = (WIN+1)'(d_re) + (WIN+1)'(in_re); sum_im = (WIN+1)'(d_im) + (WIN+1)'(in_im);
        dif_re = (WIN+1)'(d_re) - (WIN+1)'(in_re); dif_im = (WIN+1)'(d_im) - (WIN+1)'(in_im);
        if (SCALE) begin s_re = WOUT'(sum_re >>> 1); s_im = WOUT'(sum_im >>> 1); df_re = WOUT'(dif_re >>> 1); df_im = WOUT'(dif_im >>> 1); end
        else       begin s_re = WOUT'(sum_re); s_im = WOUT'(sum_im); df_re = WOUT'(dif_re); df_im = WOUT'(dif_im); end
    end
    assign wb_re = second ? df_re : WOUT'(in_re);
    assign wb_im = second ? df_im : WOUT'(in_im);
    // first half: the value leaving the line is the previous block's a-b; multiply by W (k = 0 -> pass through)
    logic signed [PW-1:0] p_re, p_im;
    generate if (CMUL == 1) begin : g_three
        // Gauss: k1 = cr*(d_re + d_im), k2 = d_re*(ci - cr), k3 = d_im*(cr + ci); re = k1 - k3, im = k1 + k2
        logic signed [WOUT:0] s_d; logic signed [TW:0] s_c1, s_c2;
        logic signed [PW:0] k1, k2, k3;
        always_comb begin
            s_d = (WOUT+1)'(d_re) + (WOUT+1)'(d_im); s_c1 = (TW+1)'(ci) - (TW+1)'(cr); s_c2 = (TW+1)'(cr) + (TW+1)'(ci);
            k1 = s_d * cr; k2 = d_re * s_c1; k3 = d_im * s_c2;
            p_re = PW'(k1 - k3); p_im = PW'(k1 + k2);
        end
    end else if (CMUL == 2) begin : g_lmul
        // shift-and-add written out as partial-product adders so no synthesis tool recognises a $mul: fabric
        // adders only, no DSP. Four products: rr = d_re*cr, ii = d_im*ci, ri = d_re*ci, ir = d_im*cr; the twiddle's
        // top bit is its sign, so that partial product is subtracted
        logic signed [PW-1:0] acc_rr [0:TW], acc_ii [0:TW], acc_ri [0:TW], acc_ir [0:TW];
        logic [TW-1:0] crb, cib; assign crb = cr; assign cib = ci;
        assign acc_rr[0] = '0; assign acc_ii[0] = '0; assign acc_ri[0] = '0; assign acc_ir[0] = '0;
        for (genvar i = 0; i < TW; i++) begin : g_pp
            logic signed [PW-1:0] sre, sim;
            assign sre = PW'(d_re) <<< i; assign sim = PW'(d_im) <<< i;
            if (i == TW-1) begin : gs
                assign acc_rr[i+1] = acc_rr[i] - (crb[i] ? sre : '0); assign acc_ii[i+1] = acc_ii[i] - (cib[i] ? sim : '0);
                assign acc_ri[i+1] = acc_ri[i] - (cib[i] ? sre : '0); assign acc_ir[i+1] = acc_ir[i] - (crb[i] ? sim : '0);
            end else begin : gu
                assign acc_rr[i+1] = acc_rr[i] + (crb[i] ? sre : '0); assign acc_ii[i+1] = acc_ii[i] + (cib[i] ? sim : '0);
                assign acc_ri[i+1] = acc_ri[i] + (cib[i] ? sre : '0); assign acc_ir[i+1] = acc_ir[i] + (crb[i] ? sim : '0);
            end
        end
        assign p_re = acc_rr[TW] - acc_ii[TW];
        assign p_im = acc_ri[TW] + acc_ir[TW];
    end else begin : g_four
        always_comb begin
            p_re = d_re * cr - d_im * ci;
            p_im = d_re * ci + d_im * cr;
        end
    end endgenerate
    logic signed [WOUT-1:0] m_re, m_im;
    always_comb begin
        m_re = (LAST || k == 0) ? d_re : WOUT'((p_re + (PW'(1) <<< (TW-2))) >>> (TW-1));
        m_im = (LAST || k == 0) ? d_im : WOUT'((p_im + (PW'(1) <<< (TW-2))) >>> (TW-1));
    end
    // output of this cycle, then MREG pipeline registers (the multiplier is retimed into them by synthesis)
    logic signed [WOUT-1:0] o_re, o_im; logic o_v, o_f;
    always_comb begin
        o_re = second ? s_re : m_re;
        o_im = second ? s_im : m_im;
        o_v  = in_valid & ~rst;
        o_f  = in_valid & in_first;
    end
    logic signed [WOUT-1:0] or_q [0:3], oi_q [0:3]; logic ov_q [0:3], of_q [0:3];
    initial for (int i = 0; i < 4; i++) begin or_q[i] = '0; oi_q[i] = '0; end
    always_ff @(posedge clk) begin
        or_q[0] <= o_re; oi_q[0] <= o_im; ov_q[0] <= o_v; of_q[0] <= o_f;
        for (int i = 1; i < 4; i++) begin or_q[i] <= or_q[i-1]; oi_q[i] <= oi_q[i-1]; ov_q[i] <= ov_q[i-1]; of_q[i] <= of_q[i-1]; end
    end
    initial for (int i = 0; i < 4; i++) begin ov_q[i] = 0; of_q[i] = 0; end
    localparam int D = (MREG > 3 ? 3 : MREG);
    generate if (D == 0 && BFREG == 0) begin : g_comb
        assign out_re = o_re; assign out_im = o_im; assign out_valid = o_v; assign out_first = o_f;
    end else if (D == 0) begin : g_reg1
        assign out_re = or_q[0]; assign out_im = oi_q[0]; assign out_valid = ov_q[0]; assign out_first = of_q[0];
    end else begin : g_regn
        localparam int DD = D - 1 + BFREG;
        assign out_re = or_q[DD]; assign out_im = oi_q[DD]; assign out_valid = ov_q[DD]; assign out_first = of_q[DD];
    end endgenerate
endmodule
'''


# ---------------------------------------------------------------- emitter
def emit_fft(a: FftArch, backend: str = "ecp5") -> str:
    N, S, DW, TW = a.n, a.stages, a.data_width, a.tw_width
    from .backends import BACKENDS
    style = BACKENDS[backend].emit_style if backend in BACKENDS else "behavioral"
    use_dsp = '(* use_dsp = "yes" *) ' if style == "vivado" else ""
    OW = a.out_width
    tw_attr = '(* rom_style = "block" *) ' if a.tw_mem == "bram" else '(* rom_style = "distributed" *) '
    stages = []
    rom_mods = []
    for s in range(S):
        half = N >> (s + 1)
        win = DW + (s if a.growth == "grow" else 0)
        wout = win + (1 if a.growth == "grow" else 0)
        bram = _delay_is_bram(a, half)
        mreg = a.mul_regs + DSP_REG_MODES[a.dsp_regs]
        tw = twiddles(2 * half, TW)   # stage s needs W_{2*half}^k, k < half
        twr = ", ".join(f"TW'(sd'({r}))" for r, _ in tw) or "TW'(0)"
        twi = ", ".join(f"TW'(sd'({i}))" for _, i in tw) or "TW'(0)"
        rom_mods.append(f"""
module fft_tw{s} #(parameter int TW = {TW}, LOGH = {max(1, int(math.log2(half)))}) (input wire [LOGH-1:0] k, output logic signed [TW-1:0] cr, ci);
    {tw_attr}localparam logic signed [TW-1:0] R [0:{max(half, 1) - 1}] = '{{{twr}}};
    {tw_attr}localparam logic signed [TW-1:0] I [0:{max(half, 1) - 1}] = '{{{twi}}};
    always_comb begin cr = R[k]; ci = I[k]; end
endmodule""")
        stages.append(f"""
    // ------------------------------------------------------------ stage {s}: delay {half}, width {win} -> {wout}
    logic [{max(1, int(math.log2(half))) - 1}:0] k{s}; logic signed [TW-1:0] cr{s}, ci{s};
    fft_tw{s} u_tw{s} (.k(k{s}), .cr(cr{s}), .ci(ci{s}));
    sdf_stage #(.WIN({win}), .WOUT({wout}), .HALF({half}), .LOGH({int(math.log2(half))}), .KW({max(1, int(math.log2(half)))}), .TW(TW),
                .MREG({mreg}), .BFREG({a.bf_regs}), .SCALE({1 if a.growth == "scale" else 0}), .BRAM({1 if bram else 0}), .LAST({1 if s == S - 1 else 0}), .CMUL({ {"four": 0, "three": 1, "lut": 2}[a.cmul] }))
        u_s{s} (.clk(clk), .rst(rst), .in_valid(v{s}), .in_re(r{s}), .in_im(i{s}), .in_first(f{s}),
                .out_valid(v{s + 1}), .out_re(r{s + 1}), .out_im(i{s + 1}), .out_first(f{s + 1}), .tw_k(k{s}), .cr(cr{s}), .ci(ci{s}));""")
    wires = "\n".join(f"    logic v{s}, f{s}; logic signed [{DW + (s if a.growth == 'grow' else 0) - 1}:0] r{s}, i{s};" for s in range(S + 1))
    reorder = ""
    if a.bit_reverse:
        reorder = f"""
    // ------------------------------------------------------------ output reorder: bit-reversed -> natural (N-sample ping-pong)
    logic [LOGN:0] warm;
    always_ff @(posedge clk) if (rst) warm <= '0; else if (v{S} && warm != N-1) warm <= warm + 1'b1;
    wire wv = v{S} & (warm == N-1);
    (* ram_style = "block" *) logic [2*OW-1:0] rbuf [0:2*N-1];
    logic [LOGN-1:0] wa, ra; logic wbank, rbank; logic [LOGN:0] pend;   // frames written but not yet read out
    logic rd_v; logic [2*OW-1:0] rq;
    wire rd = (pend != 0);   // a full frame is waiting: stream it out, one sample per clock, independent of input valid
    always_ff @(posedge clk) begin
        if (rst) begin wa <= '0; wbank <= 0; ra <= '0; rbank <= 0; pend <= '0; rd_v <= 0; end
        else begin
            if (wv) begin rbuf[{{wbank, wa}}] <= {{r{S}, i{S}}}; wa <= wa + 1'b1; if (wa == N-1) wbank <= ~wbank; end
            rd_v <= rd;
            if (rd) begin rq <= rbuf[{{rbank, bitrev(ra)}}]; ra <= ra + 1'b1; if (ra == N-1) rbank <= ~rbank; end
            pend <= pend + ((wv && wa == N-1) ? 1 : 0) - ((rd && ra == N-1) ? 1 : 0);
        end
    end
    function automatic [LOGN-1:0] bitrev(input [LOGN-1:0] v); for (int b = 0; b < LOGN; b++) bitrev[b] = v[LOGN-1-b]; endfunction
    assign out_valid = rd_v; assign {{out_re, out_im}} = rq;"""
    else:
        reorder = f"""
    // the pipeline's first N-1 outputs after reset are startup garbage (delay lines not yet filled): count them off once
    logic [LOGN:0] warm;
    always_ff @(posedge clk) if (rst) warm <= '0; else if (v{S} && warm != N-1) warm <= warm + 1'b1;
    assign out_valid = v{S} & (warm == N-1); assign out_re = r{S}; assign out_im = i{S};"""
    return f"""// Generated by silicon-agent -- do not edit. IR: {a.to_dict()}
`default_nettype none
typedef logic signed [63:0] sd;

module fft_top #(
    parameter int N  = {N},
    parameter int LOGN = {S},
    parameter int DW = {DW},
    parameter int TW = {TW},
    parameter int OW = {OW}
) (
    input  wire                  clk,
    input  wire                  rst,
    input  wire                  in_valid,
    input  wire                  in_first,     // first sample of a frame
    input  wire signed [DW-1:0]  in_re,
    input  wire signed [DW-1:0]  in_im,
    output logic                 in_ready,
    output logic                 out_valid,
    output logic signed [OW-1:0] out_re,
    output logic signed [OW-1:0] out_im
);
    assign in_ready = ~rst;
{wires}
    assign v0 = in_valid & ~rst; assign r0 = in_re; assign i0 = in_im; assign f0 = in_first;
{"".join(stages)}
{reorder}
endmodule
{"".join(rom_mods)}

{SDF_STAGE}
`default_nettype wire
"""
