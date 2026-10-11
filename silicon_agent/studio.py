"""The design studio: the agent writes RTL for a block no generator covers, or rewrites a generator's design beyond
its knobs, and the result enters the same ladder as everything else -- as an authored BYO-RTL bundle (rtl.py).

What keeps an authored design honest is that nobody who wrote the RTL also wrote the check:

  designer session   (AGH, deep model, its own working directory)  writes rtl/*.sv, golden.py, design.json, SPEC.md
  reference session  (AGH, no tools, never sees the RTL)           writes reference(params, xs) from SPEC.md alone
  check (here)       elaborate -> golden vs reference (Python vs Python, cheap) -> behavioral simulation of the RTL on
                     the REFERENCE's vectors through the generic replay bench -> stream properties (SymbiYosys)
  register           the bundle becomes design kind rtl:<id>; runs, netlist sims, formal, mutation, the package work
                     as for any bundle; the trust strip shows "independent reference · N samples"

A design forked from a generator's iteration (`fork`) starts from that iteration's emitted RTL and uses the
generator's own vectors as its reference (the function is frozen, the structure is free). Later rewrites are
revisions of the same bundle (`add_revision`); the IR's `rev` selects one, so the planner can ask for a rewrite
mid-run and continue from it.

Layout under SA_ROOT/studio/<id>/:
  studio.json        the record (status, steps, brief, thread, session ids, usage)
  SPEC.md            the functional specification both sessions work from
  work/              the designer's working directory: rtl/, golden.py, design.json, README-studio.md
  ref-cache/         models' scratch (run in an isolated interpreter)
  checks/            check reports and their sim directories
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import rtl as _rtl
from .backends._common import ROOT, tool_env

STUDIO = ROOT / "studio"
RTL_DIR = ROOT / "rtl"
MAX_ROUNDS = int(os.environ.get("SA_STUDIO_ROUNDS", "3"))
# registration gate on the bench's strength: at least this share of compilable mutants of the RTL must be caught by
# the behavioral check (the reference's vectors / the generator's bench); 0 disables the gate
MUTATION_MIN = float(os.environ.get("SA_STUDIO_MUTATION_MIN", "0.6"))
_lock = threading.Lock()

README = """Design studio working directory (silicon-agent).

Write here:
  rtl/<top>.sv (+ other .sv)   the design. Interface contract for a stream block:
                               input clk, input rst (synchronous, active high), input in_valid, output in_ready,
                               input signed [IN_W-1:0] <in>, output out_valid, output signed [OUT_W-1:0] <out>.
                               in_ready must depend on state only (never combinationally on in_valid); outputs are
                               produced in order; nothing valid during reset. Top-level `parameter`s are the knobs.
  golden.py                    def golden(params: dict, xs: list[int]) -> list[int]: your bit-exact model of the
                               RTL (signed integers in arrival order -> signed outputs in order, one per `rate` inputs).
  design.json                  {"top", "params": {NAME: {default, min, max | choices, hint}}, "stream": {"in", "out",
                               "in_width": "<expr of params>", "out_width": "<expr>", "rate": "<expr>"} | null,
                               "latency": "<expr>", "throughput": "<expr>", "throughput_unit", "description"}
                               (a non-stream design sets stream null and adds "tb": "tb.sv", a self-checking bench
                               that prints RESULT PASS / RESULT FAIL)
