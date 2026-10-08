"""Gowin GW1NR-9 (Tang Nano 9K): yosys synth_gowin + nextpnr-himbaechel. ~15-60 s per design.

himbächel insists on pin constraints, so the design gets the same serial shell as iCE40 and a CST that
pins clk/sin/sout to three arbitrary IO sites."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import Backend, register
from ._common import TB_DIR, flat_sim_lib, iverilog_netlist_sim, nextpnr_report, nextpnr_seeds, run
from .ice40 import _wrapper

DEVICE, FAMILY = "GW1NR-LV9QN88PC6/I5", "GW1N-9C"
CST = 'IO_LOC "clk" 52;\nIO_LOC "sin" 53;\nIO_LOC "sout" 54;\n'


def _classify(cell: str) -> tuple[str, str]:
    base = cell.split("$")[0].removeprefix("core.")
    if "MULT" in cell or "PADD" in cell:
        return ("MULT18X18", re.sub(r"_MULT.*|_PADD.*", "", base))
    if "ALU" in cell:
        return ("carry chain", re.sub(r"_DFF.*|_ALU.*", "", base))
    if "LUT" in cell:
        return ("LUT4", re.sub(r"_DFF.*|_LUT.*", "", base))
    if "DFF" in cell:
        return ("DFF", re.sub(r"_DFF.*", "", base))
    return ("logic", base)


def synth_counts(stat_json: Path) -> dict:
    st = json.loads(stat_json.read_text())
    cells = st["design"]["num_cells_by_type"] if "design" in st else next(iter(st["modules"].values()))["num_cells_by_type"]
    return {"lut": sum(v for k, v in cells.items() if k.startswith("LUT") or k == "ALU" or k.startswith("MUX2_LUT")),
            "ff": sum(v for k, v in cells.items() if k.startswith("DFF")),
            "dsp": sum(v for k, v in cells.items() if k.startswith("MULT") or k.startswith("MULTALU")),
            "bram": sum(v for k, v in cells.items() if k in ("DP", "DPB", "SDP", "SDPB", "SP", "SPB", "DPX9B", "SDPX9B"))}


def synth_pnr(work: Path, fmax_target: float, top: str) -> dict:
    rtl = work / "rtl" / f"{top}.sv"
    out = work / "pnr"
    out.mkdir(parents=True, exist_ok=True)
    (out / "synth.ys").write_text(
        f"plugin -i slang\nread_slang --top {top} {rtl}\nsynth_gowin -top {top} -json {out / 'core.json'}\n"
        f"tee -o {out / 'stat.json'} stat -json\nwrite_verilog -noattr -norename {out / 'post_synth.v'}\n")
    p = run(["yosys", "-q", "-s", str(out / "synth.ys")], out, out / "yosys.log")
    if p.returncode != 0:
        raise RuntimeError(f"yosys failed, see {out / 'yosys.log'}")
    synth = synth_counts(out / "stat.json")
    if synth["dsp"] > DEV.dsp or synth["lut"] > DEV.lut:
        return {**synth, "fmax_mhz": 0.0, "critical_path": [], "fits": False}
    (out / "wrap.sv").write_text(_wrapper(out / "core.json", top))
    # undriven partial buses (e.g. the low bits of a sliced accumulator) survive synth_gowin as a $buf that
    # himbaechel cannot place; tie them off and clean before writing the netlist
    (out / "wrap.ys").write_text(
        f"plugin -i slang\nread_slang --top sa_wrap {out / 'wrap.sv'} {rtl}\nsynth_gowin -top sa_wrap -run begin:json\n"
        f"setundef -undriven -zero\nopt_clean -purge\nwrite_json {out / 'synth.json'}\n")
    p = run(["yosys", "-q", "-s", str(out / "wrap.ys")], out, out / "yosys_wrap.log")
    if p.returncode != 0:
        raise RuntimeError(f"yosys (wrapper) failed, see {out / 'yosys_wrap.log'}")
    (out / "pins.cst").write_text(CST)
    seeds = nextpnr_seeds(["nextpnr-himbaechel", "--device", DEVICE, "--vopt", f"family={FAMILY}", "--vopt", f"cst={out / 'pins.cst'}",
                           "--json", str(out / "synth.json"), "--freq", str(fmax_target), "--timing-allow-fail", "--detailed-timing-report"], out)
    r = nextpnr_report(out, ("LUT4", "ALU"), ("DFF",), ("MULT18X18", "MULT9X9", "MULTALU18X18", "MULTALU36X18", "MULTADDALU18X18", "PADD18", "PADD9"), ("BSRAM",), _classify)
    return {**r, "lut": synth["lut"], "ff": synth["ff"], "dsp": synth["dsp"], "bram": synth["bram"], "fits": True, "fmax_seeds": seeds}


def netlist_sims(d, arch, work: Path, fmax_target: float, on_stage=None) -> dict:
    # cells_sim.v has no MULT models (they are black boxes in cells_xtra); tb/gowin_mult_sim.v supplies them
    return iverilog_netlist_sim(d, arch, work, fmax_target, work / "pnr" / "post_synth.v",
                                [flat_sim_lib("gowin", drop=("SP", "SPX9", "DP", "DPX9", "SDP", "SDPX9", "SPB", "DPB", "SDPB", "DPX9B", "SDPX9B", "SPX9B")), TB_DIR / "gowin_mult_sim.v", TB_DIR / "bram_sim.v"], on_stage)


DEV = register(Backend(
    key="gowin", name="Gowin GW1NR-9 (Tang Nano 9K)", vendor="gowin",
    lut=8640, dsp=20, bram=26, lut_kind="LUT4", dsp_kind="MULT18X18", dsp_width=18,
    synth_pnr=synth_pnr, netlist_sims=netlist_sims, sim_stages=("behavioral", "post_synth_func"),
    seconds_per_iter="15-60 s", fmax_range="30-200 MHz", toolchain="yosys + nextpnr-himbaechel/gowin (open source)",
    parallel=3, emit_style="behavioral", aliases=("gowin", "gw1n", "tang nano", "sipeed", "高云"),
))
