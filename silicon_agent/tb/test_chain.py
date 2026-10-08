"""cocotb testbench for fir_chain_top: cascaded FIR stages with inter-stage width reduction.
Golden: stage-by-stage direct convolution, each output truncated/rounded to the next stage's data width
(arithmetic shift by acc_width - next data_width)."""

import json
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Timer

from test_fir import golden as fir_golden, to_signed


def reduce(vals, acc_w, next_dw, mode):
    shift = acc_w - next_dw
    out = []
    for v in vals:
        v = to_signed(v, acc_w)
        if shift > 0:
            v = (v + (1 << (shift - 1))) >> shift if mode == "round" else v >> shift
        out.append(to_signed(v, next_dw))
    return out


def golden(xs, stages, mode):
    cur = xs
    for i, st in enumerate(stages):
        ys = fir_golden(cur, st["coefs"])
        if i < len(stages) - 1:
            cur = reduce(ys, st["acc_width"], stages[i + 1]["data_width"], mode)
        else:
            return ys
    return cur


@cocotb.test()
async def stream(dut):
    arch = json.loads(os.environ["SA_ARCH"])
    stages, mode = arch["stages"], arch.get("round_mode", "truncate")
    DW, AW = stages[0]["data_width"], stages[-1]["acc_width"]
    n_samples = int(os.environ.get("SA_SAMPLES", "200"))
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))
    lo, hi = -(1 << (DW - 1)), (1 << (DW - 1)) - 1
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1; dut.in_valid.value = 0; dut.in_data.value = 0
    await ClockCycles(dut.clk, 3); dut.rst.value = 0; await RisingEdge(dut.clk)
    taps0 = len(stages[0]["coefs"])
    xs = [hi] * taps0 + [lo] * taps0 + [rng.randint(lo, hi) for _ in range(n_samples)]
    expected = golden(xs, stages, mode)
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk); await ReadOnly()
            if dut.out_valid.value:
                got.append(to_signed(int(dut.out_data.value), AW))
    cocotb.start_soon(collector())
    burst_from = len(xs) - n_samples // 2
    for i, x in enumerate(xs):
        gap = 0 if i >= burst_from else rng.choice([0, 0, 1, 3])
        if gap:
            dut.in_valid.value = 0; await ClockCycles(dut.clk, gap)
        dut.in_valid.value = 1; dut.in_data.value = x & ((1 << DW) - 1)
        stall = 0
        while True:
            await ReadOnly(); ready = bool(dut.in_ready.value); await RisingEdge(dut.clk)
            if ready:
                break
            stall += 1
            assert stall < 4000, "in_ready stalled for 4000 cycles (dead handshake)"
        await Timer(1, unit="ns")
    dut.in_valid.value = 0
    await ClockCycles(dut.clk, 200 + 8 * sum(s.get("fold", 1) for s in stages))
    assert len(got) == len(expected), f"expected {len(expected)} outputs, got {len(got)}"
    ref = [to_signed(v, AW) for v in expected]
    for k, (g, r) in enumerate(zip(got, ref)):
        assert g == r, f"sample {k}: got {g} expected {r}"
