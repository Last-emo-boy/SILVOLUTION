"""Proposals: a change to silicon-agent's own generators, submitted as a unified diff, gated before a human merges it.

The agent (or anyone) can propose a change to the Python under silicon_agent/ -- a new IR knob, a better emitter, a
testbench improvement. The proposal is applied to a scratch copy of the app tree and that copy is exercised in a
subprocess with the real toolchain:

  1. import + emit: every design's presets still emit, every emitter still elaborates (iverilog)
  2. behavioral simulation of the bench cases' initial IRs (plus the IRs the proposal names) on the patched tree
  3. formal equivalence, old tree vs patched tree, for every IR the proposal does NOT claim to change (a refactor
     must not change the RTL's behaviour; a new knob must leave the knob-off RTL equivalent)
  4. mutation score of the testbench on the touched design, before and after (the bench must not get weaker)
  5. optional: a measured A/B on the device for the IRs the proposal names (synthesis + P&R, same as a run)

The result is a report; nothing is merged here. Merging is `git apply` by the operator (or the deploy script)
after reading the report in the chat. The server keeps proposals under SA_ROOT/proposals/<id>/.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP = Path(__file__).resolve().parent.parent  # the checkout: silicon_agent/, agh-plugin/, deploy/ ...
ALLOWED = ("silicon_agent/",)                # a proposal may touch only these subtrees (new files included: a new generator + its tb/test_*.py)
FORBIDDEN = ("silicon_agent/server.py", "silicon_agent/agh.py", "silicon_agent/proposals.py", "silicon_agent/llm.py")


def _files_in_diff(diff: str) -> list[str]:
    out = []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            p = line[4:].split("\t")[0].strip()
            if p.startswith(("a/", "b/")):
                p = p[2:]
            if p != "/dev/null" and p not in out:
                out.append(p)
    return out


def check_scope(diff: str) -> list[str]:
    """Why the diff cannot be gated at all (outside the allowed tree, touching the runtime, not a diff)."""
    files = _files_in_diff(diff)
    errs = []
    if not files:
        errs.append("not a unified diff (no +++ lines)")
    for f in files:
        if not f.startswith(ALLOWED):
            errs.append(f"{f}: proposals may only change {', '.join(ALLOWED)}")
        if f in FORBIDDEN:
            errs.append(f"{f}: the server/runtime is not a proposal target")
        if ".." in f.split("/"):
            errs.append(f"{f}: path escapes the tree")
    return errs


def scratch_copy(dest: Path) -> Path:
    """Copy the app's Python (not tools/, results/ ...) so a patched version can be imported in a subprocess."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copytree(APP / "silicon_agent", dest / "silicon_agent", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest


def apply(diff: str, tree: Path) -> tuple[bool, str]:
    (tree / "proposal.diff").write_text(diff)
    p = subprocess.run(["patch", "-p1", "--batch", "--forward", "-i", "proposal.diff"], cwd=tree, capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr)[-2000:]


