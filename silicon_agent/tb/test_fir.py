"""cocotb testbench for fir_top. Golden model: direct convolution in Python.

Drives a random sample stream with gaps, honours in_ready (folded designs
stall), collects every out_valid sample and compares the whole stream to
the reference. No latency assumptions.
"""

import json
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Timer
from coverage import Coverage


def to_signed(v: int, w: int) -> int:
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


def golden(xs, h):
    n = len(h)
    hist = [0] * n
    out = []
    for x in xs:
        hist = [x] + hist[:-1]
        out.append(sum(hist[k] * h[k] for k in range(n)))
    return out


@cocotb.test()
async def stream(dut):
    arch = json.loads(os.environ["SA_ARCH"])
    DW, AW, h = arch["data_width"], arch["acc_width"], arch["coefs"]
    n_samples = int(os.environ.get("SA_SAMPLES", "200"))
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))
    lo, hi = -(1 << (DW - 1)), (1 << (DW - 1)) - 1

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.in_valid.value = 0
    dut.in_data.value = 0
    if arch.get("coef_port"):
        dut.coef_we.value = 0; dut.coef_addr.value = 0; dut.coef_data.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    if arch.get("coef_port"):
        # replace the generated coefficients with a reversed/perturbed set through the write port, then filter with it
        U = (len(h) + 1) // 2 if arch.get("symmetric") else len(h)
        new_u = [((c * 3) // 4 + (7 if i % 2 else -5)) for i, c in enumerate(h[:U])]
        CW = arch["coef_width"]; lo_c, hi_c = -(1 << (CW - 1)), (1 << (CW - 1)) - 1
        new_u = [max(lo_c, min(hi_c, c)) for c in new_u]
        for i, c in enumerate(new_u):
            dut.coef_we.value = 1; dut.coef_addr.value = i; dut.coef_data.value = c & ((1 << CW) - 1)
            await RisingEdge(dut.clk)
        dut.coef_we.value = 0
        await RisingEdge(dut.clk)
        h = new_u + (new_u[::-1][len(h) % 2:] if arch.get("symmetric") else [])
        if arch.get("symmetric"):
            h = [new_u[min(i, len(h) - 1 - i)] for i in range(len(h))]

    # extremes first (saturation / sign), then random with gaps, then a gapless burst
    xs = [hi] * len(h) + [lo] * len(h) + [rng.randint(lo, hi) for _ in range(n_samples)]
    expected = golden(xs, h)
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if dut.out_valid.value:
                got.append(to_signed(int(dut.out_data.value), AW))

    cocotb.start_soon(collector())

    cov = Coverage("fir", ["sample:max", "sample:min", "sample:zero", "stim:gap", "stim:back-to-back", "stall:ready-low", "coef:runtime-write"])
    if arch.get("coef_port"):
        cov.hit("coef:runtime-write", U)
    burst_from = len(xs) - n_samples // 2
    for i, x in enumerate(xs):
        gap = 0 if i >= burst_from else rng.choice([0, 0, 1, 3])
        cov.hit("stim:gap" if gap else "stim:back-to-back")
        if x == hi: cov.hit("sample:max")
        elif x == lo: cov.hit("sample:min")
        elif x == 0: cov.hit("sample:zero")
        if gap:
            dut.in_valid.value = 0
            await ClockCycles(dut.clk, gap)
        dut.in_valid.value = 1
        dut.in_data.value = x & ((1 << DW) - 1)
        # hold until the clock edge at which the DUT samples valid&&ready high
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
        await Timer(1, unit="ns")  # move off the edge before driving the next value
    dut.in_valid.value = 0
    cov.report()

    await ClockCycles(dut.clk, 64 + 4 * arch["fold"])

    assert len(got) == len(expected), f"expected {len(expected)} outputs, got {len(got)}"
    ref = [to_signed(v, AW) for v in expected]
    for k, (g, r) in enumerate(zip(got, ref)):
        assert g == r, f"sample {k}: got {g} expected {r}"