Check with the sa_studio_check tool (your shell has no iverilog/python toolchain); submit with sa_studio_submit.
The expected outputs in the check come from an INDEPENDENT reference model written from SPEC.md by another
session that never sees your code: if SPEC.md is ambiguous, say so in SPEC.md rather than guessing silently.
"""


# ---------------------------------------------------------------- records
def _dir(sid: str) -> Path:
    return STUDIO / sid


def load(sid: str) -> dict:
    return json.loads((_dir(sid) / "studio.json").read_text())


def save(rec: dict) -> None:
    d = _dir(rec["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "studio.json.tmp"
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    tmp.replace(d / "studio.json")


def list_all() -> list[dict]:
    out = []
    if STUDIO.exists():
        for d in sorted(STUDIO.iterdir()):
            f = d / "studio.json"
            if f.exists():
                try:
                    out.append(json.loads(f.read_text()))
                except ValueError:
                    pass
    return out


def step(rec: dict, name: str, **kw) -> dict:
    """Append a pipeline step to the record (persisted at once: the chat polls it)."""
    st = {"name": name, "t": time.time(), **kw}
    with _lock:
        rec.setdefault("steps", []).append(st)
        save(rec)
    return st


def set_status(rec: dict, status: str, **kw) -> None:
    with _lock:
        rec["status"] = status
        rec.update(kw)
        save(rec)


def new(brief: str, *, name: str | None = None, thread: str | None = None, subject: str = "", spec_text: str | None = None,
        project: str | None = None) -> dict:
    """A fresh studio for a block no generator covers. SPEC.md starts as the brief (the designer refines it)."""
    import secrets
    sid = time.strftime("%Y%m%d-%H%M%S") + "-s" + secrets.token_hex(2)
    d = _dir(sid)
    (d / "work" / "rtl").mkdir(parents=True)
    (d / "work" / "README-studio.md").write_text(README)
    (d / "SPEC.md").write_text((spec_text or brief).strip() + "\n")
    rec = {"id": sid, "name": (name or brief)[:120], "brief": brief, "thread": thread, "project": project, "subject": subject,
           "status": "new", "origin": None, "steps": [], "rounds": 0, "revisions": 0, "created": time.time(), "usage": {}}
    save(rec)
    return rec


# ---------------------------------------------------------------- fork: a generator's design, out of its knobs
def _port_fields(ports: dict) -> tuple[list[dict], list[dict], dict]:
    """Split a generator top's ports into the stream's input fields, output fields and the control names
    (valid in / ready / valid out). Widths must already be literal ([hi:lo])."""
    ctrl = {"valid_in": "in_valid", "ready": None, "valid_out": "out_valid"}
    ins, outs = [], []
    for n, (dd, t) in ports.items():
        if n in ("clk", "rst"):
            continue
        if dd == "input" and n == "in_valid":
            continue
        if dd == "output" and n == "out_valid":
            continue
        if dd == "output" and n in ("in_ready", "busy"):
            ctrl["ready"] = n if n == "in_ready" else None   # matmul's `busy` is not a ready: the bench drives at full rate
            continue
        m = re.search(r"\[(\d+):(\d+)\]", t)
        if not m and "[" in t:
            raise ValueError(f"port {n} has a width the fork cannot resolve: {t}")
        w = abs(int(m.group(1)) - int(m.group(2))) + 1 if m else 1
        f = {"name": n, "width": str(w), "signed": "signed" in t}
        (ins if dd == "input" else outs).append(f)
    return ins, outs, ctrl


def fork(job: dict, iteration: int | None = None, *, brief: str = "", name: str | None = None, thread: str | None = None,
         subject: str = "", project: str | None = None) -> dict:
    """A studio seeded with one iteration's RTL (the summary's best when `iteration` is None): the reference is the
    generator's own vectors for that IR, so the function is frozen and the designer is free to restructure. Works
    for every generator: the interface is read from the emitted top's ports (one accepted beat of input fields,
    one valid beat of output fields) and the generator's bench replays its own vectors."""
    from .designs import DESIGNS
    from .prompts import prompts_for
    spec = job["spec"]
    kind = spec.get("design", "matmul")
    if kind.startswith("rtl:"):
        raise ValueError("this run is already on a studio / BYO design: revise it (sa_studio_revise) instead of forking")
    d = DESIGNS[kind]
    results = [e for e in job.get("events", []) if e.get("kind") == "result"]
    if iteration is None:
        best = (job.get("summary") or {}).get("best")
        if not best:
            raise ValueError("the run has no feasible design to fork from; name an iteration")
        ir_d, label = best["ir"], f"best (iteration {best.get('iteration')})"
    else:
        e = next((x for x in results if x.get("iteration") == iteration and not x.get("slot")), None) or next((x for x in results if x.get("iteration") == iteration), None)
        if not e:
            raise ValueError(f"no iteration {iteration} in this run")
        ir_d, label = {**spec.get("locked", {}), **e["ir"]}, f"iteration {iteration}"
    ir = d.ir_cls.from_dict(ir_d)
    backend = spec.get("backend", "ecp5")
    text = d.emit(ir, backend=backend)
    ports = {n: (dd, t) for dd, t, n in _rtl._ports({"top": d.top, "files": {"x.sv": text}})}
    ins, outs, ctrl = _port_fields(ports)
    tp = d.throughput(ir)
    rate = int(round(1 / tp)) if 0 < tp < 1 and abs(1 / tp - round(1 / tp)) < 1e-6 else (ir_d.get("decim", 1) if kind == "cic" else 1)
    frozen = {k: v for k, v in ir.to_dict().items() if k != "coefs"}
    what = prompts_for(kind)["what"]
    task = spec.get("task", kind)
    iface = (" + ".join(f"{f['name']} ({f['width']} bits{' signed' if f['signed'] else ''})" for f in ins) + " -> " +
             " + ".join(f"{f['name']} ({f['width']} bits{' signed' if f['signed'] else ''})" for f in outs))
    spec_md = (f"# {task}\n\n"
               f"Forked from run {job.get('id', '?')}, {label}: {what}.\n\n"
               f"## Function (frozen)\nThe block must compute exactly what the generator's RTL computes for this IR -- same outputs for the same inputs, "
               f"in order. Interface: {ctrl['valid_in']}{'/' + ctrl['ready'] if ctrl['ready'] else ' (no back-pressure: the source may push every cycle)'} with {iface}, "
               f"{ctrl['valid_out']} marks each output beat; synchronous active-high reset. The expected outputs in every check are the generator's own "
               f"test vectors for this IR, replayed by the generator's own bench, so the port names, widths and the beat protocol must stay exactly as they are.\n\n"
               f"## The design to start from\n`work/rtl/{d.top}.sv` is the generator's output for IR {json.dumps(frozen)}. "
               f"Its structure is free to change (pipelining, resource sharing, a different architecture) as long as the function and the interface hold.\n\n"
               f"## Goal\n{brief.strip() or 'Improve the design beyond what the IR knobs allow; say in design.json which parameters remain tunable.'}\n")
    rec = new(brief or f"fork of {task}", name=name or f"{task} (studio)", thread=thread, subject=subject, spec_text=spec_md, project=project)
    w = _dir(rec["id"]) / "work"
    (w / "rtl" / f"{d.top}.sv").write_text(text)
    stream = {"inputs": ins, "outputs": outs, "rate": str(rate), "valid_in": ctrl["valid_in"], "valid_out": ctrl["valid_out"], **({"ready": ctrl["ready"]} if ctrl["ready"] else {})}
    (w / "design.json").write_text(json.dumps({
        "top": d.top, "params": {}, "stream": stream,
        "latency": str(int(d.latency(ir))), "throughput": str(tp), "throughput_unit": d.throughput_unit,
        "description": f"{task} -- forked from {kind} {label}"}, indent=1))
    rec["origin"] = {"job": job.get("id"), "iteration": iteration, "design": kind, "backend": backend, "ir": ir.to_dict(), "label": label}
    rec["reference"] = {"kind": "generator", "origin": {"design": kind, "ir": ir.to_dict()}, "code": None}
    rec["status"] = "designing"
    save(rec)
    return rec


