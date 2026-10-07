"""Design registry: everything the loop and EDA layer need to know per design kind.

Adding a design = one entry here + an IR dataclass + an emitter + a cocotb test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import fir
from .emit import drain_latency, emit_matmul, min_k_full_rate
from .ir import MatmulArch, throughput_macs_per_cycle


@dataclass(frozen=True)
class Design:
    kind: str
    ir_cls: type
    top: str                                  # RTL top module name
    emit: Callable                            # (ir, backend) -> SystemVerilog text
    test_module: str                          # cocotb module under silicon_agent/tb/
    sim_env: Callable                         # ir -> extra env for the testbench
    throughput: Callable                      # ir -> number (higher is better)
    throughput_unit: str
    latency: Callable                         # ir -> cycles
    extra_sim_sources: tuple[str, ...] = ()   # files under tb/ to compile alongside the RTL


DESIGNS: dict[str, Design] = {
    "matmul": Design(
        kind="matmul",
        ir_cls=MatmulArch,
        top="matmul_top",
        emit=emit_matmul,
        test_module="test_matmul",
        sim_env=lambda a: {"SA_MIN_K": str(min_k_full_rate(a)), "SA_TILES": "6"},
        throughput=throughput_macs_per_cycle,
        throughput_unit="MAC/cycle",
        latency=lambda a: drain_latency(a) + a.pe_rows,
        extra_sim_sources=("mult18x18d_sim.v",),
    ),
    "fir": Design(
        kind="fir",
        ir_cls=fir.FirArch,
        top="fir_top",
        emit=fir.emit_fir,
        test_module="test_fir",
        sim_env=lambda a: {"SA_SAMPLES": "200"},
        throughput=fir.throughput,
        throughput_unit="samples/cycle",
        latency=fir.latency_cycles,
        extra_sim_sources=("mult18x18d_sim.v",),
    ),
}


def design_for(spec: dict) -> Design:
    kind = spec.get("design", "matmul")
    if kind not in DESIGNS:
        raise ValueError(f"unknown design {kind!r}; have {sorted(DESIGNS)}")
    return DESIGNS[kind]
