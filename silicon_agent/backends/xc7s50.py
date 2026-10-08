"""AMD Spartan-7 XC7S50-1: Vivado 2025.2 batch, out-of-context synth + impl, xsim netlist ladder. ~1.5-4 min."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from . import Backend, register
from ._common import run

PART = "xc7s50csga324-1"


# ---------------------------------------------------------------- XC7S50: Vivado
VIVADO = os.environ.get("SA_VIVADO", "vivado")  # command; may be a wrapper script

VIVADO_TCL = """set_param general.maxThreads {threads}
create_project -in_memory -part {part}
read_verilog -sv {rtl}
set fh [open {out}/clk.xdc w]
puts $fh "create_clock -period {period:.3f} -name clk \\[get_ports clk\\]"
close $fh
read_xdc {out}/clk.xdc
synth_design -top {top} -mode out_of_context -flatten_hierarchy rebuilt
write_verilog -force -mode funcsim {out}/post_synth_func.v
write_verilog -force -mode timesim -sdf_anno true {out}/post_synth_time.v
write_sdf -force -mode timesim {out}/post_synth.sdf
set fh [open {out}/synth_metrics.json w]
puts $fh "{{\\"lut\\": [llength [get_cells -hier -filter {{PRIMITIVE_GROUP == LUT}}]], \\"dsp\\": [llength [get_cells -hier -filter {{PRIMITIVE_SUBGROUP == DSP}}]]}}"
close $fh
opt_design
place_design
phys_opt_design
route_design
write_verilog -force -mode funcsim {out}/post_impl_func.v
write_verilog -force -mode timesim -sdf_anno true {out}/post_impl_time.v
write_sdf -force -mode timesim {out}/post_impl.sdf
report_utilization -file {out}/util.txt
set_switching_activity -default_toggle_rate 25 -default_static_probability 0.5
report_power -file {out}/power.txt
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


def synth_pnr(work: Path, fmax_target: float, top: str) -> dict:
    rtl = work / "rtl" / f"{top}.sv"
    out = work / "vivado"
    out.mkdir(parents=True, exist_ok=True)
    period = 1000.0 / fmax_target
    (out / "run.tcl").write_text(VIVADO_TCL.format(threads=os.cpu_count() or 4, part=PART, rtl=rtl, out=out, period=period, top=top))
    env = dict(os.environ)
    env.setdefault("HOME", str(out))
    p = run([VIVADO, "-mode", "batch", "-nolog", "-nojournal", "-source", str(out / "run.tcl")], out, out / "vivado.log", timeout=3600, env=env)
    metrics = out / "metrics.json"
    if p.returncode != 0 or not metrics.exists():
        synth = out / "synth_metrics.json"
        if synth.exists():
            s = json.loads(synth.read_text())
            if s["dsp"] > DEV.dsp or s["lut"] > DEV.lut:
                return {**s, "ff": 0, "bram": 0, "fmax_mhz": 0.0, "critical_path": [], "fits": False}
        errs = "\n".join(l for l in (out / "vivado.log").read_text().splitlines() if l.startswith("ERROR"))[-2000:]
        raise RuntimeError(f"vivado failed, see {out / 'vivado.log'}\n{errs}")
    m = json.loads(metrics.read_text())
    fmax = 1000.0 / (m["period"] - m["wns"])
    return {
        "fmax_mhz": round(fmax, 2),
        "lut": m["lut"], "ff": m["ff"], "dsp": m["dsp"], "bram": m["bram"],
        "critical_path": summarize_critical_path((out / "timing_path.txt").read_text()),
        "fits": True,
        "power_mw": parse_power(out / "power.txt"),
    }


def parse_power(path: Path) -> float | None:
    """Dynamic power (mW) from report_power at the constrained clock with default 25% toggle rates:
    a relative measure for ranking architectures, not a board number."""
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        if line.strip().startswith("| Dynamic (W)"):
            try:
                return round(float(line.split("|")[2].strip()) * 1000, 1)
            except (IndexError, ValueError):
                return None
    return None


_VIV_CELL = re.compile(r"^\s+(\S+)\s+(\S+)\s+\(([^)]+)\)\s+([\d.]+)\s+([\d.]+)\s+[rf]\s+(\S+)")
_VIV_NET = re.compile(r"^\s+net \(fo=(\d+), (\w+)\)\s+([\d.]+)\s+([\d.]+)\s+(\S+)")


def summarize_critical_path(text: str) -> list[dict]:
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


