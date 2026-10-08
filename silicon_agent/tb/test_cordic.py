"""cocotb testbench for cordic_top: bit-exact against the Python shift-add model."""

import json
import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from silicon_agent.cordic import CordicArch, golden  # noqa: E402


def to_signed(v: int, w: int) -> int:
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


def vectors(a: CordicArch, rng: random.Random, n: int):
    W = a.width
    lo, hi = -(1 << (W - 1)), (1 << (W - 1)) - 1
    half = 1 << (W - 2)  # keep |x|,|y| < 2^(W-2) so the CORDIC gain (1.647) cannot overflow the guard-less path
    xs, ys, zs = [], [], []
    for i in range(n):
        if a.mode == "rotate":
            xs.append(int(half * 0.6) if i % 3 == 0 else rng.randint(-half // 2, half // 2)); ys.append(0 if i % 3 == 0 else rng.randint(-half // 2, half // 2)); zs.append(rng.randint(lo // 2, hi // 2))
        else:
            xs.append(rng.randint(-half // 2, half // 2)); ys.append(rng.randint(-half // 2, half // 2)); zs.append(0)
    return xs, ys, zs


@cocotb.test()
async def stream(dut):
    arch = json.loads(os.environ["SA_ARCH"])
    a = CordicArch.from_dict(arch)
    W = a.width
    n = int(os.environ.get("SA_SAMPLES", "150"))
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1; dut.in_valid.value = 0; dut.in_x.value = 0; dut.in_y.value = 0; dut.in_z.value = 0
    await ClockCycles(dut.clk, 3); dut.rst.value = 0; await RisingEdge(dut.clk)
    xs, ys, zs = vectors(a, rng, n)
    expected = golden(a, xs, ys, zs)
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk); await ReadOnly()
            if dut.out_valid.value:
                got.append((to_signed(int(dut.out_x.value), W), to_signed(int(dut.out_y.value), W), to_signed(int(dut.out_z.value), W)))
    cocotb.start_soon(collector())
    mask = (1 << W) - 1
    for i, (x, y, z) in enumerate(zip(xs, ys, zs)):
        if i % 5 == 0:
            dut.in_valid.value = 0; await ClockCycles(dut.clk, rng.randint(1, 3))
        dut.in_valid.value = 1; dut.in_x.value = x & mask; dut.in_y.value = y & mask; dut.in_z.value = z & mask
        stall = 0
        while True:
            await ReadOnly(); ready = bool(dut.in_ready.value); await RisingEdge(dut.clk)
            if ready:
                break
            stall += 1
            assert stall < 4000, "in_ready stalled for 4000 cycles (dead handshake)"
        await Timer(1, unit="ns")
    dut.in_valid.value = 0
    await ClockCycles(dut.clk, 40 + a.iterations * 2)
    assert len(got) == len(expected), f"expected {len(expected)} outputs, got {len(got)}"
    for k, (g, r) in enumerate(zip(got, expected)):
        assert g == r, f"sample {k}: got {g} expected {r} (in x={xs[k]} y={ys[k]} z={zs[k]})"
