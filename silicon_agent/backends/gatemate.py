"""Cologne Chip GateMate CCGM1A1: yosys synth_gatemate (-luttree) + nextpnr-himbaechel. ~2-4 min per design (3 seeds).

The GateMate CPE is a 2x LUT-tree + adder cell; the multiplier (CC_MULT) is implemented from CPE_MULT cells, so the
"DSP" count below is the number of CC_MULT instances synthesis created, not a hard block count."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import Backend, register
from ._common import TB_DIR, flat_sim_lib, iverilog_netlist_sim, nextpnr_report, nextpnr_seeds, run
from .ice40 import _wrapper

DEVICE = "CCGM1A1"


def _classify(cell: str) -> tuple[str, str]:
    base = cell.split("$")[0].removeprefix("core.")
    if "mult" in cell.lower():
        return ("CC_MULT", re.sub(r"\$.*", "", base) or "mult")
    if "ADDF" in cell or "addf" in cell:
        return ("carry chain", re.sub(r"_CC_ADDF.*|\$.*", "", base) or "add")
    if "LT" in cell or "lut" in cell.lower():
        return ("LUT", re.sub(r"_CC_LUT.*|\$.*", "", base) or "lut")
    if "FF" in cell or "ff" in cell:
        return ("DFF", re.sub(r"_CC_DFF.*|\$.*", "", base) or "ff")
    return ("logic", base)


def synth_counts(stat_json: Path) -> dict:
    st = json.loads(stat_json.read_text())
    cells = st["design"]["num_cells_by_type"] if "design" in st else next(iter(st["modules"].values()))["num_cells_by_type"]
    return {"lut": sum(v for k, v in cells.items() if k.startswith("CC_LUT") or k in ("CC_ADDF", "CC_L2T4", "CC_L2T5")),
            "ff": sum(v for k, v in cells.items() if k.startswith("CC_DFF") or k.startswith("CC_DLT")),
            "dsp": cells.get("CC_MULT", 0),
            "bram": cells.get("CC_BRAM_20K", 0) + cells.get("CC_BRAM_40K", 0)}


def synth_pnr(work: Path, fmax_target: float, top: str) -> dict:
    rtl = work / "rtl" / f"{top}.sv"
    out = work / "pnr"
    out.mkdir(parents=True, exist_ok=True)
    (out / "synth.ys").write_text(
        f"plugin -i slang\nread_slang --top {top} {rtl}\nsynth_gatemate -top {top} -luttree -json {out / 'core.json'}\n"
        f"tee -o {out / 'stat.json'} stat -json\nwrite_verilog -noattr -norename {out / 'post_synth.v'}\n")
    p = run(["yosys", "-q", "-s", str(out / "synth.ys")], out, out / "yosys.log")
    if p.returncode != 0:
        raise RuntimeError(f"yosys failed, see {out / 'yosys.log'}")
    synth = synth_counts(out / "stat.json")
    if synth["lut"] > DEV.lut or synth["bram"] > DEV.bram:
        return {**synth, "fmax_mhz": 0.0, "critical_path": [], "fits": False}
    (out / "wrap.sv").write_text(_wrapper(out / "core.json", top))
    (out / "wrap.ys").write_text(
        f"plugin -i slang\nread_slang --top sa_wrap {out / 'wrap.sv'} {rtl}\nsynth_gatemate -top sa_wrap -luttree -run begin:json\n"
        f"setundef -undriven -zero\nopt_clean -purge\nwrite_json {out / 'synth.json'}\n")
    p = run(["yosys", "-q", "-s", str(out / "wrap.ys")], out, out / "yosys_wrap.log")
    if p.returncode != 0:
        raise RuntimeError(f"yosys (wrapper) failed, see {out / 'yosys_wrap.log'}")
    seeds = nextpnr_seeds(["nextpnr-himbaechel", "--device", DEVICE, "--vopt", "allow-unconstrained",
                           "--json", str(out / "synth.json"), "--freq", str(fmax_target), "--timing-allow-fail", "--detailed-timing-report"], out)
    r = nextpnr_report(out, ("CPE_LT", "CPE_ADDF2", "CPE_COMP"), ("CPE_FF",), ("CPE_MULT",), ("CPE_RAMIO",), _classify)
    return {**r, "lut": synth["lut"], "ff": synth["ff"], "dsp": synth["dsp"], "bram": synth["bram"], "fits": True, "fmax_seeds": seeds}


def netlist_sims(d, arch, work: Path, fmax_target: float, on_stage=None) -> dict:
    return iverilog_netlist_sim(d, arch, work, fmax_target, work / "pnr" / "post_synth.v", [flat_sim_lib("gatemate")], on_stage)


DEV = register(Backend(
    key="gatemate", name="Cologne Chip GateMate CCGM1A1", vendor="colognechip",
    lut=20480, dsp=256, bram=32, lut_kind="CPE (LUT-tree)", dsp_kind="CC_MULT", dsp_width=20,
    synth_pnr=synth_pnr, netlist_sims=netlist_sims, sim_stages=("behavioral", "post_synth_func"),
    seconds_per_iter="2-4 min", fmax_range="30-100 MHz", toolchain="yosys + nextpnr-himbaechel/gatemate (open source)",
    parallel=3, emit_style="behavioral", aliases=("gatemate", "cologne", "ccgm1a1", "colognechip"),
))
