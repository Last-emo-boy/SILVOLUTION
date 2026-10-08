"""Lattice iCE40 UP5K: yosys + nextpnr-ice40. Tiny device (5280 LC, 8 SB_MAC16), ~10-40 s per design.

nextpnr-ice40 has no --out-of-context, so the design is wrapped in a serial shell (one input pin shifted
into the core's inputs, outputs shifted out through one pin) before P&R. Resource and timing numbers come
from the core's cells only where the report allows; the shell adds a few LCs, which are subtracted."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import Backend, register
from ._common import TOOLS, flat_sim_lib, iverilog_netlist_sim, nextpnr_report, nextpnr_seeds, run

DEVICE, PACKAGE = "up5k", "sg48"


def _classify(cell: str) -> tuple[str, str]:
    base = cell.split("$")[0]
    if "DSP" in cell or "MAC16" in cell:
        return ("SB_MAC16", re.sub(r"_SB_MAC16.*|\.DSP.*", "", base))
    if "CARRY" in cell:
        return ("carry chain", re.sub(r"_SB_CARRY.*|_SB_LUT4.*|_LC$", "", base))
    if "LUT4" in cell or cell.endswith("_LC"):
        return ("LUT4", re.sub(r"_SB_DFF.*|_SB_LUT4.*|_LC$", "", base))
    if "DFF" in cell:
        return ("DFF", re.sub(r"_SB_DFF.*", "", base))
    return ("logic", base)


def _ports(core_json: Path, top: str) -> list[tuple[str, str, int]]:
    """(dir, name, width) of the top module's ports, from yosys's JSON of the synthesized core."""
    mod = json.loads(core_json.read_text())["modules"][top]
    return [(v["direction"], n, len(v["bits"])) for n, v in mod["ports"].items()]


def _wrapper(core_json: Path, top: str) -> str:
    ports = _ports(core_json, top)
    ins = [(n, w) for d, n, w in ports if d == "input" and n != "clk"]
    outs = [(n, w) for d, n, w in ports if d == "output"]
    n_in, n_out = sum(w for _, w in ins), sum(w for _, w in outs)
    decl = "\n".join(f"  logic [{w - 1}:0] {n};" for n, w in ins + outs)
    conn = ", ".join([".clk(clk)"] + [f".{n}({n})" for n, _ in ins + outs])
    slices, off = [], 0
    for n, w in ins:
        slices.append(f"  assign {n} = isr[{off + w - 1}:{off}];"); off += w
    return f"""module sa_wrap(input logic clk, input logic sin, output logic sout);
{decl}
  logic [{n_in - 1}:0] isr;
  always_ff @(posedge clk) isr <= {{isr[{n_in - 2}:0], sin}};
{chr(10).join(slices)}
  {top} core({conn});
  logic [{n_out - 1}:0] osr;
  always_ff @(posedge clk) osr <= isr[0] ? {{{", ".join(n for n, _ in outs)}}} : {{osr[{n_out - 2}:0], 1'b0}};
  assign sout = osr[{n_out - 1}];
endmodule
"""


def synth_counts(stat_json: Path) -> dict:
    st = json.loads(stat_json.read_text())
    cells = st["design"]["num_cells_by_type"] if "design" in st else next(iter(st["modules"].values()))["num_cells_by_type"]
    return {"lut": sum(v for k, v in cells.items() if k in ("SB_LUT4", "SB_CARRY")),
            "ff": sum(v for k, v in cells.items() if k.startswith("SB_DFF")),
            "dsp": cells.get("SB_MAC16", 0), "bram": cells.get("SB_RAM40_4K", 0) + cells.get("SB_SPRAM256KA", 0)}


def synth_pnr(work: Path, fmax_target: float, top: str) -> dict:
    rtl = work / "rtl" / f"{top}.sv"
    out = work / "pnr"
    out.mkdir(parents=True, exist_ok=True)
    # core-only synthesis for the resource numbers, the port list and the post-synth netlist
    (out / "synth.ys").write_text(
        f"plugin -i slang\nread_slang --top {top} {rtl}\nsynth_ice40 -dsp -top {top} -json {out / 'core.json'}\n"
        f"tee -o {out / 'stat.json'} stat -json\nwrite_verilog -noattr -norename {out / 'post_synth.v'}\n")
    p = run(["yosys", "-q", "-s", str(out / "synth.ys")], out, out / "yosys.log")
    if p.returncode != 0:
        raise RuntimeError(f"yosys failed, see {out / 'yosys.log'}")
    synth = synth_counts(out / "stat.json")
    if synth["dsp"] > DEV.dsp or synth["lut"] > DEV.lut:
        return {**synth, "fmax_mhz": 0.0, "critical_path": [], "fits": False}
    (out / "wrap.sv").write_text(_wrapper(out / "core.json", top))
    (out / "wrap.ys").write_text(
        f"plugin -i slang\nread_slang --top sa_wrap {out / 'wrap.sv'} {rtl}\nsynth_ice40 -dsp -top sa_wrap -json {out / 'synth.json'}\n")
    p = run(["yosys", "-q", "-s", str(out / "wrap.ys")], out, out / "yosys_wrap.log")
    if p.returncode != 0:
        raise RuntimeError(f"yosys (wrapper) failed, see {out / 'yosys_wrap.log'}")
    seeds = nextpnr_seeds(["nextpnr-ice40", f"--{DEVICE}", "--package", PACKAGE, "--json", str(out / "synth.json"), "--freq", str(fmax_target),
                           "--timing-allow-fail", "--pcf-allow-unconstrained", "--detailed-timing-report"], out)
    r = nextpnr_report(out, ("ICESTORM_LC",), (), ("ICESTORM_DSP",), ("ICESTORM_RAM", "ICESTORM_SPRAM"), _classify)
    # the wrapper's shift registers are LCs too; report the core's synth counts, nextpnr's Fmax/path
    return {**r, "lut": synth["lut"], "ff": synth["ff"], "dsp": synth["dsp"], "bram": synth["bram"], "fits": True, "fmax_seeds": seeds}


def netlist_sims(d, arch, work: Path, fmax_target: float, on_stage=None) -> dict:
    return iverilog_netlist_sim(d, arch, work, fmax_target, work / "pnr" / "post_synth.v", [flat_sim_lib("ice40")], on_stage)


DEV = register(Backend(
    key="ice40", name="Lattice iCE40 UP5K (SG48)", vendor="lattice",
    lut=5280, dsp=8, bram=30, lut_kind="LUT4", dsp_kind="SB_MAC16", dsp_width=16,
    synth_pnr=synth_pnr, netlist_sims=netlist_sims, sim_stages=("behavioral", "post_synth_func"),
    seconds_per_iter="10-40 s", fmax_range="20-120 MHz", toolchain="yosys + nextpnr-ice40 (open source)",
    emit_style="behavioral", aliases=("ice40", "up5k", "icebreaker", "icestick", "tinyfpga"),
))
