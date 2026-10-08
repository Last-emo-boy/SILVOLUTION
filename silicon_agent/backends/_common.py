"""Shared helpers for backend plugins: tool environment, subprocess runner, iverilog netlist sims."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(os.environ.get("SA_ROOT", Path(__file__).resolve().parent.parent.parent))
TOOLS = Path(os.environ.get("SA_TOOLS", ROOT / "tools" / "oss-cad-suite"))
TB_DIR = Path(__file__).resolve().parent.parent / "tb"
CACHE = ROOT / "cache"


def tool_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{TOOLS / 'bin'}{os.pathsep}{TOOLS / 'py3bin'}{os.pathsep}{env.get('PATH', '')}"
    return env


def run(cmd: list[str], cwd: Path, log: Path, timeout: int = 1800, env: dict | None = None) -> subprocess.CompletedProcess:
    with log.open("w") as f:
        return subprocess.run(cmd, cwd=cwd, env=env or tool_env(), stdout=f, stderr=subprocess.STDOUT, timeout=timeout, text=True)


def flat_sim_lib(family: str, drop: tuple[str, ...] = ()) -> Path:
    """yosys's <family>/cells_sim.v may include headers twice (ecp5 does); preprocess once and drop
    duplicate modules plus any listed in `drop` (black boxes we model ourselves). Cached under cache/."""
    lib = CACHE / f"{family}_cells_flat{'_' + '_'.join(drop) if drop else ''}.v"
    if lib.exists():
        return lib
    d = TOOLS / "share" / "yosys" / family
    flat = subprocess.run(["iverilog", "-g2012", "-E", "-I", str(d), "-o", "/dev/stdout", str(d / "cells_sim.v")],
                          capture_output=True, text=True, env=tool_env()).stdout
    out, seen, skip = [], set(), False
    for line in flat.splitlines():
        m = re.match(r"\s*module\s+(\w+)", line)
        if m:
            skip = m.group(1) in seen or m.group(1) in drop
            seen.add(m.group(1))
        if not skip:
            out.append(line)
        if re.match(r"\s*endmodule", line):
            skip = False
    text = "\n".join(out) + "\n"
    # Icarus never runs an `always @*` whose inputs are all constants (nothing ever changes), so a cell whose
    # inputs synthesis tied to GND/VCC keeps X on its `reg` outputs forever (a row-serial conv on Gowin has
    # an ALU fed only by constants). Evaluating every such block once at time 0 gives always_comb semantics.
    text = _comb_as_assign(text)
    CACHE.mkdir(exist_ok=True)
    lib.write_text(text)
    return lib


# yosys's Gowin ALU models its sum/carry with `reg S, C` in an `always @*` whose body is a case on the ALU_MODE
# parameter. Icarus only evaluates @* on an input *change*, so a cell whose inputs are constant nets (GND/VCC,
# or logic that settles during time 0) keeps S/C at X forever; the real cell is purely combinational. The
# equivalent continuous assignments have no such hole.
_GOWIN_ALU_COMB = """
wire S = (ALU_MODE == 0) ? (I0 ^ I1) :
         (ALU_MODE == 1) ? (I0 ^ ~I1) :
         (ALU_MODE == 2) ? (I3 ? I0 ^ I1 : I0 ^ ~I1) :
         (ALU_MODE == 3) ? (I0 ^ ~I1) :
         (ALU_MODE == 4) ? (I0 ^ ~I1) :
         (ALU_MODE == 5) ? (~I0 ^ I1) :
         (ALU_MODE == 6) ? I0 :
         (ALU_MODE == 7) ? ~I0 :
         (ALU_MODE == 8) ? (I3 ? I0 : ~I0) :
         (ALU_MODE == 9) ? ((I0 & I1) ^ I3) : 1'bx;
wire C = (ALU_MODE == 0) ? I0 :
         (ALU_MODE == 1) ? I0 :
         (ALU_MODE == 2) ? I0 :
         (ALU_MODE == 3) ? 1'b1 :
         (ALU_MODE == 4) ? I0 :
         (ALU_MODE == 5) ? I1 :
         (ALU_MODE == 6) ? 1'b0 :
         (ALU_MODE == 7) ? 1'b1 :
         (ALU_MODE == 8) ? I0 :
         (ALU_MODE == 9) ? (I0 & I1) : 1'bx;
"""


def _comb_as_assign(text: str) -> str:
    """Replace the Gowin ALU's `reg S, C; always @* case (ALU_MODE) ...` with continuous assignments (see above).
    Other families' cells are written with assigns already; anything unrecognised is left alone."""
    m = re.search(r"^module ALU \(SUM, COUT, I0, I1, I3, CIN\);.*?^endmodule", text, re.M | re.S)
    if not m:
        return text
    body = m.group(0)
    # the wires must be declared before `assign SUM = S ^ CIN` uses them: put them where `reg S, C;` was
    body = body.replace("reg S, C;", _GOWIN_ALU_COMB)
    body = re.sub(r"^always @\* begin\n\tcase \(ALU_MODE\).*?^\tendcase\nend\n", "", body, flags=re.M | re.S)
    if "always @*" in body:
        raise ValueError("gowin ALU model changed shape; update _comb_as_assign")
    return text[:m.start()] + body + text[m.end():]