# ---------------------------------------------------------------- the bundle a working directory describes
def design_json(sid: str) -> dict:
    f = _dir(sid) / "work" / "design.json"
    if not f.exists():
        raise ValueError("work/design.json is missing")
    try:
        return json.loads(f.read_text())
    except ValueError as e:
        raise ValueError(f"work/design.json is not valid JSON: {e}")


def work_files(sid: str) -> dict:
    w = _dir(sid) / "work" / "rtl"
    files = {p.name: p.read_text() for p in sorted(w.glob("*.sv")) + sorted(w.glob("*.v")) if p.is_file()}
    if not files:
        raise ValueError("work/rtl/ has no .sv files")
    if sum(len(v) for v in files.values()) > 2_000_000:
        raise ValueError("the RTL is larger than 2 MB")
    return files


def bundle_of(rec: dict, files: dict | None = None) -> dict:
    """The bundle (as rtl.py reads it) for the current working directory, with the registered revisions, if any."""
    sid = rec["id"]
    dj = design_json(sid)
    top = str(dj.get("top", "")).strip()
    if not re.match(r"^[A-Za-z_]\w*$", top):
        raise ValueError("design.json: top must be the top module's name")
    params = dj.get("params") or {}
    if not isinstance(params, dict) or any(not isinstance(v, dict) for v in params.values()):
        raise ValueError("design.json: params must be {NAME: {default, min, max | choices, hint}}")
    files = files or work_files(sid)
    # the registered bundle (with every revision's files) lives on disk; the record keeps only a summary of it
    bf = RTL_DIR / sid / "bundle.json"
    prev = json.loads(bf.read_text()) if rec.get("bundle") and bf.exists() else {}
    revs = list(prev.get("revisions") or [])
    b = {"id": sid, "name": rec["name"], "top": top, "files": revs[0]["files"] if revs else files, "tb": (dj.get("tb") and (_dir(sid) / "work" / dj["tb"]).read_text()) or "",
         "params": params, "port_widths": dj.get("port_widths") or {}, "latency": dj.get("latency"), "throughput": dj.get("throughput"),
         "throughput_unit": dj.get("throughput_unit", "items/cycle"), "description": str(dj.get("description", ""))[:2000], "notes": str(dj.get("notes", ""))[:4000],
         "stream": dj.get("stream") or None, "authored": True, "reference": rec.get("reference"), "golden": _read(_dir(sid) / "work" / "golden.py"),
         "revisions": revs, "subject": rec.get("subject", ""), "created": rec["created"], "thread": rec.get("thread"), "project": rec.get("project"),
         "dir": str(_dir(sid)), "studio": sid}
    if b["stream"] and not isinstance(b["stream"], dict):
        raise ValueError("design.json: stream must be an object or null")
    if not b["stream"] and "RESULT" not in b["tb"]:
        raise ValueError("a non-stream design needs design.json tb = a self-checking bench that prints RESULT PASS/FAIL")
    b["hash"] = _rtl.bundle_hash(b)
    return b


def _read(p: Path) -> str | None:
    return p.read_text() if p.exists() else None