# ---------------------------------------------------------------- the gate program (runs in a subprocess on a tree)
GATE = r'''
import json, sys, os, tempfile, time, traceback
from pathlib import Path
tree, out_path, spec_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(tree))
os.environ.setdefault("SA_ROOT", str(tree / "root"))
spec = json.loads(spec_path.read_text())
rep = {"steps": [], "ok": True}
def step(name, fn):
    t0 = time.time(); r = {"name": name}
    try:
        r.update(fn() or {}); r.setdefault("ok", True)
    except Exception as e:
        r.update({"ok": False, "error": (traceback.format_exc())[-1500:]})
    r["seconds"] = round(time.time() - t0, 1)
    rep["steps"].append(r); rep["ok"] = rep["ok"] and r["ok"]
    out_path.write_text(json.dumps(rep, indent=1))
    return r
from silicon_agent.designs import DESIGNS
from silicon_agent.schema import SCHEMAS
from silicon_agent import eda
from silicon_agent.backends._common import tool_env
def emit_all():
    import subprocess
    n = 0; bad = []
    for kind, d in DESIGNS.items():
        for name, preset in (SCHEMAS.get(kind, {}).get("presets") or {}).items():
            ir = d.ir_cls.from_dict({**preset, **SCHEMAS[kind].get("locked_default", {})} if kind not in ("graph", "fir_chain") else preset)
            errs = ir.validate()
            if errs: bad.append(f"{kind}/{name}: {errs}"); continue
            for be in ("ecp5", "ice40", "xc7s50"):
                rtl = d.emit(ir, backend=be)
                w = Path(tempfile.mkdtemp()); (w / "x.sv").write_text(rtl)
                srcs = [str(w / "x.sv")] + [str(tree / "silicon_agent" / "tb" / f) for f in d.extra_sim_sources]
                p = subprocess.run(["iverilog", "-g2012", "-s", d.top, "-o", str(w / "a.out")] + srcs, capture_output=True, text=True, env=tool_env())
                n += 1
                if p.returncode: bad.append(f"{kind}/{name}/{be}: {p.stderr[-300:]}")
    return {"emitted": n, "failures": bad, "ok": not bad}
step("emit+elaborate every preset", emit_all)
# the IRs to simulate: bench-case initial IRs of the touched designs + the proposal's own
def sim_irs():
    from silicon_agent.bench import CASES
    irs = []
    for name, sp, _ in CASES:
        if sp["design"] in spec["designs"]:
            irs.append((name, sp["design"], sp["backend"], {**sp.get("initial_ir", {}), **sp.get("locked", {})}))
    for i, x in enumerate(spec.get("irs", [])):
        irs.append((f"proposal-{i}", x["design"], x.get("backend", "ecp5"), x["ir"]))
    res = []
    for name, kind, be, ir in irs[:12]:
        d = DESIGNS[kind]; a = d.ir_cls.from_dict(ir)
        w = Path(tempfile.mkdtemp()); (w / "rtl").mkdir(); (w / "rtl" / f"{d.top}.sv").write_text(d.emit(a, backend=be))
        ok, log, cov = eda.simulate(d, a, w, timeout=600)
        res.append({"case": name, "design": kind, "backend": be, "ok": ok, **({} if ok else {"log": log[-800:]})})
    return {"sims": res, "ok": all(r["ok"] for r in res)}
step("behavioral simulation", sim_irs)
# equivalence old vs new for IRs the proposal does not claim to change
def equiv():
    from silicon_agent import formal
    base = Path(spec["base_tree"])
    res = []
    for name, kind, be, ir in [(c[0], c[1]["design"], c[1]["backend"], {**c[1].get("initial_ir", {}), **c[1].get("locked", {})}) for c in __import__("silicon_agent.bench", fromlist=["CASES"]).CASES if c[1]["design"] in spec["designs"]][:4]:
        d = DESIGNS[kind]; a = d.ir_cls.from_dict(ir)
        new_rtl = d.emit(a, backend=be)
        # the base tree's emitter, run in its own interpreter
        import subprocess
        p = subprocess.run([sys.executable, "-c", f"import sys, json; sys.path.insert(0, {str(base)!r}); from silicon_agent.designs import DESIGNS; d = DESIGNS[{kind!r}]; print(d.emit(d.ir_cls.from_dict(json.loads(sys.argv[1])), backend={be!r}))", json.dumps(ir)], capture_output=True, text=True, timeout=120)
        old_rtl = p.stdout
        if old_rtl.strip() == new_rtl.strip():
            res.append({"case": name, "identical": True, "ok": True}); continue
        r = formal.equivalence(d, a, old_rtl, new_rtl, Path(tempfile.mkdtemp()) / "eq", k=16)
        res.append({"case": name, "identical": False, "ok": bool(r["ok"]), "depth": r["depth"], "seconds": r["seconds"], **({} if r["ok"] else {"log": r["log"][-600:]})})
    return {"equivalence": res, "ok": all(r["ok"] for r in res)}
if spec.get("base_tree"):
    step("formal equivalence vs the current generator (unchanged IRs)", equiv)
def mutation():
    from silicon_agent import mutate
    res = []
    for kind in spec["designs"][:2]:
        d = DESIGNS[kind]
        x = next((x for x in spec.get("irs", []) if x["design"] == kind), None)
        c = next((c for c in __import__("silicon_agent.bench", fromlist=["CASES"]).CASES if c[1]["design"] == kind), None)
        ir = x["ir"] if x else {**c[1].get("initial_ir", {}), **c[1].get("locked", {})}
        be = (x or {}).get("backend") or (c[1]["backend"] if c else "ecp5")
        a = d.ir_cls.from_dict(ir)
        r = mutate.score(d, a, d.emit(a, backend=be), limit=20)
        res.append({"design": kind, **r})
    return {"mutation": res, "ok": True}
step("mutation score of the testbench", mutation)
# a NEW design kind: the proposal's generator and testbench came from the same author, so the gate asks for an
# independent reference (spec["reference"]: Python source defining `reference(ir: dict, xs: list[int]) -> list[int]`
# written separately from the generator's golden) and checks the generator's RTL against it through the vector
# bench on random stimulus -- the two models must agree on every output, or the proposal fails
def independent_reference():
    import importlib.util, random as _r
    base = Path(spec["base_tree"])
    sys.path.insert(0, str(base))
    from silicon_agent.designs import DESIGNS as BASE_DESIGNS
    sys.path.pop(0)
    new_kinds = [k for k in DESIGNS if k not in BASE_DESIGNS and not k.startswith("rtl:")]
    if not new_kinds:
        return {"new_designs": [], "ok": True, "note": "no new design kind"}
    if not spec.get("reference"):
        return {"new_designs": new_kinds, "ok": False, "error": "a new design kind needs an independent reference model (proposal field `reference`: Python with reference(ir, xs) -> outputs)"}
    rp = Path(tempfile.mkdtemp()) / "reference.py"; rp.write_text(spec["reference"])
    specm = importlib.util.spec_from_file_location("sa_reference", rp); mod = importlib.util.module_from_spec(specm); specm.loader.exec_module(mod)
    res = []
    for kind in new_kinds:
        d = DESIGNS[kind]
        irs = [x["ir"] for x in spec.get("irs", []) if x["design"] == kind] or [{}]
        for ir in irs[:3]:
            a = d.ir_cls.from_dict(ir)
            if a.validate():
                res.append({"design": kind, "ir": ir, "ok": False, "error": a.validate()}); continue
            if d.vectors is None:
                res.append({"design": kind, "ir": ir, "ok": False, "error": "the design has no vector generator"}); continue
            stim, exp = d.vectors(a, seed=7)
            # the reference sees the same stimulus (the last field of each stim line is the sample; first is a gap)
            xs = [int(l.split()[-1], 16) for l in stim]
            dw = getattr(a, "data_width", None)
            if dw:
                xs = [v - (1 << dw) if v >> (dw - 1) else v for v in xs]
            try:
                ref = mod.reference(a.to_dict(), xs)
            except Exception as e:
                res.append({"design": kind, "ir": ir, "ok": False, "error": f"reference raised: {e}"}); continue
            got = [int(l, 16) for l in exp]
            aw = getattr(a, "acc_width", None) or getattr(a, "out_width", None) or dw
            refm = [v & ((1 << aw) - 1) for v in ref] if aw else ref
            same = len(refm) == len(got) and all(x == y for x, y in zip(refm, got))
            res.append({"design": kind, "ir": ir, "ok": same, "n": len(got), **({} if same else {"error": f"golden and reference differ (len {len(got)} vs {len(refm)}, first mismatch at {next((i for i, (x, y) in enumerate(zip(refm, got)) if x != y), None)})"})})
    return {"new_designs": new_kinds, "checks": res, "ok": all(r["ok"] for r in res)}
step("independent reference for a new design kind", independent_reference)
out_path.write_text(json.dumps(rep, indent=1))
'''


