"""cocotb testbench for fft_top: bit-exact against the fixed-point SDF model over several frames."""

import json
import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from silicon_agent.fft import FftArch, golden  # noqa: E402


def to_signed(v: int, w: int) -> int:
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


def frames_for(a: FftArch, rng: random.Random, n_frames: int):
    N, DW = a.n, a.data_width
    hi = (1 << (DW - 1)) - 1
    fr = []
    for f in range(n_frames):
        if f == 0:   # impulse: flat spectrum
            fr.append([(hi // 2 if i == 0 else 0, 0) for i in range(N)])
        elif f == 1: # single tone at bin 3
            import math
            fr.append([(int(hi * 0.5 * math.cos(2 * math.pi * 3 * i / N)), int(hi * 0.5 * math.sin(2 * math.pi * 3 * i / N))) for i in range(N)])
        else:
            fr.append([(rng.randint(-hi // 2, hi // 2), rng.randint(-hi // 2, hi // 2)) for _ in range(N)])
    return fr


@cocotb.test()
async def stream(dut):
    a = FftArch.from_dict(json.loads(os.environ["SA_ARCH"]))
    N, DW, OW = a.n, a.data_width, a.out_width
    n_frames = int(os.environ.get("SA_FRAMES", "3"))
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1; dut.in_valid.value = 0; dut.in_first.value = 0; dut.in_re.value = 0; dut.in_im.value = 0
    await ClockCycles(dut.clk, 3); dut.rst.value = 0; await RisingEdge(dut.clk)
    frames = frames_for(a, rng, n_frames)
    expected = golden(a, frames)
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk); await ReadOnly()
            if dut.out_valid.value:
                got.append((to_signed(int(dut.out_re.value), OW), to_signed(int(dut.out_im.value), OW)))
    cocotb.start_soon(collector())
    mask = (1 << DW) - 1
    for fr in frames:
        for i, (re, im) in enumerate(fr):
            if i % 9 == 0 and rng.random() < 0.3:
                dut.in_valid.value = 0; await ClockCycles(dut.clk, rng.randint(1, 2))
            dut.in_valid.value = 1; dut.in_first.value = 1 if i == 0 else 0; dut.in_re.value = re & mask; dut.in_im.value = im & mask
            stall = 0
            while True:
                await ReadOnly(); ready = bool(dut.in_ready.value); await RisingEdge(dut.clk)
                if ready:
                    break
                stall += 1
                assert stall < 4000, "in_ready stalled for 4000 cycles (dead handshake)"
            await Timer(1, unit="ns")
    dut.in_valid.value = 0; dut.in_first.value = 0
    # flush: a zero frame pushes the last real frame out (same as the golden model's flush)
    for i in range(N):
        dut.in_valid.value = 1; dut.in_first.value = 1 if i == 0 else 0; dut.in_re.value = 0; dut.in_im.value = 0
        await RisingEdge(dut.clk); await Timer(1, unit="ns")
    dut.in_valid.value = 0; dut.in_first.value = 0
    await ClockCycles(dut.clk, 40 + (2 * N if a.bit_reverse else 0))
    assert len(got) >= len(expected), f"expected at least {len(expected)} outputs, got {len(got)}"
    got = got[:len(expected)]
    bad = [(k, g, r) for k, (g, r) in enumerate(zip(got, expected)) if g != r]
    assert not bad, f"{len(bad)} mismatches, first: sample {bad[0][0]}: got {bad[0][1]} expected {bad[0][2]}"