# ---------------------------------------------------------------- check: the gate every version of the RTL passes
def check(rec: dict, params: dict | None = None, files: dict | None = None, tag: str | None = None, properties: bool = True) -> dict:
    """elaborate -> golden vs reference -> behavioral simulation on the reference's vectors -> stream properties.
    Returns {"ok", "steps": [{"name", "ok", "seconds", ...}]} and keeps the sim directory under checks/<tag>/."""
    from . import eda, formal
    from .designs import Design
    sid = rec["id"]
    t_all = time.time()
    rep = {"ok": True, "steps": [], "tag": tag or time.strftime("%H%M%S")}
    cdir = _dir(sid) / "checks" / rep["tag"]
    if cdir.exists():
        shutil.rmtree(cdir)
    cdir.mkdir(parents=True)

    def run(name, fn):
        t0 = time.time()
        r = {"name": name}
        try:
            r.update(fn() or {})
            r.setdefault("ok", True)
        except Exception as e:  # noqa: BLE001 -- the report is the diagnosis
            r.update({"ok": False, "error": str(e)[-1500:]})
        r["seconds"] = round(time.time() - t0, 1)
        rep["steps"].append(r)
        rep["ok"] = rep["ok"] and bool(r["ok"])
        return r

    bundle = {}
    def read_bundle():
        nonlocal bundle
        bundle = bundle_of(rec, files)
        # the working copy is what is checked, whatever the registered revisions hold
        bundle = {**bundle, "revisions": [], "files": files or work_files(sid)}
        return {"top": bundle["top"], "files": sorted(bundle["files"]), "stream": bool(bundle.get("stream")), "params": list(bundle["params"])}
    if not run("design.json + files", read_bundle)["ok"]:
        rep["seconds"] = round(time.time() - t_all, 1)
        return rep
    A = _rtl.arch_class(bundle)
    a = A.from_dict(params or {})
    errs = a.validate()
    if errs:
        rep["steps"].append({"name": "parameters", "ok": False, "error": "; ".join(errs), "seconds": 0})
        rep["ok"] = False
        return rep
    d = _rtl.design_of(bundle, cwd=_dir(sid) / "ref-cache")
    text = d.emit(a)
    (cdir / "rtl").mkdir()
    (cdir / "rtl" / f"{d.top}.sv").write_text(text)

    def elaborate():
        from .backends._common import TB_DIR
        srcs = [str(cdir / "rtl" / f"{d.top}.sv")] + [str(TB_DIR / f) for f in d.extra_sim_sources]
        p = subprocess.run(["iverilog", "-g2012", "-s", d.top, "-o", str(cdir / "elab.out"), *srcs], capture_output=True, text=True, env=tool_env(), timeout=300)
        return {"ok": p.returncode == 0, "log": (p.stderr or p.stdout)[-1500:], "lines": text.count("\n")}
    if not run("elaborate (iverilog)", elaborate)["ok"]:
        rep["seconds"] = round(time.time() - t_all, 1)
        return rep

    st = _rtl.stream_of(bundle, a)
    ref = rec.get("reference") or {}
    if st and bundle.get("golden") and ref.get("kind") == "independent" and ref.get("code"):
        def models():
            r = _rtl.compare_models(bundle["golden"], ref["code"], _rtl._env(bundle, a), st, _dir(sid) / "ref-cache")
            return {"ok": r["agree"], **r}
        run("golden vs independent reference", models)
    if st and ref.get("kind") == "generator" and bundle.get("golden") and len(st["inputs"]) == 1 and len(st["outputs"]) == 1 and len((_rtl.vectors(bundle, a, seed=1)[0][0] or "").split()) == 2:
        # a one-field stream forked from a generator whose bench lines are `<gap> <sample>`: the designer's golden
        # can be checked against the generator's vectors directly (other benches carry extra fields per line)
        def models_gen():
            stim, exp = _rtl.vectors(bundle, a, seed=1)
            xs = [int(l.split()[-1], 16) for l in stim]
            xs = [v - (1 << st["in_width"]) if v >> (st["in_width"] - 1) else v for v in xs]
            got = _rtl.run_model(bundle["golden"], _rtl._env(bundle, a), xs, _dir(sid) / "ref-cache", name="golden")
            want = [int(l, 16) for l in exp]
            gm = [int(v) & ((1 << st["out_width"]) - 1) for v in got]
            i = next((k for k, (x, y) in enumerate(zip(gm, want)) if x != y), None)
            ok = i is None and len(gm) == len(want)
            return {"ok": ok, "samples": len(want), **({} if ok else {"mismatch": {"index": i if i is not None else min(len(gm), len(want)), "golden": gm[i] if i is not None else len(gm), "reference": want[i] if i is not None else len(want)}})}
        run("golden vs generator vectors", models_gen)

    def sim():
        ok, log, _ = eda.simulate(d, a, cdir, timeout=600)
        return {"ok": ok, "log": log[-1500:]}
    if not run("behavioral simulation on the reference's vectors" if st else "behavioral simulation (self-checking bench)", sim)["ok"]:
        rep["seconds"] = round(time.time() - t_all, 1)
        return rep
    if properties:
        def props():
            r = formal.properties(d, a, text, cdir / "props", k=12)
            return {"ok": bool(r["ok"]), "depth": r["depth"], "log": r["log"][-800:]}
        run("stream properties (SymbiYosys)", props)
    rep["seconds"] = round(time.time() - t_all, 1)
    (cdir / "report.json").write_text(json.dumps(rep, indent=1))
    return rep


def knob_points(bundle: dict, limit: int = 6) -> list[dict]:
    """A few parameter sets besides the defaults that the declared ranges allow: each tunable knob at its other
    choices / its min and max, one knob at a time. The planner will try these; they must work before registration."""
    decl = bundle.get("params") or {}
    base = {k: int(v.get("default", 0)) for k, v in decl.items()}
    pts: list[dict] = []
    for k, d in decl.items():
        vals = [v for v in (d.get("choices") or []) if v != base[k]]
        if not vals and "min" in d and "max" in d and d["min"] != d["max"]:
            vals = sorted({d["min"], d["max"]} - {base[k]})
        for v in vals[:2]:
            pts.append({**base, k: v})
    return pts[:limit]


