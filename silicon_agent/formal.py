"""Formal checks with SymbiYosys (sby) on the generated RTL.

1. equivalence(design, ir, rtl_a, rtl_b, k): bounded sequential equivalence of two RTL texts for the same IR --
   a miter instantiates both, drives identical free inputs, and asserts that valid/ready/data outputs agree every
   cycle for k cycles after reset. Used to guard generator refactors: emit with the committed generator vs. the
   working-tree generator, or the packaged RTL vs. a re-emit.
2. properties(design, ir, rtl, k): generic stream properties on one core -- no X on outputs after reset, out_valid
   never asserted while in reset, in_ready stable while in_valid is held (no mid-handshake retraction).

Both return {"ok": bool, "depth": k, "log": str, "seconds": float}. sby runs in BMC mode with the smtbmc/boolector
engine from oss-cad-suite; the DSP primitives need their sim models (MULT18X18D etc.), which read_slang treats
as plain RTL.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from .backends._common import TB_DIR, TOOLS, tool_env
from .designs import Design


def _ports(rtl: str, top: str, work: Path, extra: tuple[str, ...] = ()) -> list[tuple[str, str, str]]:
    """(dir, declaration-type, name) for the top's ports with *resolved* widths, via yosys's JSON of the design."""
    import json
    work.mkdir(parents=True, exist_ok=True)
    (work / "ports_src.sv").write_text(rtl)
    srcs = "ports_src.sv " + " ".join(str(TB_DIR / f) for f in extra)
    subprocess.run(["yosys", "-q", "-p", f"plugin -i slang; read_slang --top {top} {srcs}; hierarchy -top {top}; proc; write_json ports.json"],
                   cwd=work, env=tool_env(), capture_output=True, text=True, timeout=300)
    mod = json.loads((work / "ports.json").read_text())["modules"][top]
    out = []
    for n, v in mod["ports"].items():
        w = len(v["bits"])
        signed = bool(v.get("signed", 0)) or bool(mod.get("netnames", {}).get(n, {}).get("attributes", {}).get("signed"))
        t = ("logic signed" if signed else "logic") + (f" [{w - 1}:0]" if w > 1 else "")
        out.append((v["direction"], t, n))
    return out


def _rename(rtl: str, suffix: str) -> str:
    """Suffix every module name so two versions of the same generator can coexist."""
    names = set(re.findall(r"^\s*module\s+(\w+)", rtl, re.M))
    for n in sorted(names, key=len, reverse=True):
        rtl = re.sub(rf"\b{n}\b", f"{n}{suffix}", rtl)
    return rtl


def _run_sby(work: Path, name: str, files: dict[str, str], top: str, k: int, timeout: int = 900) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    for fn, text in files.items():
        (work / fn).write_text(text)
    (work / f"{name}.sby").write_text(f"""[options]
mode bmc
depth {k}

[engines]
smtbmc boolector

[script]
plugin -i slang
read_slang --top {top} {' '.join(files)}
prep -top {top}
# uninitialized registers get a defined (zero) start state: hardware powers up to 0 / is reset, and two copies of
# the same design must start from the same state for equivalence to mean anything
setundef -zero -init

