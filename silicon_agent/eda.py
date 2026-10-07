"""EDA environment: simulate (cocotb+iverilog), synthesize (yosys), place & route
(nextpnr-ecp5). Results are cached by RTL hash so a repeated design is free.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .emit import emit_matmul, min_k_full_rate
from .ir import MatmulArch, Metrics

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools" / "oss-cad-suite"
TB_DIR = Path(__file__).resolve().parent / "tb"
CACHE = ROOT / "cache"

DEVICE = {"part": "45k", "package": "CABGA381", "speed": "6", "lut": 43848, "dsp": 72, "bram": 108}


def tool_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{TOOLS / 'bin'}{os.pathsep}{TOOLS / 'py3bin'}{os.pathsep}{env.get('PATH', '')}"
    return env


def run(cmd: list[str], cwd: Path, log: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    with log.open("w") as f:
        return subprocess.run(cmd, cwd=cwd, env=tool_env(), stdout=f, stderr=subprocess.STDOUT, timeout=timeout, text=True)


# ---------------------------------------------------------------- simulation
def simulate(arch: MatmulArch, work: Path, tiles: int = 6, seed: int = 1) -> tuple[bool, str]:
    rtl = work / "rtl" / "matmul_top.sv"
    sim_dir = work / "sim"
    sim_dir.mkdir(parents=True, exist_ok=True)
    env = tool_env()
    env.update({
        "SA_ARCH": json.dumps(arch.to_dict()),
        "SA_MIN_K": str(min_k_full_rate(arch)),
        "SA_TILES": str(tiles),
        "SA_SEED": str(seed),
        "PYTHONPATH": str(TB_DIR),
    })
    driver = f"""
from pathlib import Path
from cocotb_tools.runner import get_runner
from cocotb_tools.check_results import get_results
r = get_runner("icarus")
r.build(sources=[Path({str(rtl)!r}), Path({str(TB_DIR / "mult18x18d_sim.v")!r})], hdl_toplevel="matmul_top", build_dir={str(sim_dir / 'build')!r},
        build_args=["-g2012"], timescale=("1ns", "1ps"), always=True)
xml = r.test(test_module="test_matmul", hdl_toplevel="matmul_top", build_dir={str(sim_dir / 'build')!r},
             test_dir={str(sim_dir)!r}, timescale=("1ns", "1ps"))
