"""cocotb testbench for iir_top: bit-exact against the fixed-point Python model (step, impulse, tone, random)."""

import json
import math
import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Timer
from coverage import Coverage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def to_signed(v: int, w: int) -> int:
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


@cocotb.test()
async def stream(dut):
    from silicon_agent.iir import IirArch, golden
    a = IirArch.from_dict(json.loads(os.environ["SA_ARCH"]))
    DW = a.data_width
    n = int(os.environ.get("SA_SAMPLES", "300"))
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))
    lo, hi = -(1 << (DW - 1)), (1 << (DW - 1)) - 1
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1; dut.in_valid.value = 0; dut.in_data.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    # step at full scale (saturation inside the sections), impulse, a tone at the cutoff, random
    xs = [hi] * 40 + [0] * 20 + [lo] + [0] * 30 + [int(0.5 * hi * math.sin(2 * math.pi * a.cutoff * i)) for i in range(60)] + [rng.randint(lo, hi) for _ in range(n)]
    expected = golden(a, xs)
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if dut.out_valid.value:
                got.append(to_signed(int(dut.out_data.value), DW))

    cocotb.start_soon(collector())
    cov = Coverage("iir", ["stim:step", "stim:impulse", "stim:tone", "stim:random", "stim:gap", "stim:back-to-back", "stall:ready-low"])
    for i, x in enumerate(xs):
        cov.hit("stim:step" if i < 40 else "stim:impulse" if i < 91 else "stim:tone" if i < 151 else "stim:random")
        gap = 24 if i == 40 else rng.choice([0, 0, 0, 1, 2]) if i > 151 and i % 6 == 0 else 0
        cov.hit("stim:gap" if gap else "stim:back-to-back")
        if gap:
            dut.in_valid.value = 0
            await ClockCycles(dut.clk, gap)
        dut.in_valid.value = 1
        dut.in_data.value = x & ((1 << DW) - 1)
        stall = 0
        while True:
            await ReadOnly()
            ready = bool(dut.in_ready.value)
            await RisingEdge(dut.clk)
            if ready:
                break
            stall += 1
            assert stall < 4000, "in_ready stalled for 4000 cycles (dead handshake)"
            cov.hit("stall:ready-low")
        await Timer(1, unit="ns")
    dut.in_valid.value = 0
    cov.report()
    await ClockCycles(dut.clk, 40)
    assert len(got) == len(expected), f"expected {len(expected)} outputs, got {len(got)}"
    for k, (g, r) in enumerate(zip(got, expected)):
        assert g == r, f"sample {k}: got {g} expected {r}"
