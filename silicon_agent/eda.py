"""EDA environment: simulate, synthesize, place & route, and parse reports.

Two backends:
  ecp5   -- yosys + nextpnr-ecp5 (open source, ~15-50 s per design)
  xc7s50 -- AMD Vivado, Spartan-7 XC7S50 (needs a Vivado install, ~2-5 min)

Simulation (cocotb + iverilog) is the same for both. Results are cached by
(backend, RTL hash) so a repeated design is free.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from .emit import emit_matmul, min_k_full_rate
from .ir import MatmulArch, Metrics

ROOT = Path(os.environ.get("SA_ROOT", Path(__file__).resolve().parent.parent))
TOOLS = Path(os.environ.get("SA_TOOLS", ROOT / "tools" / "oss-cad-suite"))
TB_DIR = Path(__file__).resolve().parent / "tb"
CACHE = ROOT / "cache"
CACHE_VERSION = "2"  # bump when report parsing changes; invalidates cached metrics

BACKENDS = {
    "ecp5": {
        "name": "Lattice ECP5 LFE5U-45F-6",
        "part": "45k", "package": "CABGA381", "speed": "6",
        "lut": 43848, "dsp": 72, "bram": 108, "lut_kind": "LUT4", "dsp_kind": "MULT18X18D",
    },
    "xc7s50": {
        "name": "AMD Spartan-7 XC7S50-1 (CSGA324)",
        "part": "xc7s50csga324-1",
        "lut": 32600, "dsp": 120, "bram": 75, "lut_kind": "LUT6", "dsp_kind": "DSP48E1",
    },
}


def tool_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{TOOLS / 'bin'}{os.pathsep}{TOOLS / 'py3bin'}{os.pathsep}{env.get('PATH', '')}"
    return env


def run(cmd: list[str], cwd: Path, log: Path, timeout: int = 1800, env: dict | None = None) -> subprocess.CompletedProcess:
    with log.open("w") as f:
        return subprocess.run(cmd, cwd=cwd, env=env or tool_env(), stdout=f, stderr=subprocess.STDOUT, timeout=timeout, text=True)


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
    tail = "\n".join(line for line in text.splitlines() if re.search(r"(FAIL|ERROR|Error|error:|assert|COCOTB_RESULT|PASS)", line))[-4000:]
    return passed, tail


# ---------------------------------------------------------------- ECP5: yosys + nextpnr
def synth_pnr_ecp5(work: Path, fmax_target: float) -> dict:
    dev = BACKENDS["ecp5"]
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

    synth = synth_counts_ecp5(out / "stat.json")
    if synth["dsp"] > dev["dsp"] or synth["lut"] > dev["lut"]:
        return {**synth, "fmax_mhz": 0.0, "critical_path": [], "fits": False}

    p = run([
        "nextpnr-ecp5", f"--{dev['part']}", "--package", dev["package"], "--speed", dev["speed"],
        "--json", str(out / "synth.json"), "--out-of-context", "--freq", str(fmax_target),
        "--timing-allow-fail", "--report", str(out / "report.json"), "--detailed-timing-report",
        "--seed", "1",
    ], out, out / "nextpnr.log")
    if p.returncode != 0:
        raise RuntimeError(f"nextpnr failed, see {out / 'nextpnr.log'}")
    return {**parse_reports_ecp5(out), "fits": True}


def synth_counts_ecp5(stat_json: Path) -> dict:
    st = json.loads(stat_json.read_text())
    cells = st["design"]["num_cells_by_type"] if "design" in st else next(iter(st["modules"].values()))["num_cells_by_type"]
    return {
        "lut": sum(v for k, v in cells.items() if k.startswith("LUT") or k in ("CCU2C", "PFUMX", "L6MUX21")),
        "ff": cells.get("TRELLIS_FF", 0),
        "dsp": cells.get("MULT18X18D", 0),
        "bram": cells.get("DP16KD", 0),
    }


def parse_reports_ecp5(out: Path) -> dict:
    report = json.loads((out / "report.json").read_text())
    util = report["utilization"]

    def used(*names):
        return sum(util[n]["used"] for n in names if n in util)

    fmax = 0.0
    for clk, v in report.get("fmax", {}).items():
        fmax = v["achieved"]
    return {
        "fmax_mhz": fmax,
        "lut": used("TRELLIS_COMB"), "ff": used("TRELLIS_FF"), "dsp": used("MULT18X18D"), "bram": used("DP16KD"),
        "critical_path": summarize_critical_path_ecp5(report.get("critical_paths", [])),
    }


_PRIMS = ("MULT18X18D", "CCU2C", "LUT4", "DP16KD", "ALU54B", "TRELLIS_FF")


def _classify_ecp5(cell: str) -> tuple[str, str]:
    base = cell.split("$")[0]
    hits = [(base.rfind("_" + p), p) for p in _PRIMS if ("_" + p) in base]
    if not hits:
        return ("logic", cell)
    return (max(hits)[1], base[:min(hits)[0]])


def summarize_critical_path_ecp5(paths: list[dict]) -> list[dict]:
    """One entry per logic cell with the preceding routing folded in; carry chains merged."""
    if not paths:
        return []
    segs = paths[0]["path"]
    total = sum(s["delay"] for s in segs)
    entries: list[dict] = []
    pending_route = 0.0
    for s in segs:
        if s["type"] == "routing":
            pending_route += s["delay"]
            continue
        kind, rtl_name = _classify_ecp5(s["to"]["cell"])
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


# ---------------------------------------------------------------- XC7S50: Vivado
VIVADO = os.environ.get("SA_VIVADO", "vivado")  # command; may be a wrapper script

VIVADO_TCL = """set_param general.maxThreads {threads}
create_project -in_memory -part {part}
read_verilog -sv {rtl}
set fh [open {out}/clk.xdc w]
puts $fh "create_clock -period {period:.3f} -name clk \\[get_ports clk\\]"
close $fh
read_xdc {out}/clk.xdc
synth_design -top matmul_top -mode out_of_context -flatten_hierarchy rebuilt
set fh [open {out}/synth_metrics.json w]
puts $fh "{{\\"lut\\": [llength [get_cells -hier -filter {{PRIMITIVE_GROUP == LUT}}]], \\"dsp\\": [llength [get_cells -hier -filter {{PRIMITIVE_SUBGROUP == DSP}}]]}}"
close $fh
opt_design
place_design
phys_opt_design
route_design
report_utilization -file {out}/util.txt
report_timing_summary -file {out}/timing_summary.txt -no_detailed_paths
report_timing -max_paths 1 -file {out}/timing_path.txt
set fh [open {out}/metrics.json w]
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set lut [llength [get_cells -hier -filter {{PRIMITIVE_GROUP == LUT}}]]
set ff  [llength [get_cells -hier -filter {{PRIMITIVE_GROUP == FLOP_LATCH || PRIMITIVE_GROUP == REGISTER}}]]
set dsp [llength [get_cells -hier -filter {{PRIMITIVE_SUBGROUP == DSP}}]]
set bram [llength [get_cells -hier -filter {{PRIMITIVE_GROUP == BLOCKRAM || PRIMITIVE_GROUP == BRAM}}]]
puts $fh "{{\\"wns\\": $wns, \\"period\\": {period:.3f}, \\"lut\\": $lut, \\"ff\\": $ff, \\"dsp\\": $dsp, \\"bram\\": $bram}}"
close $fh
"""


def synth_pnr_xc7s50(work: Path, fmax_target: float) -> dict:
    dev = BACKENDS["xc7s50"]
    rtl = work / "rtl" / "matmul_top.sv"
    out = work / "vivado"
    out.mkdir(parents=True, exist_ok=True)
    period = 1000.0 / fmax_target
    (out / "run.tcl").write_text(VIVADO_TCL.format(threads=os.cpu_count() or 4, part=dev["part"], rtl=rtl, out=out, period=period))
    env = dict(os.environ)
    env.setdefault("HOME", str(out))
    p = run([VIVADO, "-mode", "batch", "-nolog", "-nojournal", "-source", str(out / "run.tcl")], out, out / "vivado.log", timeout=3600, env=env)
    metrics = out / "metrics.json"
    if p.returncode != 0 or not metrics.exists():
        synth = out / "synth_metrics.json"
        if synth.exists():
            s = json.loads(synth.read_text())
            if s["dsp"] > dev["dsp"] or s["lut"] > dev["lut"]:
                return {**s, "ff": 0, "bram": 0, "fmax_mhz": 0.0, "critical_path": [], "fits": False}
        errs = "\n".join(l for l in (out / "vivado.log").read_text().splitlines() if l.startswith("ERROR"))[-2000:]
        raise RuntimeError(f"vivado failed, see {out / 'vivado.log'}\n{errs}")
    m = json.loads(metrics.read_text())
    fmax = 1000.0 / (m["period"] - m["wns"])
    return {
        "fmax_mhz": round(fmax, 2),
        "lut": m["lut"], "ff": m["ff"], "dsp": m["dsp"], "bram": m["bram"],
        "critical_path": summarize_critical_path_vivado((out / "timing_path.txt").read_text()),
        "fits": True,
    }


_VIV_CELL = re.compile(r"^\s+(\S+)\s+(\S+)\s+\(([^)]+)\)\s+([\d.]+)\s+([\d.]+)\s+[rf]\s+(\S+)")
_VIV_NET = re.compile(r"^\s+net \(fo=(\d+), (\w+)\)\s+([\d.]+)\s+([\d.]+)\s+(\S+)")


def summarize_critical_path_vivado(text: str) -> list[dict]:
    """Parse the data-path section of `report_timing -max_paths 1` into the same
    shape as the ECP5 summary: one entry per cell with preceding route folded in.
    Vivado wraps long cell lines ('... (Prop_dsp48e1_CLK_P[14])' then the numbers
    on the next line); those are joined first."""
    m = re.search(r"Data Path Delay:\s+([\d.]+)ns", text)
    total = float(m.group(1)) if m else 0.0
    # sections after the header are separated by dashed rules:
    # [0] '' | [1] source clock network | [2] data path | [3] destination clock | ...
    try:
        body = text.split("Netlist Resource(s)", 1)[1]
        parts = re.split(r"\n\s*-{20,}.*\n", body)
        data = parts[2] if len(parts) > 2 else ""
    except IndexError:
        data = ""
    lines: list[str] = []
    for line in data.splitlines():
        if lines and re.match(r"^\s+[\d.]+\s+[\d.]+\s+[rf]\s+\S+", line) and lines[-1].rstrip().endswith(")"):
            lines[-1] = lines[-1].rstrip() + " " + line.strip()
        else:
            lines.append(line)
    entries: list[dict] = []
    pending_route = 0.0
    for line in lines:
        n = _VIV_NET.match(line)
        if n:
            pending_route += float(n.group(3))
            continue
        c = _VIV_CELL.match(line)
        if not c:
            continue
        kind, incr, res = c.group(2), float(c.group(4)), c.group(6)
        rtl = res.rsplit("/", 1)[0] if "/" in res else res
        rtl = re.sub(r"_reg(\[\d+\])?(_psdsp_\d+)?$|_i_\d+$|\[\d+\]_i_\d+$", "", rtl)
        if kind.startswith("CARRY"):
            kind = "carry chain"
        if entries and entries[-1]["kind"] == kind and entries[-1]["rtl"] == rtl and kind == "carry chain":
            entries[-1]["logic_ns"] += incr
            entries[-1]["route_ns"] += pending_route
            entries[-1]["cells"] += 1
        else:
            entries.append({"kind": kind, "rtl": rtl, "logic_ns": incr, "route_ns": pending_route, "cells": 1})
        pending_route = 0.0
    # endpoint: '    SLICE_X9Y32   FDRE   r  <res>/D' has no (Prop_...) so the cell regex skips it;
    # record it so the final route delay is not lost
    end = re.search(r"^\s+\S+\s+(\S+)\s+r\s+(\S+)/[A-Z]+\s*$", data.rstrip().splitlines()[-1]) if data.strip() else None
    if end and (pending_route or not entries):
        rtl = re.sub(r"_reg(\[\d+\])?(_psdsp_\d+)?$", "", end.group(2))
        entries.append({"kind": end.group(1) + " (endpoint)", "rtl": rtl, "logic_ns": 0.0, "route_ns": pending_route, "cells": 1})
    for e in entries:
        e["logic_ns"] = round(e["logic_ns"], 2)
        e["route_ns"] = round(e["route_ns"], 2)
    return [{"total_ns": round(total, 2)}] + entries


# ---------------------------------------------------------------- top-level evaluate
SYNTH = {"ecp5": synth_pnr_ecp5, "xc7s50": synth_pnr_xc7s50}


def rtl_for(arch: MatmulArch, backend: str) -> str:
    return emit_matmul(arch, backend=backend)


def evaluate(arch: MatmulArch, fmax_target: float, runs_dir: Path, backend: str = "ecp5", tiles: int = 6) -> Metrics:
    rtl_text = rtl_for(arch, backend)
    h = hashlib.sha256(f"{backend}\n{rtl_text}".encode()).hexdigest()[:16]
    cache_key = hashlib.sha256(f"{CACHE_VERSION}\n{backend}\n{rtl_text}".encode()).hexdigest()[:16]
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / f"{cache_key}.json"
    if cached.exists():
        return Metrics(**json.loads(cached.read_text()))

    work = runs_dir / h
    (work / "rtl").mkdir(parents=True, exist_ok=True)
    (work / "rtl" / "matmul_top.sv").write_text(rtl_text)
    (work / "arch.json").write_text(json.dumps({"backend": backend, **arch.to_dict()}, indent=2))

    sim_ok, sim_log = simulate(arch, work, tiles=tiles)
    if not sim_ok:
        m = Metrics(fmax_mhz=0, lut=0, ff=0, dsp=0, bram=0, critical_path=[], sim_passed=False, sim_log=sim_log)
    else:
        r = SYNTH[backend](work, fmax_target)
        m = Metrics(**r, sim_passed=True, sim_log=sim_log)
    cached.write_text(json.dumps(asdict(m)))
    (work / "metrics.json").write_text(json.dumps(asdict(m), indent=2))
    return m
