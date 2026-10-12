"""SkyWater sky130 (HD standard cells) through OpenROAD-flow-scripts: the ASIC backend.

The flow (yosys synthesis with the slang frontend -> floorplan -> place -> CTS -> route -> finish) runs in the
`orfs` compose service (openroad/orfs image) which watches a queue directory on the shared volume; this module
writes a request there and waits for `done`. Metrics map onto the FPGA vocabulary the loop already speaks:
`lut` = standard cells (combinational), `ff` = sequential cells, `dsp`/`bram` = 0, plus `area_um2` (stdcell
area), `power_mw` (OpenSTA, at the SDC clock) and the critical path from the finish report. Fmax comes from the
report's min period; the SDC asks for the run's target so the resizer works toward it.

Netlist sims: the yosys post-synthesis netlist and the final routed netlist (6_final.v, standard cells only)
against the design's vector testbench with the sky130_fd_sc_hd Verilog models that ship in oss-cad-suite.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from . import Backend, register
from ._common import TOOLS, iverilog_netlist_sim

QUEUE = Path(os.environ.get("SA_ASIC_QUEUE", str(Path(os.environ.get("SA_ROOT", ".")) / "asic" / "queue")))
PLATFORM = "sky130hd"
CELL_LIB = TOOLS / "examples" / "eqy" / "spm"   # sky130_fd_sc_hd.v + primitives.v


def _config(top: str, period_ns: float, req: Path, util: int = 40) -> tuple[str, str]:
    cfg = f"""export DESIGN_NAME = {top}
export PLATFORM    = {PLATFORM}
export VERILOG_FILES = {req}/{top}.sv
export SDC_FILE      = {req}/constraint.sdc
export SYNTH_HDL_FRONTEND = slang
export CORE_UTILIZATION = {util}
export PLACE_DENSITY = 0.6
export SYNTH_HIERARCHICAL = 0
export TNS_END_PERCENT = 100
"""
    sdc = f"""set clk_period {period_ns:.3f}
