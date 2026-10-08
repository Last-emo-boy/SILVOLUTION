"""cocotb testbench for matmul_top. Golden model in plain Python.

Streams several random tiles back-to-back and compares the drained C rows
to the reference product. Never assumes a latency -- it just collects
out_valid rows until all expected tiles have arrived.
"""

import json
import os
import random

import cocotb
from cocotb.clock import Clock
from coverage import Coverage
from cocotb.triggers import RisingEdge, ClockCycles


def to_signed(v: int, w: int) -> int:
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


def pack(vals, w):
    out = 0
    for i, v in enumerate(vals):
        out |= (v & ((1 << w) - 1)) << (i * w)
    return out


def unpack(raw: int, n: int, w: int):
    return [to_signed((raw >> (i * w)) & ((1 << w) - 1), w) for i in range(n)]


def golden(A, B):
    R, K, C = len(A), len(A[0]), len(B[0])
    return [[sum(A[i][k] * B[k][j] for k in range(K)) for j in range(C)] for i in range(R)]


@cocotb.test()
async def random_tiles(dut):
    arch = json.loads(os.environ["SA_ARCH"])
    R, C, W, AW = arch["pe_rows"], arch["pe_cols"], arch["data_width"], arch["acc_width"]
    n_tiles = int(os.environ.get("SA_TILES", "6"))
    min_k = int(os.environ["SA_MIN_K"])
    rng = random.Random(int(os.environ.get("SA_SEED", "1")))
    lo, hi = -(1 << (W - 1)), (1 << (W - 1)) - 1

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.in_valid.value = 0
    dut.in_last.value = 0
    dut.a_col.value = 0
    dut.b_row.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    await RisingEdge(dut.clk)

    expected = []
    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk)
            if dut.out_valid.value:
                got.append((int(dut.out_row.value), unpack(int(dut.c_row.value), C, AW)))

    cocotb.start_soon(collector())

    cov = Coverage("matmul", ["tile:extremes", "tile:random", "tile:first", "tile:back-to-back", "tile:after-gap"])

    for t in range(n_tiles):
        K = rng.choice([min_k, min_k + 3, 2 * min_k])
        A = [[rng.randint(lo, hi) for _ in range(K)] for _ in range(R)]
        B = [[rng.randint(lo, hi) for _ in range(C)] for _ in range(K)]
        if t == 0:  # extremes on the first tile
            A = [[lo] * K for _ in range(R)]
            B = [[lo] * C for _ in range(K)]
        expected.append(golden(A, B))
        cov.hit("tile:extremes" if t == 0 else "tile:random")
        cov.hit("tile:back-to-back" if t % 2 == 0 and t > 0 else "tile:after-gap" if t % 2 == 1 else "tile:first")
        for k in range(K):
            dut.in_valid.value = 1
            dut.in_last.value = 1 if k == K - 1 else 0
            dut.a_col.value = pack([A[i][k] for i in range(R)], W)
            dut.b_row.value = pack([B[k][j] for j in range(C)], W)
            await RisingEdge(dut.clk)
        dut.in_valid.value = 0
        dut.in_last.value = 0
        # tiles 1..n-1 alternate between immediate back-to-back and idle gaps
        if t % 2 == 1:
            await ClockCycles(dut.clk, rng.randint(1, 5))

    await ClockCycles(dut.clk, 4 * (R + C) + 40)
    cov.report()

    assert len(got) == n_tiles * R, f"expected {n_tiles * R} output rows, got {len(got)}"
    for t in range(n_tiles):
        for i in range(R):
            row_idx, row = got[t * R + i]
            assert row_idx == i, f"tile {t}: row index {row_idx} != {i}"
            ref = [to_signed(v, AW) for v in expected[t][i]]
            assert row == ref, f"tile {t} row {i}: got {row} expected {ref}"
