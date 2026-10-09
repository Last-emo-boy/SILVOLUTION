"""Design registry: everything the loop and EDA layer need to know per design kind.

Adding a design = one entry here + an IR dataclass + an emitter + a cocotb test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from . import chain, conv, cordic, fft, fir, graph, vectors
from .emit import drain_latency, emit_matmul, min_k_full_rate
from .ir import MatmulArch, throughput_macs_per_cycle


@dataclass(frozen=True)
class Design:
    kind: str
    ir_cls: type
    top: str                                  # RTL top module name
    emit: Callable                            # (ir, backend) -> SystemVerilog text
    test_module: str | None                   # cocotb module under silicon_agent/tb/; None = the design's own SV bench (tb) runs under iverilog
    sim_env: Callable                         # ir -> extra env for the testbench
    throughput: Callable                      # ir -> number (higher is better)
    throughput_unit: str
    latency: Callable                         # ir -> cycles
    extra_sim_sources: tuple[str, ...] = ()   # files under tb/ to compile alongside the RTL
    vectors: Callable | None = None           # ir -> (stim_lines, exp_lines) for netlist sims
    tb: Callable | None = None                # (ir, period_ns) -> SystemVerilog testbench text


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
        vectors=vectors.matmul_vectors,
        tb=lambda a, period: vectors.TB_MATMUL.format(R=a.pe_rows, C=a.pe_cols, W=a.data_width, AW=a.acc_width, period=period),
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
        vectors=vectors.fir_vectors,
        tb=lambda a, period: vectors.TB_FIR.format(DW=a.data_width, AW=a.acc_width, period=period, COEF_TIE=", .coef_we(1'b0), .coef_addr('0), .coef_data('0)" if a.coef_port else ""),
    ),
    "conv2d": Design(
        kind="conv2d",
        ir_cls=conv.ConvArch,
        top="conv2d_top",
        emit=conv.emit_conv,
        test_module="test_conv",
        sim_env=lambda a: {"SA_ROWS": str(a.k + 3)},
        throughput=conv.throughput,
        throughput_unit="pixels/cycle",
        latency=conv.latency_cycles,
        extra_sim_sources=("mult18x18d_sim.v",),
        vectors=vectors.conv_vectors,
        tb=lambda a, period: vectors.TB_CONV.format(DW=a.data_width, AW=a.acc_width, period=period),
    ),
    "fir_chain": Design(
        kind="fir_chain",
        ir_cls=chain.FirChainArch,
        top="fir_chain_top",
        emit=chain.emit_fir_chain,
        test_module="test_chain",
        sim_env=lambda a: {"SA_SAMPLES": "160"},
        throughput=chain.throughput,
        throughput_unit="samples/cycle",
        latency=chain.latency_cycles,
        extra_sim_sources=("mult18x18d_sim.v",),
        vectors=vectors.chain_vectors,
        tb=lambda a, period: vectors.TB_CHAIN.format(DW=a.data_width, AW=a.acc_width, period=period, COEF_TIE=""),
    ),
    "cordic": Design(
        kind="cordic",
        ir_cls=cordic.CordicArch,
        top="cordic_top",
        emit=cordic.emit_cordic,
        test_module="test_cordic",
        sim_env=lambda a: {"SA_SAMPLES": "150"},
        throughput=cordic.throughput,
        throughput_unit="samples/cycle",
        latency=cordic.latency_cycles,
        extra_sim_sources=("mult18x18d_sim.v",),
        vectors=vectors.cordic_vectors,
        tb=lambda a, period: vectors.TB_CORDIC.format(W=a.width, period=period),
    ),
    "fft": Design(
        kind="fft",
        ir_cls=fft.FftArch,
        top="fft_top",
        emit=fft.emit_fft,
        test_module="test_fft",
        sim_env=lambda a: {"SA_FRAMES": "3"},
        throughput=fft.throughput,
        throughput_unit="samples/cycle",
        latency=fft.latency_cycles,
        extra_sim_sources=("mult18x18d_sim.v",),
        vectors=vectors.fft_vectors,
        tb=lambda a, period: vectors.TB_FFT.format(DW=a.data_width, OW=a.out_width, N=a.n, period=period),
    ),
    "graph": Design(
        kind="graph",
        ir_cls=graph.GraphArch,
        top="graph_top",
        emit=graph.emit_graph,
        test_module="test_graph",
        sim_env=lambda a: {"SA_SAMPLES": "160"},
        throughput=graph.throughput,
        throughput_unit="samples/cycle",
        latency=graph.latency_cycles,
        extra_sim_sources=("mult18x18d_sim.v",),
        vectors=vectors.graph_vectors,
        tb=lambda a, period: vectors.TB_GRAPH.format(DW=graph.data_width(a), OW=graph.out_width(a), N=64, period=period),
    ),
}


def register_rtl(bundle: dict) -> Design:
    """A bring-your-own-RTL bundle as a design kind `rtl:<id>`: the user's files are the generator (parameters are the
    IR), the user's SystemVerilog bench is the testbench on every rung (test_module None selects the iverilog path)."""
    from . import rtl as _rtl
    kind = f"rtl:{bundle['id']}"
    A = _rtl.arch_class(bundle)
    top = bundle["top"]

    def tb(a, period):
        t = bundle.get("tb") or ""
        # the bench may read the period from a parameter named PERIOD
        return re.sub(r"parameter\s+real\s+PERIOD\s*=\s*[\d.]+", f"parameter real PERIOD = {period}", t)

    d = Design(kind=kind, ir_cls=A, top=top, emit=lambda a, backend="ecp5": _rtl.emit(bundle, a, backend), test_module=None,
               sim_env=lambda a: {}, throughput=lambda a: _rtl.throughput(bundle, a), throughput_unit=bundle.get("throughput_unit", "items/cycle"),
               latency=lambda a: _rtl.latency_cycles(bundle, a), extra_sim_sources=(), vectors=None, tb=tb)
    DESIGNS[kind] = d
    return d


def design_for(spec: dict) -> Design:
    kind = spec.get("design", "matmul")
    if kind not in DESIGNS:
        raise ValueError(f"unknown design {kind!r}; have {sorted(DESIGNS)}")
    return DESIGNS[kind]