create_clock -name core_clock -period $clk_period [get_ports clk]
set_input_delay [expr $clk_period * 0.2] -clock core_clock [all_inputs -no_clocks]
set_output_delay [expr $clk_period * 0.2] -clock core_clock [all_outputs]
"""
    return cfg, sdc


def _critical_path(rpt: str) -> list[dict]:
    """The reg-to-reg max path of 6_finish.rpt (its compact Delay/Time/Description form) as the loop's critical-path
    list: one entry per cell with its delay. OpenSTA folds the wire into the driving cell's delay, so route_ns is 0
    and logic_ns carries both; the clock tree is dropped (it is not the data path)."""
    m = re.search(r"report_checks -path_delay max reg to reg\n-+\n(.*?)\n\s*[\d.-]+\s+data arrival time", rpt, re.S)
    body = m.group(1) if m else ""
    out: list[dict] = []
    for line in body.splitlines():
        mm = re.match(r"\s*([\d.]+)\s+([\d.]+)\s+[v^]\s+(\S+)/(\w+)\s+\((\S+)\)", line)
        if not mm or mm.group(4) == "CLK":
            continue
        inst, cell = mm.group(3), mm.group(5)
        short = cell.split("__")[-1]
        if "clkbuf" in short or "clkinv" in short:
            continue
        kind = "FF" if short.startswith("df") else "adder" if short.startswith(("fa_", "ha_")) else "buffer" if short.startswith("buf_") else "logic"
        rtl = re.sub(r"\$_DFF.*|\$_.*", "", inst)
        out.append({"kind": kind, "cell": short, "rtl": rtl if not rtl.startswith(("_", "place", "rebuffer", "hold", "input", "output")) else "",
                    "logic_ns": float(mm.group(1)), "route_ns": 0.0})
    ma = re.search(r"max reg to reg.*?\n\s*([\d.]+)\s+data arrival time", rpt, re.S)
    total = float(ma.group(1)) if ma else round(sum(e["logic_ns"] for e in out), 2)
    return [{"total_ns": total}] + out[:28]


def synth_pnr(work: Path, fmax_target: float, top: str) -> dict:
    rtl = work / "rtl" / f"{top}.sv"
    out = work / "pnr"
    out.mkdir(parents=True, exist_ok=True)
    period = 1000.0 / max(fmax_target, 1.0)
    req = QUEUE / f"{work.name}-{int(time.time())}"
    req.mkdir(parents=True, exist_ok=True)
    cfg, sdc = _config(top, period, req)
    shutil.copy(rtl, req / f"{top}.sv")
    (req / "config.mk").write_text(cfg)
    (req / "constraint.sdc").write_text(sdc)
    (req / "request.json").write_text(json.dumps({"top": top, "fmax_target": fmax_target, "work": str(work)}))
    t0 = time.time()
    while not (req / "done").exists():
        if time.time() - t0 > 4000:
            raise RuntimeError(f"ORFS runner did not finish {req.name} in time (is the orfs service up?)")
        time.sleep(3)
    rc = int((req / "exit").read_text().strip() or "1") if (req / "exit").exists() else 1
    for f in ("6_report.json", "6_finish.rpt", "synth_stat.txt", "1_2_yosys.v", "6_final.v", "make.log", "1_2_yosys.log", "6_final.gds"):
        if (req / f).exists():
            shutil.copy(req / f, out / f)
    if rc != 0 or not (out / "6_report.json").exists():
        tail = (req / "make.log").read_text()[-3000:] if (req / "make.log").exists() else ""
        raise RuntimeError(f"ORFS flow failed (exit {rc}), see {out / 'make.log'}\n{tail[-800:]}")
    rep = json.loads((out / "6_report.json").read_text())
    fin = (out / "6_finish.rpt").read_text()
    m = re.search(r"period_min = ([\d.]+) fmax = ([\d.]+)", fin)
    fmax = float(m.group(2)) if m else 0.0
    cells = int(rep.get("finish__design__instance__count__stdcell", 0))
    seq = int(rep.get("finish__design__instance__count__class:sequential_cell", 0))
    area = float(rep.get("finish__design__instance__area__stdcell", 0.0))
    power_w = float(rep.get("finish__power__total", 0.0))
    return {"fmax_mhz": fmax, "lut": cells - seq, "ff": seq, "dsp": 0, "bram": 0, "fits": True,
            "critical_path": _critical_path(fin), "fmax_seeds": [fmax],
            "area_um2": round(area, 1), "power_mw": round(power_w * 1000.0, 2)}


def cell_lib() -> Path:
    """The sky130_fd_sc_hd simulation models, functional (no specify/timing) and parsable by iverilog: the PDK file has
    a few `endif FOO` lines without `//` which iverilog rejects; cached under cache/."""
    from ._common import CACHE
    lib = CACHE / "sky130_fd_sc_hd_func.v"
    if lib.exists():
        return lib
    src = (CELL_LIB / "primitives.v").read_text() + "\n" + (CELL_LIB / "sky130_fd_sc_hd.v").read_text()
    src = re.sub(r"^(`endif)\s+(\S+)\s*$", r"\1 // \2", src, flags=re.M)
    CACHE.mkdir(exist_ok=True)
    lib.write_text("`define FUNCTIONAL\n`define UNIT_DELAY\n" + src)
    return lib


def netlist_sims(d, arch, work: Path, fmax_target: float, on_stage=None) -> dict:
    libs = [cell_lib()]
    r = iverilog_netlist_sim(d, arch, work, fmax_target, work / "pnr" / "1_2_yosys.v", libs, on_stage)
    final = work / "pnr" / "6_final.v"
    if final.exists():
        r2 = iverilog_netlist_sim(d, arch, work, fmax_target, final, libs, None)
        st = r2["post_synth_func"]
        st = {**st, "label": "Post-route functional (standard cells)"}
        r["post_impl_func"] = st
        if on_stage:
            on_stage("post_impl_func", st)
    return r


register(available=os.environ.get("SA_ASIC_ENABLE", "") == "1" or QUEUE.exists(), b=Backend(
    key="sky130", name="SkyWater sky130 HD (ASIC, OpenROAD)", vendor="skywater",
    lut=200000, dsp=0, bram=0, lut_kind="stdcell", dsp_kind="none", dsp_width=0,
    synth_pnr=synth_pnr, netlist_sims=netlist_sims, sim_stages=("behavioral", "post_synth_func", "post_impl_func"),
    seconds_per_iter="2-10 min", fmax_range="50-400 MHz", toolchain="yosys + OpenROAD (ORFS, sky130hd)",
    parallel=1, emit_style="behavioral", aliases=("sky130", "skywater", "asic", "openroad", "tapeout", "standard cell", "标准单元", "流片"),
))