def check_knobs(rec: dict, files: dict | None = None, tag: str = "knobs") -> dict:
    """Elaborate + simulate (no properties) at the other declared knob values: a knob the RTL does not really
    implement fails here instead of in every run that tries it."""
    b = bundle_of(rec, files)
    out = {"ok": True, "points": []}
    for i, p in enumerate(knob_points(b)):
        rep = check(rec, params=p, files=files, tag=f"{tag}{i}", properties=False)
        out["points"].append({"params": {k: v for k, v in p.items() if v != (b["params"][k].get("default"))}, "ok": rep["ok"],
                              **({} if rep["ok"] else {"failed": next((s for s in rep["steps"] if not s.get("ok")), {}).get("name"), "log": str(next((s.get("log") or s.get("error") or s.get("mismatch") for s in rep["steps"] if not s.get("ok")), ""))[-600:]})})
        out["ok"] = out["ok"] and rep["ok"]
    return out


def check_mutation(rec: dict, files: dict | None = None, limit: int = 16) -> dict:
    """The strength of the check itself: inject bugs into the RTL (mutate.py) and count how many the behavioral
    check catches. A weak reference (too few samples, no corner) lets mutants through; the gate asks for MUTATION_MIN."""
    from . import mutate
    b = {**bundle_of(rec, files), "revisions": [], "files": files or work_files(rec["id"])}
    d = _rtl.design_of(b, cwd=_dir(rec["id"]) / "ref-cache")
    a = d.ir_cls.from_dict({})
    r = mutate.score(d, a, d.emit(a), work=_dir(rec["id"]) / "checks" / "mutation", limit=limit, budget_s=240)
    ok = r.get("score") is None and r.get("baseline") is not False or (r.get("score") or 0) >= MUTATION_MIN
    return {"ok": bool(ok), **{k: r.get(k) for k in ("total", "killed", "survived", "score", "uncompilable", "baseline", "error")}}


def _gate(rec: dict, files: dict, tag: str) -> dict:
    """The full registration gate: the check at the defaults, every declared knob value, the mutation score."""
    rep = check(rec, files=files, tag=tag)
    if not rep["ok"]:
        return rep
    kn = check_knobs(rec, files=files, tag=f"{tag}-knob")
    rep["steps"].append({"name": "declared knobs (each tunable parameter at its other values)", "ok": kn["ok"], "points": kn["points"],
                         **({} if kn["ok"] else {"error": "a declared knob value does not pass: fix the RTL/golden for it, or narrow the parameter's range in design.json: " + "; ".join(f"{p['params']} -> {p.get('failed')}" for p in kn["points"] if not p["ok"])})})
    rep["ok"] = rep["ok"] and kn["ok"]
    if rep["ok"] and MUTATION_MIN > 0:
        mu = check_mutation(rec, files=files)
        rep["steps"].append({"name": f"mutation score of the check (>= {MUTATION_MIN:.0%} of injected bugs caught)", **mu,
                             **({} if mu["ok"] else {"error": f"the check lets too many injected bugs through ({mu.get('killed')}/{mu.get('total')} caught; survivors: {', '.join((mu.get('survived') or [])[:6])}): "
                                                               "the stimulus misses the cases those mutants break -- sharpen SPEC.md's corner cases (the reference is regenerated), or the design has dead logic a mutant can flip without effect (remove it)"})})
        rep["ok"] = rep["ok"] and mu["ok"]
    return rep


def register(rec: dict, note: str = "initial design") -> dict:
    """The working directory becomes revision 0 of a registered bundle (design kind rtl:<id>); it must pass the
    gate: the check at the defaults and at every declared knob value, and the mutation score."""
    from .designs import register_rtl
    files = work_files(rec["id"])
    rep = _gate(rec, files, "register")
    if not rep["ok"]:
        return {"ok": False, "check": rep}
    b = bundle_of(rec, files)
    b["revisions"] = [{"rev": 0, "files": files, "note": note[:300], "created": time.time(), "check": _brief(rep)}]
    b["files"] = files
    b["check"] = _brief(rep)
    b["hash"] = _rtl.bundle_hash(b)
    (RTL_DIR / rec["id"]).mkdir(parents=True, exist_ok=True)
    (RTL_DIR / rec["id"] / "bundle.json").write_text(json.dumps(b, ensure_ascii=False, indent=1))
    register_rtl(b)
    with _lock:
        rec["bundle"] = {k: v for k, v in b.items() if k not in ("files", "golden", "tb")}
        rec["bundle"]["revisions"] = [{k: v for k, v in r.items() if k != "files"} for r in b["revisions"]]
        rec["revisions"] = 1
        rec["design"] = f"rtl:{rec['id']}"
        rec["status"] = "registered"
        save(rec)
    return {"ok": True, "design": f"rtl:{rec['id']}", "check": rep}


