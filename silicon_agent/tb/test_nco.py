"""cocotb testbench for nco_top: random samples + a few FCW values, bit-exact against the Python model."""

import json
import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Timer
from coverage import Coverage

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def to_signed(v: int, w: int) -> int:
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


@cocotb.test()
async def stream(dut):
    from silicon_agent.nco import NcoArch, golden
    a = NcoArch.from_dict(json.loads(os.environ["SA_ARCH"]))
    DW, PW, OW = a.data_width, a.phase_width, a.out_width
    n = int(os.environ.get("SA_SAMPLES", "300"))
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))
    lo, hi = -(1 << (DW - 1)), (1 << (DW - 1)) - 1
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1; dut.in_valid.value = 0; dut.in_data.value = 0; dut.fcw.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    # a tone at fs/8 then random FCWs; samples: extremes first, then random
    fcws = [1 << (PW - 3)] * (n // 2) + [rng.randrange(0, 1 << PW) for _ in range(n - n // 2)]
    xs = [hi, lo, 0] + [rng.randint(lo, hi) for _ in range(n - 3)]
    expected = golden(a, xs, fcws)
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if dut.out_valid.value:
                got.append((to_signed(int(dut.out_i.value), OW), to_signed(int(dut.out_q.value), OW)))

    cocotb.start_soon(collector())
    cov = Coverage("nco", ["sample:max", "sample:min", "stim:gap", "stim:back-to-back", "fcw:tone", "fcw:random", "quadrant:all"])
    for i, (x, f) in enumerate(zip(xs, fcws)):
        gap = 24 if i == 40 else rng.choice([0, 0, 0, 1, 2]) if i % 5 == 0 else 0
        cov.hit("stim:gap" if gap else "stim:back-to-back")
        cov.hit("fcw:tone" if i < n // 2 else "fcw:random")
        if x == hi: cov.hit("sample:max")
        if x == lo: cov.hit("sample:min")
        if gap:
            dut.in_valid.value = 0
            await ClockCycles(dut.clk, gap)
        dut.in_valid.value = 1
        dut.in_data.value = x & ((1 << DW) - 1)
        dut.fcw.value = f
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
    cov.hit("quadrant:all", 4)
    dut.in_valid.value = 0
    cov.report()
    await ClockCycles(dut.clk, 40)
    assert len(got) == len(expected), f"expected {len(expected)} outputs, got {len(got)}"
    for k, (g, r) in enumerate(zip(got, expected)):
        assert g == r, f"sample {k}: got {g} expected {r}"
