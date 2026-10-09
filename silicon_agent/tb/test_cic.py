"""cocotb testbench for cic_top: bit-exact against the Python CIC model."""

import json
import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from silicon_agent.cic import CicArch, golden  # noqa: E402
from coverage import Coverage  # noqa: E402


def to_signed(v: int, w: int) -> int:
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


@cocotb.test()
async def stream(dut):
    arch = json.loads(os.environ["SA_ARCH"])
    a = CicArch.from_dict(arch)
    DW, OW, R = a.data_width, a.out_width, a.decim
    n = int(os.environ.get("SA_SAMPLES", "200"))
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))
    lo, hi = -(1 << (DW - 1)), (1 << (DW - 1)) - 1

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.in_valid.value = 0
    dut.in_data.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    await RisingEdge(dut.clk)

    xs = [hi, lo] * R + [rng.randint(lo, hi) for _ in range(max(0, n - 2 * R))]
    expected = golden(a, xs)
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if dut.out_valid.value:
                got.append(to_signed(int(dut.out_data.value), OW))

    cocotb.start_soon(collector())
    cov = Coverage("cic", ["sample:max", "sample:min", "stim:gap", "stim:back-to-back",
                           "stim:long-gap", "stim:post-dec-gap", "frame:complete"])
    burst_from = len(xs) // 2
    for i, x in enumerate(xs):
        # long gaps (>= 8) anywhere must NOT corrupt the comb state; one is forced right after a dec boundary
        if i == R - 1:
            gap = 12   # a dec_tick just happened at sample index R-1, then a >= 12-cycle pause
        elif i >= burst_from:
            gap = 0
        else:
            gap = rng.choice([0, 0, 1, 3, 8, 12, 20])
        if gap >= 8:
            cov.hit("stim:long-gap")
        elif gap:
            cov.hit("stim:gap")
        else:
            cov.hit("stim:back-to-back")
        if i == R - 1 and gap:
            cov.hit("stim:post-dec-gap")
        if x == hi:
            cov.hit("sample:max")
        elif x == lo:
            cov.hit("sample:min")
        if gap:
            dut.in_valid.value = 0
            await ClockCycles(dut.clk, gap)
        dut.in_valid.value = 1
        dut.in_data.value = x & ((1 << DW) - 1)
        await ReadOnly()
        assert bool(dut.in_ready.value), "in_ready dropped (CIC must always accept)"
        await RisingEdge(dut.clk)
        if i % R == R - 1:
            cov.hit("frame:complete")
        await Timer(1, unit="ns")
    dut.in_valid.value = 0
    cov.report()
    # pipe_comb delays the comb by N-1 *cycles* (not frames)
    drain = 64 + 2 * R * a.stages + 8 * (a.stages if a.pipe_comb else 0)
    await ClockCycles(dut.clk, drain)

    assert len(got) == len(expected), f"expected {len(expected)} outputs, got {len(got)}"
    for k, (g, r) in enumerate(zip(got, expected)):
        assert g == r, f"output {k}: got {g} expected {r}"
