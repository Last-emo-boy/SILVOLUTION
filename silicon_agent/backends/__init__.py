"""Backend registry: one plugin per device/toolchain.

A backend is a `Backend` record: device facts for the planner and the fit check, the DSP/LUT
primitive names, and two functions:

    synth_pnr(work, fmax_target, top) -> {"fmax_mhz", "lut", "ff", "dsp", "bram", "critical_path", "fits"}
    netlist_sims(design, arch, work, fmax_target, on_stage) -> {stage: {"passed", "label", "period_ns", "log"}}

plus `sim_stages`, the keys of the netlist-simulation ladder it can run (the UI shows the rest as n/a).
Adding a device = one module in this package that calls `register()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

ALL_STAGES = ["behavioral", "post_synth_func", "post_synth_time", "post_impl_func", "post_impl_time"]


@dataclass(frozen=True)
class Backend:
    key: str
    name: str                       # human-readable device name
    vendor: str                     # lattice | amd | gowin ...
    lut: int                        # device capacity (fit check)
    dsp: int
    bram: int
    lut_kind: str                   # LUT4 | LUT6 ...
    dsp_kind: str                   # primitive name: MULT18X18D | DSP48E1 | SB_MAC16 | MULT18X18 ...
    dsp_width: int                  # multiplier operand width the primitive offers (per operand)
    synth_pnr: Callable
    netlist_sims: Callable | None
    sim_stages: tuple[str, ...]     # subset of ALL_STAGES this backend can run
    seconds_per_iter: str           # rough, for the planner / UI ("15-60 s")
    fmax_range: str                 # realistic Fmax band for the planner ("60-250 MHz")
    toolchain: str                  # "yosys + nextpnr-ecp5", "Vivado 2025.2" ...
    emit_style: str = "behavioral"  # how emitters should render multipliers: "ecp5" (explicit MULT18X18D), "vivado", "behavioral"
    aliases: tuple[str, ...] = ()   # words the NL interpreter maps to this backend
    parallel: int = 1               # how many candidate designs may run through this toolchain at once (CPU budget)
    extra: dict = field(default_factory=dict)

    def as_facts(self) -> dict:
        return {"key": self.key, "name": self.name, "vendor": self.vendor, "lut": self.lut, "dsp": self.dsp, "bram": self.bram,
                "lut_kind": self.lut_kind, "dsp_kind": self.dsp_kind, "toolchain": self.toolchain,
                "seconds_per_iter": self.seconds_per_iter, "fmax_range": self.fmax_range, "sim_stages": list(self.sim_stages), "parallel": self.parallel}


BACKENDS: dict[str, Backend] = {}


def register(b: Backend) -> Backend:
    BACKENDS[b.key] = b
    return b


# import order = display order
from . import ecp5, xc7s50, ice40, gowin, gatemate, xc7a35t  # noqa: E402,F401
