"""cocotb testbench for decim_top: one output per M accepted inputs, golden = full convolution sampled at frame ends."""

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


def golden(xs, h, M):
    N = len(h)
    out = []
    for n in range(len(xs) // M):
        m = n * M + M - 1
        out.append(sum(h[k] * xs[m - k] for k in range(N) if m - k >= 0))
    return out


@cocotb.test()
async def stream(dut):
    arch = json.loads(os.environ["SA_ARCH"])
    DW, AW, h, M = arch["data_width"], arch["acc_width"], arch["coefs"], arch["decim"]
    n_frames = int(os.environ.get("SA_FRAMES", "24"))
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))
    lo, hi = -(1 << (DW - 1)), (1 << (DW - 1)) - 1

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1; dut.in_valid.value = 0; dut.in_data.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    await RisingEdge(dut.clk)

    n = n_frames * M
    xs = [hi] * M + [lo] * M + [rng.randint(lo, hi) for _ in range(n - 2 * M)]
    expected = golden(xs, h, M)
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if dut.out_valid.value:
                got.append(to_signed(int(dut.out_data.value), AW))

    cocotb.start_soon(collector())
    cov = Coverage("decim", ["sample:max", "sample:min", "stim:gap", "stim:back-to-back", "stall:ready-low", "frame:complete"])
    burst_from = len(xs) // 2
    for i, x in enumerate(xs):
        gap = 0 if i >= burst_from else rng.choice([0, 0, 1, 3])
        cov.hit("stim:gap" if gap else "stim:back-to-back")
        if x == hi: cov.hit("sample:max")
        elif x == lo: cov.hit("sample:min")
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
        if i % M == M - 1: cov.hit("frame:complete")
        await Timer(1, unit="ns")
    dut.in_valid.value = 0
    cov.report()
    await ClockCycles(dut.clk, 64 + 8 * arch["fold"] * M)

    assert len(got) == len(expected), f"expected {len(expected)} outputs, got {len(got)}"
    ref = [to_signed(v, AW) for v in expected]
    for k, (g, r) in enumerate(zip(got, ref)):
        assert g == r, f"output {k}: got {g} expected {r}"