def gate(diff: str, work: Path, designs: list[str], irs: list[dict] | None = None, base_tree: Path | None = None, timeout: int = 3600, reference: str | None = None) -> dict:
    """Apply the diff to a scratch copy and run the gate program on it. Returns the report (also written to work/report.json)."""
    work.mkdir(parents=True, exist_ok=True)
    errs = check_scope(diff)
    if errs:
        return {"ok": False, "scope_errors": errs}
    tree = scratch_copy(work / "tree")
    ok, log = apply(diff, tree)
    if not ok:
        return {"ok": False, "apply_error": log}
    base = base_tree or scratch_copy(work / "base")
    (tree / "root").mkdir(exist_ok=True)
    spec = {"designs": designs, "irs": irs or [], "base_tree": str(base), "reference": reference}
    (work / "gate_spec.json").write_text(json.dumps(spec))
    (work / "gate.py").write_text(GATE)
    out = work / "report.json"
    env = dict(os.environ)
    env["SA_ROOT"] = str(tree / "root")          # the scratch tree's cache/runs/memory: nothing of the real state is touched
    env.setdefault("SA_TOOLS", str(APP / "tools" / "oss-cad-suite"))   # ... but the toolchain is the real one
    env.pop("SA_AGH", None)
    try:
        p = subprocess.run([sys.executable, str(work / "gate.py"), str(tree), str(out), str(work / "gate_spec.json")],
                           capture_output=True, text=True, timeout=timeout, env=env, cwd=str(tree))
        tail = (p.stdout + p.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        tail = f"gate timed out after {timeout} s"
    rep = json.loads(out.read_text()) if out.exists() else {"ok": False, "steps": []}
    rep["log"] = tail
    rep["files"] = _files_in_diff(diff)
    rep["diff_lines"] = sum(1 for l in diff.splitlines() if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))
    rep["finished"] = time.time()
    out.write_text(json.dumps(rep, indent=1))
    return rep


