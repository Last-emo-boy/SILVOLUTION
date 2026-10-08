"""Architecture IR: the only thing the LLM edits. RTL is generated from it."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


DSP_REG_MODES = {"none": 0, "out": 1, "in_out": 2, "in_pipe_out": 3}  # mode -> latency in cycles


@dataclass(frozen=True)
class MatmulArch:
    """Output-stationary systolic matmul: C[M x N] += A[M x K] * B[K x N].

    pe_rows x pe_cols PEs, each PE owns one C element and accumulates
    A[i][k] * B[k][j] over k. The array is tiled over M and N.
    """

    pe_rows: int = 4
    pe_cols: int = 4
    data_width: int = 8
    acc_width: int = 32
    pipe_mul: int = 1  # fabric register stages between multiplier and accumulator (0..3)
    reg_in: bool = True  # register A/B inputs at array boundary
    dsp_regs: str = "none"  # DSP internal registers: none | out | in_out | in_pipe_out
    acc_in_dsp: bool = True  # xc7s50 only: let Vivado fold the accumulate into the DSP48E1 post-adder

    def validate(self) -> list[str]:
        errs = []
        if not 1 <= self.pe_rows <= 32:
            errs.append("pe_rows must be in 1..32")
        if not 1 <= self.pe_cols <= 32:
            errs.append("pe_cols must be in 1..32")
        if self.data_width not in (4, 8, 12, 16):
            errs.append("data_width must be one of 4, 8, 12, 16")
        if not 2 * self.data_width + 8 <= self.acc_width <= 48:
            errs.append(f"acc_width must be in {2 * self.data_width + 8}..48")
        if not 0 <= self.pipe_mul <= 3:
            errs.append("pipe_mul must be in 0..3")
        if self.dsp_regs not in DSP_REG_MODES:
            errs.append(f"dsp_regs must be one of {sorted(DSP_REG_MODES)}")
        return errs

    @property
    def mul_latency(self) -> int:
        return self.pipe_mul + DSP_REG_MODES[self.dsp_regs]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MatmulArch":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)

    def digest(self, backend: str = "ecp5") -> str:
        """Identity of the design as the backend sees it; fields that have no effect
        on a backend are dropped so the loop does not count them as new designs."""
        d = self.to_dict()
        from .backends import BACKENDS
        if backend in BACKENDS and BACKENDS[backend].emit_style != "vivado":
            d.pop("acc_in_dsp", None)
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


@dataclass
class Constraints:
    fmax_mhz: float = 100.0
    max_lut: int | None = None
    max_dsp: int | None = None
    max_bram: int | None = None
    max_ff: int | None = None
    max_power_mw: float | None = None  # dynamic power estimate (Vivado report_power); None on devices without a power model
    max_area_um2: float | None = None  # standard-cell area (ASIC backends); None on FPGAs
    objective: list[str] = field(default_factory=lambda: ["maximize_throughput", "minimize_lut"])

    @classmethod
    def from_dict(cls, d: dict) -> "Constraints":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class Metrics:
    fmax_mhz: float
    lut: int
    ff: int
    dsp: int
    bram: int
    critical_path: list[dict]  # [{"cell": "...", "delay_ns": ..}, ...] from nextpnr
    sim_passed: bool
    sim_log: str = ""
    fits: bool = True  # False when synthesis shows the design exceeds the device
    sim_stages: dict = field(default_factory=dict)  # netlist simulation ladder: {stage: {"passed", "label", ...}}
    fmax_seeds: list = field(default_factory=list)  # per-seed Fmax when P&R ran with several seeds; fmax_mhz is their median
    power_mw: float | None = None  # dynamic power estimate where the toolchain reports one (Vivado)
    sim_coverage: dict = field(default_factory=dict)  # functional-coverage bins hit by the behavioral testbench
    area_um2: float | None = None  # standard-cell area after place-and-route (ASIC backends); lut then counts cells

    def violations(self, c: Constraints) -> list[str]:
        v = []
        if not self.sim_passed:
            v.append("simulation failed")
        if not self.fits:
            v.append("does not fit the device (P&R skipped)")
        for k, st in self.sim_stages.items():
            if st.get("passed") is False:
                v.append(f"{st.get('label', k)} simulation failed")
        if self.fmax_mhz < c.fmax_mhz:
            v.append(f"fmax {self.fmax_mhz:.1f} MHz < {c.fmax_mhz} MHz")
        for name, limit in (("lut", c.max_lut), ("dsp", c.max_dsp), ("bram", c.max_bram), ("ff", c.max_ff)):
            if limit is not None and getattr(self, name) > limit:
                v.append(f"{name} {getattr(self, name)} > {limit}")
        if c.max_power_mw is not None and self.power_mw is not None and self.power_mw > c.max_power_mw:
            v.append(f"power {self.power_mw} mW > {c.max_power_mw} mW")
        if c.max_area_um2 is not None and self.area_um2 is not None and self.area_um2 > c.max_area_um2:
            v.append(f"area {self.area_um2:.0f} um2 > {c.max_area_um2} um2")
        return v


def throughput_macs_per_cycle(a: MatmulArch) -> int:
    return a.pe_rows * a.pe_cols