def add_revision(rec: dict, note: str) -> dict:
    """Snapshot the working directory as the next revision of the registered bundle, if it passes the check."""
    from .designs import register_rtl
    if not rec.get("bundle"):
        return register(rec, note)
    files = work_files(rec["id"])
    f = RTL_DIR / rec["id"] / "bundle.json"
    b = json.loads(f.read_text())
    if any(r["files"] == files for r in b["revisions"]):
        return {"ok": False, "error": "the working copy is identical to an existing revision"}
    n = len(b["revisions"])
    rep = _gate(rec, files, f"rev{n}")
    if not rep["ok"]:
        return {"ok": False, "check": rep}
    # the designer may have changed design.json too (new knobs, latency); the bundle follows it
    nb = bundle_of(rec, files)
    b.update({k: nb[k] for k in ("params", "port_widths", "latency", "throughput", "throughput_unit", "description", "notes", "stream", "golden")})
    b["revisions"].append({"rev": n, "files": files, "note": note[:300], "created": time.time(), "check": _brief(rep)})
    b["hash"] = _rtl.bundle_hash(b)
    f.write_text(json.dumps(b, ensure_ascii=False, indent=1))
    register_rtl(b)
    with _lock:
        rec["bundle"]["revisions"] = [{k: v for k, v in r.items() if k != "files"} for r in b["revisions"]]
        rec["revisions"] = n + 1
        save(rec)
    return {"ok": True, "rev": n, "check": rep}


def _brief(rep: dict) -> dict:
    ref = next((s for s in rep["steps"] if s["name"].startswith("golden vs")), None)
    sim = next((s for s in rep["steps"] if s["name"].startswith("behavioral")), None)
    pr = next((s for s in rep["steps"] if s["name"].startswith("stream properties")), None)
    mu = next((s for s in rep["steps"] if s["name"].startswith("mutation score")), None)
    return {"ok": rep["ok"], "golden_vs_reference": {"ok": ref["ok"], "samples": ref.get("samples")} if ref else None,
            "sim": sim["ok"] if sim else None, "properties": pr["ok"] if pr else None,
            "mutation": {"killed": mu.get("killed"), "total": mu.get("total"), "score": mu.get("score")} if mu else None, "seconds": rep.get("seconds")}


def file_listing(sid: str) -> list[dict]:
    w = _dir(sid) / "work"
    out = []
    for p in sorted(w.rglob("*")):
        if p.is_file():
            out.append({"path": str(p.relative_to(w)), "size": p.stat().st_size})
    return out


# ---------------------------------------------------------------- the sessions (Agnes Harness)
DESIGNER_METHOD = """You are the designer session of silicon-agent's studio. Your working directory is this studio's work/ (README-studio.md
explains the files). Write the RTL for the block SPEC.md describes, a bit-exact golden.py, and design.json; then check with
sa_studio_check and submit with sa_studio_submit. Rules: synthesizable SystemVerilog-2012 that iverilog, yosys and Vivado accept
(no interfaces, no classes, no unpacked-array ports); the stream contract in README-studio.md; parameters as top-level
`parameter`s and listed in design.json params with ranges (these are the knobs the planner will tune -- expose pipelining and
sharing choices as parameters when they are cheap to make switchable -- but every value in a declared range is checked at
submit: declare only knobs the RTL and golden.py really implement, narrow the range otherwise); golden.py standard library only, integer arithmetic,
exactly the RTL's truncation/rounding/wrap. The expected outputs in the check come from an independent reference written from
SPEC.md by a session that never sees your code: read SPEC.md as that session will, and if it is ambiguous, make SPEC.md precise
(state the arithmetic) before you write RTL. A failed check comes back to you with the log; fix and check again."""

REFERENCE_PROMPT = """You are writing an INDEPENDENT reference model of a hardware block, from its specification only, for a verification gate.
Do not call any tool. Reply with ONLY a Python code block defining `def reference(params: dict, xs: list[int]) -> list[int]`
(standard library only). `params` are the block's parameters (names and defaults below); `xs` are the signed input samples in
arrival order; return the signed output samples in order, exactly as fixed-point hardware would produce them (integer
arithmetic, the widths/truncation/rounding/saturation the specification states; one output per `rate` inputs for a rate-changing
block; samples before the stream are zero; nothing is emitted for a partial frame at the end unless the specification says so).
Be literal about the definition; do not optimise; do not add behaviour the specification does not state."""


def _agh():
    from . import agh
    return agh


def designer_prompt(rec: dict, text: str, lang: str = "zh") -> str:
    return f"[silicon-agent studio · {rec['id']} · answer in {'Chinese' if lang == 'zh' else 'English'}]\n{text}"


def designer_session(rec: dict) -> str:
    """The designer's AGH session (one per studio, resumed across turns and restarts)."""
    agh = _agh()
    c = agh.client()
    cwd = str(_dir(rec["id"]) / "work")
    sid = rec.get("designer_session")
    if sid:
        try:
            c.load_session(sid, cwd)
            return sid
        except agh.AghError:
            pass
    sid = c.new_session(cwd, preset=os.environ.get("SA_STUDIO_PRESET", "author"), session_key=f"sa-studio-{rec['id']}")
    model = os.environ.get("SA_STUDIO_MODEL", "deepseek-v4-pro")
    try:
        c.set_model(sid, "deepseek", model, "high")
    except agh.AghError as e:
        print(f"studio {rec['id']}: setModel failed: {e}", flush=True)
    set_status(rec, rec["status"], designer_session=sid)
    return sid