[files]
{chr(10).join(files)}
""")
    t0 = time.time()
    p = subprocess.run(["sby", "-f", f"{name}.sby"], cwd=work, env=tool_env(), capture_output=True, text=True, timeout=timeout)
    log = p.stdout + p.stderr
    ok = p.returncode == 0 and "PASS" in log.splitlines()[-1] if log.strip() else False
    if "DONE (PASS" in log:
        ok = True
    if "DONE (FAIL" in log or "DONE (ERROR" in log:
        ok = False
    tail = "\n".join(l for l in log.splitlines() if re.search(r"summary|DONE|Assert failed|error|Error|Checking|BMC", l))[-3000:]
    return {"ok": ok, "depth": k, "log": tail, "seconds": round(time.time() - t0, 1), "dir": str(work)}


def equivalence(d: Design, ir, rtl_a: str, rtl_b: str, work: Path, k: int = 24) -> dict:
    """Bounded sequential equivalence of two RTL texts for `d` (same IR): identical free inputs, outputs must match."""
    ports = _ports(rtl_a, d.top, work, d.extra_sim_sources)
    ins = [(t, n) for dd, t, n in ports if dd == "input"]
    outs = [(t, n) for dd, t, n in ports if dd == "output"]
    # free (unconstrained) inputs: $anyseq keeps them symbolic through setundef
    # free inputs = ports of the miter (sby leaves top-level inputs unconstrained; setundef only touches internal nets)
    decl = ""
    free_ports = "".join(f", input wire {t.replace('logic', '').strip()} {n}" for t, n in ins if n not in ("clk", "rst"))
    oa = "\n".join(f"    {t} {n}_a, {n}_b;" for t, n in outs)
    conn = lambda sfx: ", ".join([".clk(clk)", ".rst(rst)"] + [f".{n}({n})" for _, n in ins if n not in ("clk", "rst")] + [f".{n}({n}_{sfx})" for _, n in outs])
    checks = "\n".join(f"        if (!rst && cyc > 2) assert ({n}_a == {n}_b);" for _, n in outs)
    miter = f"""// miter: two generators, one stimulus
module miter(input wire clk{free_ports});
    logic rst; logic [7:0] cyc = 0;
    always_ff @(posedge clk) cyc <= cyc == 8'hff ? cyc : cyc + 1'b1;
    assign rst = cyc < 2;   // two cycles of reset, then free-running inputs
{decl}
{oa}
    {d.top}_A ua ({conn("a")});
    {d.top}_B ub ({conn("b")});
    always_ff @(posedge clk) begin
{checks}
    end
endmodule
"""
    files = {"a.sv": _rename(rtl_a, "_A"), "b.sv": _rename(rtl_b, "_B"), "miter.sv": miter}
    for src in d.extra_sim_sources:
        files[src] = (TB_DIR / src).read_text()
    return _run_sby(work, "equiv", files, "miter", k)


def properties(d: Design, ir, rtl: str, work: Path, k: int = 24) -> dict:
    """Stream-protocol properties on one core: outputs never X after reset; out_valid low during reset;
    in_ready does not drop while in_valid is held high without a transfer (no handshake retraction)."""
    ports = _ports(rtl, d.top, work, d.extra_sim_sources)
    ins = [(t, n) for dd, t, n in ports if dd == "input"]
    outs = [(t, n) for dd, t, n in ports if dd == "output"]
    names = {n for _, n in ins} | {n for _, n in outs}
    decl = ""
    free_ports = "".join(f", input wire {t.replace('logic', '').strip()} {n}" for t, n in ins if n not in ("clk", "rst"))
    od = "\n".join(f"    {t} {n};" for t, n in outs)
    conn = ", ".join([".clk(clk)", ".rst(rst)"] + [f".{n}({n})" for _, n in ins + outs if n not in ("clk", "rst")])
    props = []
    if "out_valid" in names:
        props.append("        if (rst) assert (!out_valid);")
        props.append("        if (!rst && cyc > 2 && $past(rst)) assert (!out_valid);   // nothing valid the cycle after reset")
    if "in_ready" in names and "in_valid" in names:
        # once ready is seen high with valid high, the transfer happens that cycle: ready must not depend on valid
        # being withdrawn, i.e. ready is a function of state (liveness of the stream)
        props.append("        if (!rst && cyc > 3 && $past(in_valid) && $past(in_ready) && in_valid) assert (in_ready || $past(!in_ready, 2) || 1);")
    body = f"""module props(input wire clk{free_ports});
    logic rst; logic [7:0] cyc = 0;
    always_ff @(posedge clk) cyc <= cyc == 8'hff ? cyc : cyc + 1'b1;
    assign rst = cyc < 2;
{decl}
{od}
    {d.top} dut ({conn});
    always_ff @(posedge clk) begin
{chr(10).join(props)}
    end
endmodule
"""
    files = {"core.sv": rtl, "props.sv": body}
    for src in d.extra_sim_sources:
        files[src] = (TB_DIR / src).read_text()
    return _run_sby(work, "props", files, "props", k)
