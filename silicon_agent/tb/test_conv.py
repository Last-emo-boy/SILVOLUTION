"""cocotb testbench for conv2d_top. Golden model: direct K x K correlation over the interior of each frame.

Streams two frames (one with frame_start on its first pixel, a second to check re-sync), random gaps,
honours in_ready, collects every out_valid sample and compares the whole stream. No latency assumptions.
"""

import json
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Timer


def to_signed(v: int, w: int) -> int:
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


def golden(frames, k, w, coefs):
    """frames: list of pixel lists (row-major, each len == w*h). Output per frame: (w-k+1)*(h-k+1) values,
    row-major over the interior, correlation form (no kernel flip)."""
    out = []
    for px in frames:
        h = len(px) // w
        for r in range(k - 1, h):
            for c in range(k - 1, w):
                acc = 0
                for i in range(k):
                    for j in range(k):
                        acc += coefs[i * k + j] * px[(r - k + 1 + i) * w + (c - k + 1 + j)]
                out.append(acc)
    return out


def make_frames(rng, k, w, dw, n_rows, n_frames):
    lo, hi = -(1 << (dw - 1)), (1 << (dw - 1)) - 1
    frames = []
    for f in range(n_frames):
        if f == 0:
            # extremes first: a checkerboard of lo/hi, then random
            px = [(hi if (i + i // w) % 2 else lo) for i in range(w * k)] + [rng.randint(lo, hi) for _ in range(w * (n_rows - k))]
        else:
            px = [rng.randint(lo, hi) for _ in range(w * n_rows)]
        frames.append(px)
    return frames


@cocotb.test()
async def stream(dut):
    arch = json.loads(os.environ["SA_ARCH"])
    K, W, DW, AW, coefs = arch["k"], arch["width"], arch["data_width"], arch["acc_width"], arch["coefs"]
    n_rows = int(os.environ.get("SA_ROWS", str(K + 3)))
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.in_valid.value = 0
    dut.frame_start.value = 0
    dut.in_data.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    await RisingEdge(dut.clk)

    frames = make_frames(rng, K, W, DW, n_rows, 2)
    expected = golden(frames, K, W, coefs)
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if dut.out_valid.value:
                got.append(to_signed(int(dut.out_data.value), AW))

    cocotb.start_soon(collector())

    for px in frames:
        for i, x in enumerate(px):
            gap = rng.choice([0, 0, 0, 1, 2]) if i % 7 == 0 else 0
            if gap:
                dut.in_valid.value = 0
                await ClockCycles(dut.clk, gap)
            dut.in_valid.value = 1
            dut.frame_start.value = 1 if i == 0 else 0
            dut.in_data.value = x & ((1 << DW) - 1)
            while True:
                await ReadOnly()
                ready = bool(dut.in_ready.value)
                await RisingEdge(dut.clk)
                if ready:
                    break
            await Timer(1, unit="ns")
    dut.in_valid.value = 0
    dut.frame_start.value = 0
    await ClockCycles(dut.clk, 64 + 4 * K)

    assert len(got) == len(expected), f"expected {len(expected)} outputs, got {len(got)}"
    ref = [to_signed(v, AW) for v in expected]
    for i, (g, r) in enumerate(zip(got, ref)):
        assert g == r, f"output {i}: got {g} expected {r}"