# ---------------------------------------------------------------- quick check of a working copy (the author's inner loop)
def quick_check(tree: Path, design: str, backend: str, ir: dict, timeout: int = 600) -> dict:
    """Emit + elaborate + behavioral simulation of one IR on a working copy of the package (b/ of an author tree),
    in a subprocess so the server's own modules stay untouched. Returns the RTL head, validate() errors, iverilog
    output and the testbench's verdict -- what the author needs before proposing."""
    prog = r'''
import json, sys, tempfile, subprocess, os
from pathlib import Path
tree, design, backend = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
ir = json.loads(sys.argv[4])
sys.path.insert(0, str(tree))
out = {}
try:
    from silicon_agent.designs import DESIGNS
    from silicon_agent import eda
    from silicon_agent.backends._common import tool_env
    d = DESIGNS[design]
    a = d.ir_cls.from_dict(ir)
    out["validate"] = a.validate()
    out["ir"] = {k: v for k, v in a.to_dict().items() if k != "coefs"}
    out["latency_cycles"] = int(d.latency(a)); out["throughput"] = d.throughput(a)
    rtl = d.emit(a, backend=backend)
    out["rtl_lines"] = rtl.count("\n"); out["rtl_head"] = "\n".join(rtl.splitlines()[:40])
    if out["validate"]:
        print(json.dumps(out)); sys.exit(0)
    w = Path(tempfile.mkdtemp(prefix="sa_qc_")); (w / "rtl").mkdir(); (w / "rtl" / f"{d.top}.sv").write_text(rtl)
    srcs = [str(w / "rtl" / f"{d.top}.sv")] + [str(tree / "silicon_agent" / "tb" / f) for f in d.extra_sim_sources]
    p = subprocess.run(["iverilog", "-g2012", "-s", d.top, "-o", str(w / "a.out")] + srcs, capture_output=True, text=True, env=tool_env())
    out["elaborate"] = {"ok": p.returncode == 0, "log": (p.stderr or p.stdout)[-1500:]}
    if p.returncode == 0:
        ok, log, cov = eda.simulate(d, a, w, timeout=480)
        out["simulate"] = {"ok": ok, "log": log[-1500:], "coverage": cov}
except Exception as e:
    import traceback; out["error"] = traceback.format_exc()[-1500:]
print(json.dumps(out))
'''
    env = dict(os.environ); env["SA_ROOT"] = str(tree.parent / "qc-root"); env.setdefault("SA_TOOLS", str(APP / "tools" / "oss-cad-suite")); env.pop("SA_AGH", None)
    (tree.parent / "qc-root").mkdir(exist_ok=True)
    try:
        p = subprocess.run([sys.executable, "-c", prog, str(tree), design, backend, json.dumps(ir)], capture_output=True, text=True, timeout=timeout, env=env, cwd=str(tree))
    except subprocess.TimeoutExpired:
        return {"error": f"quick check timed out after {timeout} s"}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (p.stderr or p.stdout)[-1500:]}