total, failed = get_results(xml)
print(f"COCOTB_RESULT total={{total}} failed={{failed}}")
raise SystemExit(1 if failed else 0)
"""
    log = sim_dir / "sim.log"
    with log.open("w") as f:
        p = subprocess.run([str(TOOLS / "py3bin" / "python3"), "-c", driver], cwd=sim_dir, env=env,
                           stdout=f, stderr=subprocess.STDOUT, text=True, timeout=1800)
    text = log.read_text()
    passed = p.returncode == 0 and "failed=0" in text
    # keep only the interesting tail for the LLM
    tail = "\n".join(line for line in text.splitlines() if re.search(r"(FAIL|ERROR|Error|error:|assert|COCOTB_RESULT|PASS)", line))[-4000:]
    return passed, tail


# ---------------------------------------------------------------- synthesis + P&R
def synth_pnr(work: Path, fmax_target: float) -> dict:
    rtl = work / "rtl" / "matmul_top.sv"
    out = work / "pnr"
    out.mkdir(parents=True, exist_ok=True)
    ys = out / "synth.ys"
    ys.write_text(
        f"plugin -i slang\n"
        f"read_slang --top matmul_top {rtl} {TOOLS / 'share' / 'yosys' / 'lattice' / 'cells_bb_ecp5.v'}\n"
        f"synth_ecp5 -top matmul_top -json {out / 'synth.json'}\n"
        f"tee -o {out / 'stat.json'} stat -json\n"
    )
    p = run(["yosys", "-q", "-s", str(ys)], out, out / "yosys.log")
    if p.returncode != 0:
        raise RuntimeError(f"yosys failed, see {out / 'yosys.log'}")

    synth = synth_counts(out / "stat.json")
    if synth["dsp"] > DEVICE["dsp"] or synth["lut"] > DEVICE["lut"]:
        # does not fit: skip P&R, report synthesis counts with fmax=0 so the planner sees the overflow
        return {**synth, "fmax_mhz": 0.0, "critical_path": [], "fits": False}

    p = run([
        "nextpnr-ecp5", f"--{DEVICE['part']}", "--package", DEVICE["package"], "--speed", DEVICE["speed"],
        "--json", str(out / "synth.json"), "--out-of-context", "--freq", str(fmax_target),
        "--timing-allow-fail", "--report", str(out / "report.json"), "--detailed-timing-report",
        "--seed", "1",
    ], out, out / "nextpnr.log")
    if p.returncode != 0:
        raise RuntimeError(f"nextpnr failed, see {out / 'nextpnr.log'}")
    return {**parse_reports(out), "fits": True}


def synth_counts(stat_json: Path) -> dict:
    """Post-synthesis cell counts from `yosys stat -json` (pre-P&R, so LUT is an estimate)."""
    st = json.loads(stat_json.read_text())
    cells = st["design"]["num_cells_by_type"] if "design" in st else next(iter(st["modules"].values()))["num_cells_by_type"]
    return {
        "lut": sum(v for k, v in cells.items() if k.startswith("LUT") or k in ("CCU2C", "PFUMX", "L6MUX21")),
        "ff": cells.get("TRELLIS_FF", 0),
        "dsp": cells.get("MULT18X18D", 0),
        "bram": cells.get("DP16KD", 0),
    }


def parse_reports(out: Path) -> dict:
    report = json.loads((out / "report.json").read_text())
    util = report["utilization"]

    def used(*names):
        return sum(util[n]["used"] for n in names if n in util)

    lut = used("TRELLIS_COMB")
    ff = used("TRELLIS_FF")
    dsp = used("MULT18X18D")
    bram = used("DP16KD")

    fmax = 0.0
    for clk, v in report.get("fmax", {}).items():
        fmax = v["achieved"]

    cp = summarize_critical_path(report.get("critical_paths", []))
    return {"fmax_mhz": fmax, "lut": lut, "ff": ff, "dsp": dsp, "bram": bram, "critical_path": cp}


_PRIMS = ("MULT18X18D", "CCU2C", "LUT4", "DP16KD", "ALU54B", "TRELLIS_FF")


def _classify(cell: str) -> tuple[str, str]:
    """('kind', 'rtl name') for a yosys-mangled cell name.

    Mangled names look like 'g_row[2].g_col[2].u_pe.acc_out_TRELLIS_FF_Q_DI_CCU2C_S0_COUT...$CCU2_COMB1'.
    The primitive that actually sits on the path is the last one mentioned; the RTL
    name is everything before the first primitive suffix.
    """
    base = cell.split("$")[0]
    hits = [(base.rfind("_" + p), p) for p in _PRIMS if ("_" + p) in base]
    if not hits:
        return ("logic", cell)
    kind = max(hits)[1]
    first = min(hits)[0]
    return (kind, base[:first])


def summarize_critical_path(paths: list[dict]) -> list[dict]:
    """Collapse nextpnr's per-segment path into a short, LLM-readable list:
    one entry per logic cell, with routing delay folded into the following cell.
    Consecutive cells of the same kind on the same RTL signal (carry chains) merge.
    """
    if not paths:
        return []
    segs = paths[0]["path"]
    total = sum(s["delay"] for s in segs)
    entries = []
    pending_route = 0.0
    for s in segs:
        if s["type"] == "routing":
            pending_route += s["delay"]
            continue
        kind, rtl_name = _classify(s["to"]["cell"])
        if kind == "CCU2C":
            kind = "carry chain"
        if entries and entries[-1]["kind"] == kind and entries[-1]["rtl"] == rtl_name and kind != "MULT18X18D":
            entries[-1]["logic_ns"] += s["delay"]
            entries[-1]["route_ns"] += pending_route
            entries[-1]["cells"] += 1
        else:
            entries.append({"kind": kind, "rtl": rtl_name, "logic_ns": s["delay"], "route_ns": pending_route, "cells": 1})
        pending_route = 0.0
    for e in entries:
        e["logic_ns"] = round(e["logic_ns"], 2)
        e["route_ns"] = round(e["route_ns"], 2)
    return [{"total_ns": round(total, 2)}] + entries


# ---------------------------------------------------------------- top-level evaluate
def evaluate(arch: MatmulArch, fmax_target: float, runs_dir: Path, tiles: int = 6) -> Metrics:
    rtl_text = emit_matmul(arch)
    h = hashlib.sha256(rtl_text.encode()).hexdigest()[:16]
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / f"{h}.json"
    if cached.exists():
        return Metrics(**json.loads(cached.read_text()))

    work = runs_dir / h
    (work / "rtl").mkdir(parents=True, exist_ok=True)
    (work / "rtl" / "matmul_top.sv").write_text(rtl_text)
    (work / "arch.json").write_text(json.dumps(arch.to_dict(), indent=2))

    sim_ok, sim_log = simulate(arch, work, tiles=tiles)
    if not sim_ok:
        m = Metrics(fmax_mhz=0, lut=0, ff=0, dsp=0, bram=0, critical_path=[], sim_passed=False, sim_log=sim_log)
    else:
        r = synth_pnr(work, fmax_target)
        m = Metrics(**r, sim_passed=True, sim_log=sim_log)
    cached.write_text(json.dumps(asdict(m)))
    (work / "metrics.json").write_text(json.dumps(asdict(m), indent=2))
    return m


if __name__ == "__main__":
    a = MatmulArch.from_dict(json.loads(sys.argv[1])) if len(sys.argv) > 1 else MatmulArch()
    m = evaluate(a, 100.0, ROOT / "runs")
    print(json.dumps({k: v for k, v in asdict(m).items() if k != "sim_log"}, indent=2))
    print(m.sim_log)