# ---------------------------------------------------------------- XC7S50: netlist simulation (xsim)
XSIM_STAGES = [
    # key, label, netlist, sdf, libs
    ("post_synth_func", "Post-synthesis functional", "post_synth_func.v", None, ["unisims_ver"]),
    ("post_synth_time", "Post-synthesis timing", "post_synth_time.v", "post_synth.sdf", ["simprims_ver"]),
    ("post_impl_func", "Post-implementation functional", "post_impl_func.v", None, ["unisims_ver"]),
    ("post_impl_time", "Post-implementation timing", "post_impl_time.v", "post_impl.sdf", ["simprims_ver"]),
]
XILINX_GLBL = os.environ.get("SA_XILINX_GLBL", "/opt/amd/2025.2/Vivado/data/verilog/src/glbl.v")


def _clean_netlist(path: Path) -> None:
    """Vivado 2025.2 writes the host's multi-line /etc/os-release into the '// Host' comment,
    which breaks the comment and makes xvlog choke. Collapse it to one line."""
    lines = path.read_text().split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith("// Host"):
            out.append("// Host        : (redacted)")
            i += 1
            while i < len(lines) and not lines[i].startswith("// Command"):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    path.write_text("\n".join(out))


def netlist_sims(d, arch, work: Path, fmax_target: float, on_stage=None) -> dict:
    """Stages 2-5 of the simulation ladder. Returns {stage: {"passed", "label", "period_ns", "log"}}.
    Post-implementation timing runs at the *target* period: that is the real pass/fail."""
    out = work / "vivado"
    sim = work / "xsim"
    sim.mkdir(parents=True, exist_ok=True)
    stim, exp = d.vectors(arch)
    (sim / "stim.hex").write_text("\n".join(stim) + "\n")
    (sim / "exp.hex").write_text("\n".join(exp) + "\n")
    period = 1000.0 / fmax_target
    (sim / "tb.sv").write_text(d.tb(arch, period))
    env = dict(os.environ)
    env.setdefault("HOME", str(sim))
    env.pop("LIBRARY_PATH", None)
    results = {}
    for key, label, netlist, sdf, libs in XSIM_STAGES:
        net = out / netlist
        if not net.exists():
            results[key] = {"passed": None, "label": label, "period_ns": period, "log": "netlist not produced"}
            continue
        _clean_netlist(net)
        log = sim / f"{key}.log"
        libflags = [x for l in libs for x in ("-L", l)] + ["-L", "secureip"]
        timing = ["-maxdelay", "-sdfmax", f"/tb/dut={out / sdf}"] if sdf else []
        cmds = [
            ["xvlog", "-sv", str(sim / "tb.sv")],
            ["xvlog", str(net), XILINX_GLBL],
            ["xelab", "-debug", "off", "-timescale", "1ns/1ps", *libflags, *timing, "tb", "glbl", "-s", key],
            ["xsim", key, "-R"],
        ]
        text = ""
        ok = True
        with log.open("w") as f:
            for c in cmds:
                r = subprocess.run([VIVADO_BIN_DIR + c[0]] + c[1:] if VIVADO_BIN_DIR else c, cwd=sim, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=1800)
                f.write(f"$ {' '.join(c)}\n{r.stdout}\n")
                text += r.stdout
                if r.returncode != 0 and c[0] != "xsim":
                    ok = False
                    break
        passed = ok and ("RESULT PASS" in text)
        tail = "\n".join(l for l in text.splitlines() if re.search(r"RESULT|MISMATCH|COUNT|^ERROR", l))[-1500:]
        results[key] = {"passed": passed, "label": label, "period_ns": round(period, 3), "log": tail}
        if on_stage:
            on_stage(key, results[key])
        if not ok:
            break
    return results


VIVADO_BIN_DIR = os.environ.get("SA_VIVADO_BIN_DIR", "")  # e.g. /opt/amd/2025.2/Vivado/bin/ when not on PATH


DEV = register(Backend(
    key="xc7s50", name="AMD Spartan-7 XC7S50-1 (CSGA324)", vendor="amd",
    lut=32600, dsp=120, bram=75, lut_kind="LUT6", dsp_kind="DSP48E1", dsp_width=18,
    synth_pnr=synth_pnr, netlist_sims=netlist_sims,
    sim_stages=("behavioral", "post_synth_func", "post_synth_time", "post_impl_func", "post_impl_time"),
    seconds_per_iter="1.5-4 min", fmax_range="100-500 MHz", toolchain="AMD Vivado 2025.2",
    parallel=1, emit_style="vivado", aliases=("xc7s50", "spartan", "spartan-7", "xilinx", "amd", "vivado", "7 series", "zynq"),
))