def designer_turn(rec: dict, text: str, lang: str = "zh", on_event=None) -> dict:
    agh = _agh()
    c = agh.client()
    sid = designer_session(rec)
    r = c.prompt(sid, designer_prompt(rec, text, lang), on_event=on_event, timeout=3600)
    u = c.usage(sid)
    with _lock:
        rec.setdefault("turns", []).append({"t": time.time(), "text": (r.get("text") or "")[-4000:], "tools": [t.get("name") for t in r.get("tools") or []], "usage": u})
        rec["usage"] = u.get("session") or rec.get("usage") or {}
        save(rec)
    return r


def independent_reference(desc: str, key: str, model: str | None = None) -> str:
    """Ask a no-tool session for reference(params, xs) from the description alone; returns the Python source."""
    agh = _agh()
    c = agh.client()
    sid = c.new_session(agh.workspace(), preset=os.environ.get("SA_AGH_PRESET") or None, session_key=key)
    try:
        c.set_model(sid, "deepseek", model or os.environ.get("SA_STUDIO_MODEL", "deepseek-v4-pro"), "high")
    except agh.AghError:
        pass
    r = c.prompt(sid, f"[silicon-agent reference · answer in English]\n{REFERENCE_PROMPT}\n\n{desc}", timeout=900)
    code = re.search(r"```(?:python)?\s*\n(.*?)```", r.get("full_text") or r.get("text") or "", re.S)
    if not code or "def reference" not in code.group(1):
        raise RuntimeError("no reference() in the reply")
    return code.group(1)


def reference_desc(rec: dict) -> str:
    """What the reference session sees: SPEC.md and the interface/parameters from design.json -- never the RTL."""
    spec_md = (_dir(rec["id"]) / "SPEC.md").read_text()
    try:
        dj = design_json(rec["id"])
    except ValueError:
        dj = {}
    params = dj.get("params") or {}
    st = dj.get("stream") or {}
    lines = [f"Specification:\n{spec_md}"]
    if params:
        lines.append("Parameters (name: default -- meaning):\n" + "\n".join(f"  {k}: {v.get('default')} -- {v.get('hint', '')}" for k, v in params.items()))
    if st:
        lines.append(f"Interface: input `{st.get('in', 'in_data')}` ({st.get('in_width')} bits signed) -> output `{st.get('out', 'out_data')}` ({st.get('out_width')} bits signed); one output per {st.get('rate', 1)} input(s).")
    return "\n\n".join(lines)


def run_pipeline(rec: dict, lang: str = "zh", on_event=None) -> dict:
    """designing -> referencing -> checking (rounds) -> registered | failed. Blocking; the server runs it in a thread."""
    sid = rec["id"]
    try:
        set_status(rec, "designing")
        st = step(rec, "designer: write the design", ok=None)
        kick = ("SPEC.md in the parent directory (../SPEC.md) is the task. Write work/rtl/*.sv, golden.py and design.json, run sa_studio_check "
                f"(studio id {sid}) until it passes, then sa_studio_submit. Report what you built and what the knobs are.")
        if rec.get("origin"):
            kick = (f"../SPEC.md is the task: a design forked from the {rec['origin']['design']} generator ({rec['origin']['label']} of run {rec['origin']['job']}). "
                    f"work/rtl/ already holds the generator's RTL; design.json has the interface. Rewrite or restructure it toward the goal in SPEC.md, keep the function "
                    f"(the check compares against the generator's own vectors), expose the choices you make as parameters, run sa_studio_check (studio id {sid}) until it passes, "
                    f"then sa_studio_submit. Report what you changed and why.")
        r = designer_turn(rec, kick, lang, on_event)
        # the designer's own sa_studio_check / sa_studio_submit may have appended steps meanwhile: close this one by handle
        st.update({"ok": True, "text": (r.get("text") or "")[-1500:]})
        save(rec)
        if rec.get("bundle"):
            # the designer's own sa_studio_submit registered the design during the turn
            set_status(rec, "registered", design=f"rtl:{sid}")
            return rec
        # the designer may have submitted inside its turn (sa_studio_submit); otherwise the server submits now
        return submit(rec, lang, on_event)
    except Exception as e:  # noqa: BLE001
        step(rec, "pipeline error", ok=False, error=str(e)[-1500:])
        set_status(rec, "failed")
        return rec


def ensure_reference(rec: dict) -> dict:
    """The independent reference for a fresh design (a fork already has the generator's vectors)."""
    ref = rec.get("reference") or {}
    if ref.get("kind") == "generator" or (ref.get("kind") == "independent" and ref.get("code")):
        return ref
    try:
        dj = design_json(rec["id"])
    except ValueError:
        dj = {}
    if not (dj.get("stream")):
        # a non-stream design keeps its own self-checking bench; no reference can be replayed through the generic bench
        rec["reference"] = {"kind": "self-check", "code": None, "origin": None}
        save(rec)
        return rec["reference"]
    set_status(rec, "referencing")
    st = step(rec, "reference: independent model from SPEC.md", ok=None)
    code = independent_reference(reference_desc(rec), f"sa-reference-{rec['id']}-{len(rec.get('steps', []))}")
    (_dir(rec["id"]) / "reference.py").write_text(code)
    rec["reference"] = {"kind": "independent", "code": code, "origin": None}
    st["ok"] = True
    save(rec)
    return rec["reference"]


