"""NCO / DDS with complex mixer: the front end of a digital down-converter.

A phase accumulator of `phase_width` bits advances by a programmable frequency control word (FCW, a port) every
accepted sample; its top `lut_addr_width` bits address a quarter-wave sine table (`sin_width`-bit samples), with
sign/symmetry logic producing sin and cos. The input sample is multiplied by (cos, -sin) -- a complex mix down by
the NCO frequency -- giving I and Q outputs (`out_i`, `out_q`) of `out_width` bits.

Knobs:
  data_width       task  input sample bits
  phase_width      task  accumulator bits (frequency resolution = fs / 2^phase_width)
  sin_width        task  sine sample bits (spur floor ~ 6 dB/bit)
  lut_addr_width   int   bits addressing the quarter-wave table (table depth 2^(lut_addr_width-2))
  dither           bool  add a 1-bit LFSR phase dither below the truncated phase (breaks phase-truncation spurs)
  table_mem        str   "bram" | "lut": where the sine table lives (lut = logic / distributed RAM)
  pipe_lut         int   0..2 register stages after the table read
  pipe_mix         int   0..3 fabric register stages after the mixer multipliers (as pipe_mul elsewhere)
  dsp_regs         str   DSP-internal registers of the mixer multipliers

Interface: in_valid / in_ready (always 1) / in_data / fcw -> out_valid / out_i / out_q (1 sample per cycle).
The golden model mirrors the fixed-point datapath exactly (table quantization, phase truncation, dither sequence).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass

from .fir import DSP_REG_MODES, _style


@dataclass(frozen=True)
class NcoArch:
    data_width: int = 16
    phase_width: int = 32
    sin_width: int = 16
    out_width: int = 16
    lut_addr_width: int = 12
    dither: bool = False
    table_mem: str = "bram"
    pipe_lut: int = 1
    pipe_mix: int = 1
    dsp_regs: str = "none"
    reg_in: bool = True

    @property
    def table_depth(self) -> int:
        return 1 << (self.lut_addr_width - 2)

    @property
    def latency(self) -> int:
        return (1 if self.reg_in else 0) + 1 + self.pipe_lut + 1 + self.pipe_mix + DSP_REG_MODES[self.dsp_regs] + 1

    def validate(self) -> list[str]:
        errs = []
        if self.data_width not in (8, 12, 16, 18):
            errs.append("data_width must be one of 8, 12, 16, 18")
        if not 16 <= self.phase_width <= 48:
            errs.append("phase_width must be in 16..48")
        if self.sin_width not in (8, 10, 12, 14, 16, 18):
            errs.append("sin_width must be one of 8, 10, 12, 14, 16, 18")
        if not 8 <= self.out_width <= self.data_width + self.sin_width:
            errs.append(f"out_width must be in 8..{self.data_width + self.sin_width}")
        if not 6 <= self.lut_addr_width <= min(16, self.phase_width):
            errs.append("lut_addr_width must be in 6..16 and <= phase_width")
        if self.table_mem not in ("bram", "lut"):
            errs.append("table_mem must be bram or lut")
        if not 0 <= self.pipe_lut <= 2:
            errs.append("pipe_lut must be in 0..2")
        if not 0 <= self.pipe_mix <= 3:
            errs.append("pipe_mix must be in 0..3")
        if self.dsp_regs not in DSP_REG_MODES:
            errs.append(f"dsp_regs must be one of {sorted(DSP_REG_MODES)}")
        return errs

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NcoArch":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def digest(self, backend: str = "ecp5") -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:12]


def throughput(a: NcoArch) -> float:
    return 1.0


def latency_cycles(a: NcoArch) -> int:
    return a.latency


# ---------------------------------------------------------------- the table and the golden model
def sine_table(a: NcoArch) -> list[int]:
    """Quarter wave: sin(2*pi*(i+0.5)/(4*depth)) for i in 0..depth-1, scaled to sin_width-1 bits (max 2^(w-1)-1).
    The half-sample offset makes the quadrant mirror exact (no duplicated peak sample)."""
    D = a.table_depth
    amp = (1 << (a.sin_width - 1)) - 1
    return [int(round(amp * math.sin(2 * math.pi * (i + 0.5) / (4 * D)))) for i in range(D)]


def sincos(a: NcoArch, phase: int, table: list[int]) -> tuple[int, int]:
    """(sin, cos) from the top lut_addr_width bits of the phase, quarter-wave symmetry; mirrors the RTL exactly."""
    A = a.lut_addr_width
    D = a.table_depth
    idx = (phase >> (a.phase_width - A)) & ((1 << A) - 1)
    quad = idx >> (A - 2)
    frac = idx & (D - 1)
    s_idx = frac if quad in (0, 2) else D - 1 - frac
    c_idx = D - 1 - frac if quad in (0, 2) else frac
    s = table[s_idx]
    c = table[c_idx]
    sin_v = s if quad in (0, 1) else -s
    cos_v = c if quad in (0, 3) else -c
    return sin_v, cos_v


def _lfsr_step(st: int) -> int:
    """16-bit Fibonacci LFSR x^16+x^14+x^13+x^11+1 (the RTL's dither source)."""
    bit = ((st >> 15) ^ (st >> 13) ^ (st >> 12) ^ (st >> 10)) & 1
    return ((st << 1) | bit) & 0xFFFF


def golden(a: NcoArch, xs: list[int], fcws: list[int]) -> list[tuple[int, int]]:
    """I/Q outputs for accepted samples xs with the FCW sampled alongside each. Phase starts at 0; dither (when on)
    adds the LFSR bit at the bit just below the table address before truncation. Outputs are the top out_width bits
    of the (data_width + sin_width)-bit product, truncated (arithmetic shift)."""
    table = sine_table(a)
    mask = (1 << a.phase_width) - 1
    phase = 0
    lfsr = 0xACE1
    shift = a.data_width + a.sin_width - a.out_width
    out = []
    for x, fcw in zip(xs, fcws):
        p = phase
        if a.dither:
            lfsr = _lfsr_step(lfsr)
            p = (p + ((lfsr & 1) << (a.phase_width - a.lut_addr_width - 1))) & mask if a.phase_width > a.lut_addr_width else p
        s, c = sincos(a, p, table)
        i_v = (x * c) >> shift
        q_v = (-(x * s)) >> shift
        out.append((i_v, q_v))
        phase = (phase + (fcw & mask)) & mask
    return out


# ---------------------------------------------------------------- emitter
def emit_nco(a: NcoArch, backend: str = "ecp5") -> str:
    DW, PW, SW, OW, A = a.data_width, a.phase_width, a.sin_width, a.out_width, a.lut_addr_width
    D = a.table_depth
    table = sine_table(a)
    tbl = ", ".join(f"{SW}'sd{v}" if v >= 0 else f"-{SW}'sd{-v}" for v in table)
    ML = a.pipe_mix + DSP_REG_MODES[a.dsp_regs]
    # "lut" leaves the table unattributed: yosys maps it to logic (a ROM of constants folds into LUTs) where the
    # device has no distributed RAM (iCE40), and to LUT RAM where it does
    mem_attr = '(* ram_style = "block" *)' if a.table_mem == "bram" else '(* rom_style = "logic" *)'
    shift = DW + SW - OW
    header = f"// Generated by silicon-agent -- do not edit. IR: {json.dumps(a.to_dict())}\n`default_nettype none\n\n"
    dither = f"""    // 1-bit LFSR dither below the table address: decorrelates phase-truncation spurs
    logic [15:0] lfsr;
    wire  [15:0] lfsr_next = {{lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]}};
    always_ff @(posedge clk) begin
        if (rst) lfsr <= 16'hACE1;
        else if (v0) lfsr <= lfsr_next;
    end
    wire [PW-1:0] ph_d = ph + (PW'(lfsr_next[0]) << (PW - A - 1));""" if (a.dither and PW > A) else "    wire [PW-1:0] ph_d = ph;"
    # pipeline helpers: a shift chain for valid and for the data sample alongside the table + mixer stages
    def chain(name: str, src: str, n: int, width: str | None = None) -> str:
        # data chains are free-running; the 1-bit valid chains reset so out_valid is never X after reset
        if n == 0:
            return f"    assign {name} = {src};"
        decl = f"logic {width} {name}_q [{n}];" if width else f"logic {name}_q [{n}];"
        stages = " ".join(f"{name}_q[{i}] <= {name}_q[{i-1}];" for i in range(1, n))
        if width:
            return f"    {decl}\n    always_ff @(posedge clk) begin {name}_q[0] <= {src}; {stages} end\n    assign {name} = {name}_q[{n - 1}];"
        zero = " ".join(f"{name}_q[{i}] <= 1'b0;" for i in range(n))
        return f"    {decl}\n    always_ff @(posedge clk) if (rst) begin {zero} end else begin {name}_q[0] <= {src}; {stages} end\n    assign {name} = {name}_q[{n - 1}];"
    lut_stages = a.pipe_lut
    mix_stages = ML
    return f"""{header}module nco_top #(
    parameter int DW = {DW},
    parameter int PW = {PW},
    parameter int SW = {SW},
    parameter int OW = {OW},
    parameter int A  = {A},
    parameter int D  = {D}
) (
    input  wire                   clk,
    input  wire                   rst,
    input  wire                   in_valid,
    input  wire signed [DW-1:0]   in_data,
    input  wire [PW-1:0]          fcw,
    output logic                  in_ready,
    output logic                  out_valid,
    output logic signed [OW-1:0]  out_i,
    output logic signed [OW-1:0]  out_q
);
    assign in_ready = 1'b1;

    // ---- input register (optional) ----
    logic signed [DW-1:0] x0; logic v0; logic [PW-1:0] fcw0;
    generate
        if ({1 if a.reg_in else 0}) begin : g_reg_in
            always_ff @(posedge clk) begin x0 <= in_data; v0 <= in_valid & ~rst; fcw0 <= fcw; end
        end else begin : g_no_reg_in
            assign x0 = in_data; assign v0 = in_valid & ~rst; assign fcw0 = fcw;
        end
    endgenerate

    // ---- phase accumulator: advances by the FCW on every accepted sample ----
    logic [PW-1:0] ph;
    always_ff @(posedge clk) begin
        if (rst) ph <= '0;
        else if (v0) ph <= ph + fcw0;
    end
{dither}

    // ---- quarter-wave table lookup (stage 1: address + quadrant; stage 2: read) ----
    {mem_attr} logic signed [SW-1:0] SINE [D] = '{{{tbl}}};
    logic [A-1:0] idx; logic [1:0] quad; logic [A-3:0] frac;
    assign idx  = ph_d[PW-1 -: A];
    assign quad = idx[A-1:A-2];
    assign frac = idx[A-3:0];
    logic [A-3:0] s_addr, c_addr; logic s_neg, c_neg; logic v1; logic signed [DW-1:0] x1;
    always_ff @(posedge clk) begin
        s_addr <= (quad[0] == 1'b0) ? frac : (A-2)'(D - 1) - frac;
        c_addr <= (quad[0] == 1'b0) ? (A-2)'(D - 1) - frac : frac;
        s_neg  <= quad[1];
        c_neg  <= quad[0] ^ quad[1];
        v1     <= v0 & ~rst;
        x1     <= x0;
    end
    logic signed [SW-1:0] s_raw, c_raw; logic s_neg2, c_neg2; logic v2; logic signed [DW-1:0] x2;
    always_ff @(posedge clk) begin
        s_raw <= SINE[s_addr]; c_raw <= SINE[c_addr];
        s_neg2 <= s_neg; c_neg2 <= c_neg; v2 <= v1 & ~rst; x2 <= x1;
    end
    // the table read has its own stage (the BRAM/ROM output register); the quadrant sign is a conditional negate
    // (one carry chain) on the read value, Q = x * (-sin) so the negation is on the sine, never on the product.
    // pipe_lut registers follow the negate: with pipe_lut >= 1 the negate and the DSP input are in different cycles.
    logic signed [SW-1:0] s_n, c_n;
    assign s_n = s_neg2 ? s_raw : -s_raw;     // -sin for the Q path
    assign c_n = c_neg2 ? -c_raw : c_raw;
    logic signed [SW-1:0] s_m, c_m; logic signed [DW-1:0] x_l; logic v_l;
{chain("s_m", "s_n", lut_stages, f"signed [SW-1:0]")}
{chain("c_m", "c_n", lut_stages, f"signed [SW-1:0]")}
{chain("x_l", "x2", lut_stages, f"signed [DW-1:0]")}
{chain("v_l", "v2", lut_stages)}

    // ---- complex mixer: I = x*cos, Q = x*(-sin) ----
    logic signed [DW+SW-1:0] pi_c, pq_c;
    assign pi_c = x_l * c_m;
    assign pq_c = x_l * s_m;
    logic signed [DW+SW-1:0] pi_m, pq_m; logic v_m;
{chain("pi_m", "pi_c", mix_stages, f"signed [DW+SW-1:0]")}
{chain("pq_m", "pq_c", mix_stages, f"signed [DW+SW-1:0]")}
{chain("v_m", "v_l", mix_stages)}

    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0; out_i <= '0; out_q <= '0;
        end else begin
            out_valid <= v_m;
            out_i <= OW'(pi_m >>> {shift});
            out_q <= OW'(pq_m >>> {shift});
        end
    end
endmodule
`default_nettype wire
"""
