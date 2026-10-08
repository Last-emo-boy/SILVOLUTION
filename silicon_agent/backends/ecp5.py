"""Lattice ECP5 LFE5U-45F: yosys (slang front end) + nextpnr-ecp5. ~15-60 s per design."""

from __future__ import annotations

import json
from pathlib import Path

from . import Backend, register
from ._common import TB_DIR, TOOLS, flat_sim_lib, iverilog_netlist_sim, nextpnr_report, nextpnr_seeds, run

PART, PACKAGE, SPEED = "45k", "CABGA381", "6"
_PRIMS = ("MULT18X18D", "CCU2C", "LUT4", "DP16KD", "ALU54B", "TRELLIS_FF")


def _classify(cell: str) -> tuple[str, str]:
    base = cell.split("$")[0]
    hits = [(base.rfind("_" + p), p) for p in _PRIMS if ("_" + p) in base]
    if not hits:
        return ("logic", cell)
    kind = max(hits)[1]
    return ("carry chain" if kind == "CCU2C" else kind, base[:min(hits)[0]])


def synth_counts(stat_json: Path) -> dict:
    st = json.loads(stat_json.read_text())
    cells = st["design"]["num_cells_by_type"] if "design" in st else next(iter(st["modules"].values()))["num_cells_by_type"]
    return {
        "lut": sum(v for k, v in cells.items() if k.startswith("LUT") or k in ("CCU2C", "PFUMX", "L6MUX21")),
        "ff": cells.get("TRELLIS_FF", 0),
        "dsp": cells.get("MULT18X18D", 0),
        "bram": cells.get("DP16KD", 0),
    }


def synth_pnr(work: Path, fmax_target: float, top: str) -> dict:
    rtl = work / "rtl" / f"{top}.sv"
    out = work / "pnr"
    out.mkdir(parents=True, exist_ok=True)
    (out / "synth.ys").write_text(
        f"plugin -i slang\n"
        f"read_slang --top {top} {rtl} {TOOLS / 'share' / 'yosys' / 'lattice' / 'cells_bb_ecp5.v'}\n"
        f"synth_ecp5 -top {top} -json {out / 'synth.json'}\n"
        f"tee -o {out / 'stat.json'} stat -json\n"
        f"write_verilog -noattr -norename {out / 'post_synth.v'}\n"
    )
    p = run(["yosys", "-q", "-s", str(out / "synth.ys")], out, out / "yosys.log")
    if p.returncode != 0:
        raise RuntimeError(f"yosys failed, see {out / 'yosys.log'}")
    synth = synth_counts(out / "stat.json")
    if synth["dsp"] > DEV.dsp or synth["lut"] > DEV.lut:
        return {**synth, "fmax_mhz": 0.0, "critical_path": [], "fits": False}
    seeds = nextpnr_seeds([
        "nextpnr-ecp5", f"--{PART}", "--package", PACKAGE, "--speed", SPEED,
        "--json", str(out / "synth.json"), "--out-of-context", "--freq", str(fmax_target),
        "--timing-allow-fail", "--detailed-timing-report",
    ], out)
    return {**nextpnr_report(out, ("TRELLIS_COMB",), ("TRELLIS_FF",), ("MULT18X18D",), ("DP16KD",), _classify), "fits": True, "fmax_seeds": seeds}


def netlist_sims(d, arch, work: Path, fmax_target: float, on_stage=None) -> dict:
    # cells_sim.v's DP16KD is a black box; tb/bram_sim.v models it the way yosys drives it
    return iverilog_netlist_sim(d, arch, work, fmax_target, work / "pnr" / "post_synth.v",
                                [flat_sim_lib("ecp5", drop=("DP16KD",)), TB_DIR / "bram_sim.v"], on_stage)


DEV = register(Backend(
    key="ecp5", name="Lattice ECP5 LFE5U-45F-6", vendor="lattice",
    lut=43848, dsp=72, bram=108, lut_kind="LUT4", dsp_kind="MULT18X18D", dsp_width=18,
    synth_pnr=synth_pnr, netlist_sims=netlist_sims, sim_stages=("behavioral", "post_synth_func"),
    seconds_per_iter="15-60 s", fmax_range="60-250 MHz", toolchain="yosys + nextpnr-ecp5 (open source)",
    emit_style="ecp5", aliases=("ecp5", "lattice", "ulx3s", "orangecrab", "open source", "开源"),
))
