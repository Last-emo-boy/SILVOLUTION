"""cocotb testbench for graph_top: node-by-node golden (each node's own golden model with the adapter in between)."""

import json
import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from silicon_agent.graph import GraphArch, _io, data_width, out_width  # noqa: E402
from test_fir import golden as fir_golden, to_signed  # noqa: E402


def adapt(vals, ow, nw, mode):
    out = []
    for v in vals:
        v = to_signed(v, ow)
        if ow <= nw:
            out.append(v)
        elif mode == "saturate":
            out.append(max(-(1 << (nw - 1)), min((1 << (nw - 1)) - 1, v)))
        else:
            sh = ow - nw
            if mode == "round":
                v = v + (1 << (sh - 1))
            out.append(to_signed(v >> sh, nw))
    return out


def node_golden(kind, ir, xs, mode_in_width):
    if kind == "fir":
        return fir_golden(xs, list(ir.coefs))
    if kind == "decim":
        from silicon_agent.decim import golden
        return golden(ir, xs)
    if kind == "iir":
        from silicon_agent.iir import golden
        return golden(ir, xs)
    if kind == "nco":
        from silicon_agent.nco import golden
        return [o[0] for o in golden(ir, xs, [1 << (ir.phase_width - 3)] * len(xs))]
    if kind == "cordic":
        from silicon_agent.cordic import golden
        return [o[0] for o in golden(ir, xs, [0] * len(xs), [0] * len(xs))]
    if kind == "fft":
        from silicon_agent.fft import golden
        N = ir.n
        frames = [[(x, 0) for x in xs[i:i + N]] for i in range(0, len(xs) - len(xs) % N, N)]
        return [o[0] for o in golden(ir, frames)] if frames else []
    if kind == "conv2d":
        from test_conv import golden
        W = ir.width; n_rows = len(xs) // W
        return golden([xs[:W * n_rows]], ir.k, W, list(ir.coefs))
    raise ValueError(kind)


def graph_golden(a: GraphArch, xs):
    cur = xs
    for i, n in enumerate(a.nodes):
        ir = a.node_ir(i); io = _io(n["kind"], ir)
        ys = node_golden(n["kind"], ir, cur, io["in"][1])
        if i < len(a.nodes) - 1:
            nxt = _io(a.nodes[i + 1]["kind"], a.node_ir(i + 1))["in"][1]
            cur = adapt(ys, io["out"][1], nxt, a.adapt)
        else:
            return [to_signed(y, io["out"][1]) for y in ys]
    return cur


@cocotb.test()
async def stream(dut):
    a = GraphArch.from_dict(json.loads(os.environ["SA_ARCH"]))
    DW, AW = data_width(a), out_width(a)
    n = int(os.environ.get("SA_SAMPLES", "160"))
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1; dut.in_valid.value = 0; dut.in_frame.value = 0; dut.in_data.value = 0
    await ClockCycles(dut.clk, 3); dut.rst.value = 0; await RisingEdge(dut.clk)
    lo, hi = -(1 << (DW - 2)), (1 << (DW - 2)) - 1    # half scale: keeps every node's output in range
    # frame-sized stimulus if any node is an FFT (whole frames) or conv (whole rows)
    unit = 1
    for i, nd in enumerate(a.nodes):
        if nd["kind"] == "fft": unit = max(unit, a.node_ir(i).n)
        if nd["kind"] == "conv2d": unit = max(unit, a.node_ir(i).width)
        if nd["kind"] == "decim": unit = max(unit, a.node_ir(i).decim)
    n = max(unit * 2, (n // unit) * unit)
    xs = [rng.randint(lo, hi) for _ in range(n)]
    expected = graph_golden(a, xs)
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk); await ReadOnly()
            if dut.out_valid.value:
                got.append(to_signed(int(dut.out_data.value), AW))
    cocotb.start_soon(collector())
    for i, x in enumerate(xs):
        if i % 11 == 0 and rng.random() < 0.3:
            dut.in_valid.value = 0; await ClockCycles(dut.clk, rng.randint(1, 2))
        dut.in_valid.value = 1; dut.in_frame.value = 1 if i == 0 else 0; dut.in_data.value = x & ((1 << DW) - 1)
        stall = 0
        while True:
            await ReadOnly(); ready = bool(dut.in_ready.value); await RisingEdge(dut.clk)
            if ready:
                break
            stall += 1
            assert stall < 4000, "in_ready stalled for 4000 cycles (dead handshake)"
        await Timer(1, unit="ns")
    # flush with zeros for the longest node latency (FFT needs a frame)
    for i in range(unit):
        dut.in_valid.value = 1; dut.in_frame.value = 0; dut.in_data.value = 0
        stall = 0
        while True:
            await ReadOnly(); ready = bool(dut.in_ready.value); await RisingEdge(dut.clk)
            if ready:
                break
            stall += 1
            assert stall < 4000, "in_ready stalled for 4000 cycles (dead handshake)"
        await Timer(1, unit="ns")
    dut.in_valid.value = 0
    await ClockCycles(dut.clk, 400 + 3 * unit)
    assert len(got) >= len(expected), f"expected at least {len(expected)} outputs, got {len(got)}"
    got = got[:len(expected)]
    bad = [(k, g, r) for k, (g, r) in enumerate(zip(got, expected)) if g != r]
    assert not bad, f"{len(bad)} mismatches, first: {bad[0]}"