def submit(rec: dict, lang: str = "zh", on_event=None) -> dict:
    """Reference (once) + check; a failing check goes back to the designer, up to MAX_ROUNDS times."""
    sid = rec["id"]
    for rnd in range(MAX_ROUNDS + 1):
        ensure_reference(rec)
        set_status(rec, "checking", rounds=rnd)
        st = step(rec, f"check (round {rnd + 1})", ok=None)
        res = register(rec, note="initial design" if not rec.get("bundle") else f"revision after check round {rnd + 1}") if not rec.get("bundle") else add_revision(rec, f"round {rnd + 1}")
        rep = res.get("check") or {}
        st.update({"ok": bool(res.get("ok")), "report": rep, **({"error": res["error"]} if res.get("error") else {})})
        save(rec)
        if res.get("ok"):
            set_status(rec, "registered", design=f"rtl:{sid}")
            return rec
        if rnd == MAX_ROUNDS:
            break
        failed = [s for s in rep.get("steps", []) if not s.get("ok")]
        if failed and failed[0]["name"].startswith("golden vs independent"):
            # the two models disagree: both sides look again -- the designer may fix golden/RTL or sharpen SPEC.md; the
            # reference is rewritten from the (possibly sharpened) spec once
            msg = (f"Check round {rnd + 1} failed: your golden.py and the independent reference model disagree at {json.dumps(failed[0].get('mismatch'))}"
                   f"{(' (' + failed[0]['error'] + ')') if failed[0].get('error') else ''}. Either your model/RTL or the specification is wrong. "
                   "If SPEC.md is ambiguous about this case, rewrite the relevant part of ../SPEC.md precisely (the reference will be regenerated from it); "
                   "otherwise fix golden.py and the RTL. Then sa_studio_check and sa_studio_submit again.")
            set_status(rec, "revising")
            designer_turn(rec, msg, lang, on_event)
            rec["reference"] = {"kind": "independent", "code": None, "origin": None}
            save(rec)
            continue
        msg = (f"Check round {rnd + 1} failed. Steps:\n" + "\n".join(f"- {s['name']}: {'ok' if s.get('ok') else 'FAILED'}" + (f" -- {str(s.get('error') or s.get('log') or s.get('mismatch') or '')[-700:]}" if not s.get('ok') else "") for s in rep.get("steps", []))
               + ("\n" + res["error"] if res.get("error") else "") + "\nFix the design (or golden.py / design.json), run sa_studio_check until it passes, then sa_studio_submit.")
        set_status(rec, "revising")
        designer_turn(rec, msg, lang, on_event)
    set_status(rec, "failed")
    return rec


def revise(rec: dict, instruction: str, context: dict | None = None, lang: str = "zh", on_event=None) -> dict:
    """A rewrite requested mid-run (the planner's `revise`, or the user): the designer changes the RTL, the result is
    checked and becomes the next revision. Returns {"ok", "rev", "check" | "error"}."""
    if rec.get("status") not in ("registered", "failed", "revising", "interrupted"):
        return {"ok": False, "error": f"studio is {rec.get('status')}"}
    if not rec.get("bundle"):
        # nothing registered yet (an interrupted first pipeline): the revision request is the designer's next
        # instruction and the result registers as revision 0
        set_status(rec, "revising")
        step(rec, "revise", ok=None, instruction=instruction[:1000])
        designer_turn(rec, f"Revise the design: {instruction}\nThen sa_studio_check until it passes and sa_studio_submit.", lang, on_event)
        return {"ok": rec.get("status") == "registered", **({"rev": 0} if rec.get("status") == "registered" else {"error": "no passing design yet"})}
    set_status(rec, "revising")
    st = step(rec, "revise", ok=None, instruction=instruction[:1000])
    ctx = ""
    if context:
        ctx = "\nMeasured context: " + json.dumps({k: context[k] for k in ("metrics", "violations", "critical_path", "ir") if k in context}, ensure_ascii=False)[:3000]
    msg = (f"Revise the design: {instruction}{ctx}\nKeep the function and the interface (the check compares against the same reference). Edit work/rtl/ "
           "(and golden.py / design.json if the knobs change), run sa_studio_check until it passes, then sa_studio_submit. Say in one sentence what you changed.")
    before = rec.get("revisions") or 0
    for rnd in range(MAX_ROUNDS):
        r = designer_turn(rec, msg, lang, on_event)
        note = (r.get("text") or instruction)[:300]
        if (rec.get("revisions") or 0) > before:
            # the designer submitted inside its turn (sa_studio_submit registered the revision already)
            res = {"ok": True, "rev": rec["revisions"] - 1}
        else:
            res = add_revision(rec, note)
        if res.get("ok"):
            st.update({"ok": True, "rev": res["rev"], "note": note})
            set_status(rec, "registered")
            return {**res, "note": note}
        rep = res.get("check") or {}
        msg = ("The revision did not pass the check: " + (res.get("error") or "; ".join(f"{s['name']}: {str(s.get('error') or s.get('log') or s.get('mismatch') or '')[-500:]}" for s in rep.get("steps", []) if not s.get("ok")))
               + ". Fix it, sa_studio_check, sa_studio_submit.")
    st.update({"ok": False, "error": "no passing revision after the rounds allowed"})
    set_status(rec, "registered" if rec.get("bundle") else "failed")
    return {"ok": False, "error": st["error"]}