def iverilog_netlist_sim(d, arch, work: Path, fmax_target: float, netlist: Path, libs: list[Path], on_stage=None) -> dict:
    """Stage 2 (post-synthesis functional) with iverilog: the yosys-mapped netlist against the design's
    vector testbench. Open-source P&R outputs have no simulation model / SDF, so stages 3-5 stay unavailable."""
    sim = work / "nsim"
    sim.mkdir(parents=True, exist_ok=True)
    stim, exp = d.vectors(arch)
    (sim / "stim.hex").write_text("\n".join(stim) + "\n")
    (sim / "exp.hex").write_text("\n".join(exp) + "\n")
    period = 1000.0 / fmax_target
    (sim / "tb.sv").write_text(d.tb(arch, period))
    label = "Post-synthesis functional"
    if not netlist.exists():
        return {"post_synth_func": {"passed": None, "label": label, "period_ns": period, "log": "netlist not produced"}}
    log = sim / "post_synth_func.log"
    srcs = [str(sim / "tb.sv"), str(netlist)] + [str(l) for l in libs] + [str(TB_DIR / f) for f in d.extra_sim_sources]
    with log.open("w") as f:
        c = subprocess.run(["iverilog", "-g2012", "-o", "ps.vvp", *srcs], cwd=sim, env=tool_env(), stdout=f, stderr=subprocess.STDOUT, text=True, timeout=600)
        r = subprocess.run(["vvp", "ps.vvp"], cwd=sim, env=tool_env(), stdout=f, stderr=subprocess.STDOUT, text=True, timeout=1800) if c.returncode == 0 else None
    text = log.read_text()
    passed = c.returncode == 0 and r is not None and "RESULT PASS" in text
    tail = "\n".join(l for l in text.splitlines() if re.search(r"RESULT|MISMATCH|COUNT|error", l))[-1500:]
    res = {"post_synth_func": {"passed": passed, "label": label, "period_ns": round(period, 3), "log": tail}}
    if on_stage:
        on_stage("post_synth_func", res["post_synth_func"])
    return res


def nextpnr_report(out: Path, lut_names: tuple[str, ...], ff_names: tuple[str, ...], dsp_names: tuple[str, ...], bram_names: tuple[str, ...], classify) -> dict:
    """Common nextpnr --report parsing: utilization by cell family + the critical path, one entry per
    logic cell with the preceding routing folded in and carry chains merged."""
    import json
    report = json.loads((out / "report.json").read_text())
    util = report["utilization"]

    def used(names):
        return sum(util[n]["used"] for n in names if n in util)

    fmax = 0.0
    for v in report.get("fmax", {}).values():
        fmax = v["achieved"]
    paths = report.get("critical_paths", [])
    entries: list[dict] = []
    total = 0.0
    if paths:
        segs = paths[0]["path"]
        total = sum(s["delay"] for s in segs)
        pending_route = 0.0
        for s in segs:
            if s["type"] == "routing":
                pending_route += s["delay"]
                continue
            kind, rtl_name = classify(s["to"]["cell"])
            if entries and entries[-1]["kind"] == kind and entries[-1]["rtl"] == rtl_name and kind in ("carry chain", "logic", "LUT4"):
                entries[-1]["logic_ns"] += s["delay"]
                entries[-1]["route_ns"] += pending_route
                entries[-1]["cells"] += 1
            else:
                entries.append({"kind": kind, "rtl": rtl_name, "logic_ns": s["delay"], "route_ns": pending_route, "cells": 1})
            pending_route = 0.0
        for e in entries:
            e["logic_ns"] = round(e["logic_ns"], 2)
            e["route_ns"] = round(e["route_ns"], 2)
    return {
        "fmax_mhz": fmax,
        "lut": used(lut_names), "ff": used(ff_names), "dsp": used(dsp_names), "bram": used(bram_names),
        "critical_path": ([{"total_ns": round(total, 2)}] + entries) if paths else [],
    }


SEEDS = int(os.environ.get("SA_SEEDS", "3"))  # P&R seeds per design on the open-source flows (median Fmax is reported)


def nextpnr_seeds(cmd: list[str], out: Path, n: int = SEEDS) -> list[float]:
    """Run `cmd` (a nextpnr command line without --seed/--report) once per seed, writing report_<seed>.json;
    leave the median run's report as report.json and its log as nextpnr.log. Returns the per-seed Fmax list."""
    import json
    import shutil
    fm = []
    for seed in range(1, n + 1):
        rep = out / f"report_{seed}.json"
        p = run(cmd + ["--seed", str(seed), "--report", str(rep)], out, out / f"nextpnr_{seed}.log")
        if p.returncode != 0:
            raise RuntimeError(f"nextpnr failed (seed {seed}), see {out / f'nextpnr_{seed}.log'}")
        r = json.loads(rep.read_text())
        f = 0.0
        for v in r.get("fmax", {}).values():
            f = v["achieved"]
        fm.append(round(f, 2))
    order = sorted(range(n), key=lambda i: fm[i])
    med = order[len(order) // 2] if n % 2 else order[len(order) // 2 - 1]  # lower median for even n: conservative
    shutil.copy(out / f"report_{med + 1}.json", out / "report.json")
    shutil.copy(out / f"nextpnr_{med + 1}.log", out / "nextpnr.log")
    return fm
