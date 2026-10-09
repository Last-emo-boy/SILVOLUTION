"""The autonomous design loop: spec -> IR -> RTL -> sim -> synth/P&R -> diagnose -> next IR.

Generic over the design registry (silicon_agent.designs) and the backend table
(silicon_agent.eda.BACKENDS). Design-specific planner knowledge lives in prompts.py.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from . import llm, memory, screen
from .designs import Design, design_for
from .eda import BACKENDS, ROOT, evaluate
from .ir import Constraints, Metrics
from .prompts import PROMPTS, device_facts, prompts_for

SYSTEM = """You are a senior FPGA architect running an autonomous design-space exploration loop.
The hardware is @WHAT@ on @DEVICE_NAME@.
You never write Verilog. You edit an architecture IR; a verified generator emits the RTL. The EDA tools are ground truth.

@IR_SCHEMA@

@DEVICE_FACTS@

Decision rule: a design is FEASIBLE only if every constraint holds. Among feasible designs, prefer by the objective list in order.
Each iteration you must (1) diagnose the last result from its critical path and resource numbers, (2) propose up to three
candidate IRs that attack the diagnosis from different angles, each with a one-line rationale. A cheap screening model
ranks them and only the top candidate is sent through EDA this iteration; the others stay as fallbacks. Order the
candidates by your own confidence. Never repeat an IR already in the history.
When the current best is feasible and you believe no further improvement is likely, set "stop": true, give no
candidates, and put the *evidence* in "stop_reason": which objective term is at a bound that no remaining knob can
move (e.g. "throughput at the DSP cap: 32 PEs = 32 DSP", "acc_width at its legal minimum", "critical path is the
device's intrinsic DSP M->P stage, 2.31 ns, no IR field touches it"), and which knobs were tried without gain. A stop
without a measured bound is not acceptable: if a knob is untested and could plausibly help, try it instead.

Respond with a JSON object:
{
  "diagnosis": "<2-4 sentences: what limits the last design, citing the critical path>",
  "candidates": [
    {"ir": @IR_JSON@, "why": "<one sentence: the mechanism this change exploits>"},
    ...
  ],
  "stop": false,
  "stop_reason": "<only when stop is true: the bound reached and the evidence, 1-3 sentences>"
}"""


def build_system(design: Design, backend: str) -> str:
    p = prompts_for(design.kind)
    return (SYSTEM.replace("@WHAT@", p["what"]).replace("@DEVICE_NAME@", BACKENDS[backend]["name"])
            .replace("@IR_SCHEMA@", p["ir"]).replace("@DEVICE_FACTS@", device_facts(design.kind, backend)).replace("@IR_JSON@", p["ir_json"]))


def merge_locked(ir: dict, locked: dict) -> dict:
    """Locked task fields override the IR; for composite designs `locked["stages"]` is a list of per-stage dicts
    merged stage by stage (so the planner's per-stage knobs survive and the task fields do not)."""
    out = {**ir}
    for k, v in locked.items():
        if k == "stages" and isinstance(v, list) and isinstance(out.get("stages"), list):
            out["stages"] = [{**st, **(v[i] if i < len(v) else {})} for i, st in enumerate(out["stages"])]
        elif k == "nodes" and isinstance(v, list) and isinstance(out.get("nodes"), list):
            out["nodes"] = [{"kind": (v[i].get("kind") if i < len(v) else None) or nd.get("kind"), "ir": {**nd.get("ir", {}), **((v[i].get("ir") or {}) if i < len(v) else {})}}
                            for i, nd in enumerate(out["nodes"])]
        else:
            out[k] = v
    return out


def ir_view(ir, locked: dict) -> dict:
    """IR as shown to the planner: locked fields and bulky task data (coefficients) omitted.
    Composite designs keep their "stages" list (the planner edits per-stage knobs) minus coefficients."""
    d = ir.to_dict()
    out = {k: v for k, v in d.items() if (k not in locked or k in ("stages", "nodes")) and k != "coefs"}
    if isinstance(out.get("stages"), list):
        out["stages"] = [{k: v for k, v in st.items() if k != "coefs"} for st in out["stages"]]
    if isinstance(out.get("nodes"), list):
        out["nodes"] = [{"kind": nd["kind"], "ir": {k: v for k, v in nd["ir"].items() if k != "coefs"}} for nd in out["nodes"]]
    return out


def fmt_history(history: list[dict], c: Constraints, unit: str, locked: dict) -> str:
    lines = []
    for h in history:
        m = Metrics(**h["metrics"])
        v = m.violations(c)
        status = "FEASIBLE" if not v else "INFEASIBLE: " + "; ".join(v)
        shown = {k: val for k, val in h["ir"].items() if (k not in locked or k in ("stages", "nodes")) and k != "coefs"}
        if isinstance(shown.get("stages"), list):
            shown["stages"] = [{k: v for k, v in st.items() if k != "coefs"} for st in shown["stages"]]
        if isinstance(shown.get("nodes"), list):
            shown["nodes"] = [{"kind": nd["kind"], "ir": {k: v for k, v in nd["ir"].items() if k != "coefs"}} for nd in shown["nodes"]]
        lines.append(f"### iteration {h['iteration']}  ir={json.dumps(shown)}")
        seeds = f" (median of {len(m.fmax_seeds)} P&R seeds: {min(m.fmax_seeds):.1f}-{max(m.fmax_seeds):.1f})" if len(m.fmax_seeds) > 1 else ""
        pw = f" power={m.power_mw}mW" if m.power_mw is not None else ""
        res = f"cells={m.lut} ff={m.ff} area={m.area_um2:.0f}um2" if m.area_um2 is not None else f"lut={m.lut} ff={m.ff} dsp={m.dsp} bram={m.bram}"
        lines.append(f"fmax={m.fmax_mhz:.1f}MHz{seeds} {res}{pw} "
                     f"throughput={h['throughput']} {unit}  latency={h['latency_cycles']} cycles  "
                     f"sim={'pass' if m.sim_passed else 'FAIL'}  -> {status}")
        if m.critical_path:
            lines.append("critical path (ns, logic+route per stage): " + json.dumps(m.critical_path))
        if not m.sim_passed:
            lines.append("sim log tail: " + m.sim_log[-800:])
        if m.sim_stages:
            lines.append("netlist simulations: " + ", ".join(f"{k}={'pass' if v.get('passed') else 'FAIL' if v.get('passed') is False else 'skipped'}" for k, v in m.sim_stages.items()))
            for k, v in m.sim_stages.items():
                if v.get("passed") is False:
                    lines.append(f"  {k} log: {v.get('log', '')[-400:]}")
        lines.append(f"rationale: {h.get('action', 'initial design')}")
    return "\n".join(lines)


def pick_best(history: list[dict], c: Constraints) -> dict | None:
    feas = [h for h in history if not Metrics(**h["metrics"]).violations(c)]
    if not feas:
        return None

    def key(h):
        m = Metrics(**h["metrics"])
        k = []
        for obj in c.objective:
            if obj == "maximize_throughput":
                k.append(-h["throughput"])
            elif obj == "maximize_fmax":
                k.append(-m.fmax_mhz)
            elif obj == "minimize_lut":
                k.append(m.lut)
            elif obj == "minimize_dsp":
                k.append(m.dsp)
            elif obj == "minimize_latency":
                k.append(h["latency_cycles"])
            elif obj == "minimize_area":
                k.append(m.area_um2 if m.area_um2 is not None else m.lut)
            elif obj == "minimize_power":
                k.append(m.power_mw if m.power_mw is not None else 0)
        return tuple(k)

    return min(feas, key=key)


LESSON_SYSTEM = """You distill one reusable lesson from a single hardware design move, for a planner that will face
similar situations on the same device. Write ONE sentence, imperative, concrete, with the numbers: what situation,
what was changed, what happened, and the mechanism if the critical path makes it clear. Do not restate the task.
Example: "When a folded FIR on XC7S50 is timing-limited by the tap-operand mux, going non-symmetric at twice the
fold keeps the MAC count and removes the pre-adder from the path (+22 MHz, feasible at 259 MHz)."
Respond with JSON: {"lesson": "<sentence>", "confidence": 0.0-1.0}"""


def _learn(design, backend: str, c: Constraints, run_id: str, prev: dict, cur: dict, shown: list[dict], emit) -> None:
    """Turn (previous design, action, this result) into a scored lesson and reconcile the ones that were shown."""
    taken = memory.action_of(prev["ir"], cur["ir"])
    if not taken:
        return
    m0, m1 = Metrics(**prev["metrics"]), Metrics(**cur["metrics"])
    eff = memory.effect_of(m0, m1, prev["violations"], cur["violations"], c)
    touched = memory.reconcile(design.kind, backend, shown, taken, eff["outcome"])
    sit = memory.situation(design.kind, prev["ir"], prev["metrics"], prev["violations"])
    facts = {
        "device": backend, "design": design.kind, "situation": sit, "action": taken, "effect": eff,
        "before": {"fmax": m0.fmax_mhz, "lut": m0.lut, "dsp": m0.dsp, "critical_path": prev["metrics"].get("critical_path", [])[:4]},
        "after": {"fmax": m1.fmax_mhz, "lut": m1.lut, "dsp": m1.dsp, "critical_path": cur["metrics"].get("critical_path", [])[:4]},
        "planner_diagnosis": cur.get("diagnosis", ""), "planner_action": cur.get("action", ""),
    }
    try:
        r = llm.chat_json(LESSON_SYSTEM, json.dumps(facts), retries=2)
        text = str(r.get("lesson", "")).strip()
        conf = float(r.get("confidence", 0.5))
    except Exception as e:  # a lesson is a nice-to-have; never fail the run over it
        print(f"     lesson distillation failed: {e}", flush=True)
        return
    if not text:
        return
    lesson = memory.add(design.kind, backend, {
        "run_id": run_id, "iteration": cur["iteration"], "situation": sit, "action": taken, "effect": eff,
        "text": text, "confidence": conf,
        # the move's own outcome is its first piece of evidence
        "confirmations": 1 if eff["outcome"] == "helped" else 0,
        "contradictions": 1 if eff["outcome"] == "hurt" else 0,
    })
    print(f"     lesson [{lesson['id']}] ({eff['outcome']}): {text}", flush=True)
    emit({"kind": "lesson", "iteration": cur["iteration"], "lesson": {"id": lesson["id"], "text": text, "outcome": eff["outcome"],
          "action": taken, "effect": eff}, "reconciled": [{"id": t["id"], "score": memory.score(t)} for t in touched]})


def run_loop(spec: dict, max_iter: int = 8, out_dir: Path | None = None, on_event=None, control=None) -> dict:
    """control: optional object with .steer() -> list[str] (user messages since last call) and
    .should_stop() -> bool, checked between iterations."""
    design = design_for(spec)
    IR = design.ir_cls
    c = Constraints.from_dict(spec["constraints"])
    backend = spec.get("backend", "ecp5")
    system = build_system(design, backend)
    emit = on_event or (lambda ev: None)
    locked = dict(spec.get("locked", {}))  # task-level fields the planner may not change
    planner_model = spec.get("planner") or None   # planner A/B: which LLM proposes; default from the environment
    init = merge_locked(spec.get("initial_ir", {}), locked)
    ir = IR.from_dict(init)
    # task data that is part of the design identity but never a planner knob (FIR coefficients)
    if "coefs" in ir.to_dict():
        locked["coefs"] = ir.to_dict()["coefs"]
        locked.setdefault("cutoff", ir.to_dict().get("cutoff"))
    out = out_dir or (ROOT / "results" / time.strftime("%Y%m%d-%H%M%S"))
    run_id = out.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "spec.json").write_text(json.dumps(spec, indent=2))
    history: list[dict] = []
    action = "initial design from spec"
    diagnosis = ""
    seen = set()
    shown_lessons: list[dict] = []
    fallbacks: list[dict] = []  # screened candidates not yet evaluated, best first

    from .backends import BACKENDS as _REG
    n_par = max(1, min(_REG[backend].parallel, int(spec.get("parallel", 99))))
    emit_lock = threading.Lock()

    def evaluate_one(it: int, ir_x, action_x: str, diagnosis_x: str, slot: int = 0) -> dict:
        """One EDA evaluation -> history entry. Events carry `slot` so the UI can show siblings of one round."""
        def ev(e):
            with emit_lock:
                emit({**e, "slot": slot} if slot else e)
        ev({"kind": "evaluating", "iteration": it, "ir": ir_view(ir_x, locked)})
        t0 = time.time()
        m = evaluate(design, ir_x, c.fmax_mhz, ROOT / "runs", backend=backend, fresh=bool(spec.get("fresh")),
                     on_stage=lambda key, st: ev({"kind": "stage", "iteration": it, "stage": key, **{k: v for k, v in st.items() if k != "log"}}))
        entry = {
            "iteration": it, "slot": slot,
            "ir": ir_x.to_dict(),
            "metrics": asdict(m),
            "throughput": design.throughput(ir_x),
            "latency_cycles": design.latency(ir_x),
            "violations": m.violations(c),
            "diagnosis": diagnosis_x,
            "action": action_x,
            "eda_seconds": round(time.time() - t0, 1),
        }
        if m.fits and m.sim_passed:
            screen.record(design.kind, backend, ir_x.to_dict(), asdict(m))
        v = entry["violations"]
        print(f"     [{it}{'.' + str(slot) if slot else ''}] fmax={m.fmax_mhz:.1f} lut={m.lut} dsp={m.dsp} ff={m.ff} sim={'ok' if m.sim_passed else 'FAIL'} "
              f"{'FEASIBLE' if not v else 'violations: ' + '; '.join(v)}  ({entry['eda_seconds']}s)", flush=True)
        ev({"kind": "result", **{k: val for k, val in entry.items() if k not in ("metrics", "ir")},
            "ir": ir_view(ir_x, locked), "metrics": {k: val for k, val in asdict(m).items() if k != "sim_log"}})
        return entry

    def record(entry: dict) -> None:
        history.append(entry)
        tag = f"{entry['iteration']:02d}" + (f"_{entry['slot']}" if entry.get("slot") else "")
        (out / f"iter_{tag}.json").write_text(json.dumps(entry, indent=2))
        if len(history) >= 2 and not spec.get("no_memory"):
            _learn(design, backend, c, run_id, history[-2], history[-1], shown_lessons, emit)

    siblings: list[dict] = []  # extra screened candidates to evaluate alongside the chosen one this round

    if spec.get("sweep"):
        # design-space sweep: no planner; every point of the product of the listed knob values is measured (n_par at
        # a time), the summary's best is the pick under the objective; the trajectory is the whole grid
        return _sweep(spec, design, IR, c, backend, locked, init, out, run_id, evaluate_one, record, history, seen, n_par, emit, control)

    for it in range(1, max_iter + 1):
        errs = ir.validate()
        if errs:
            print(f"[{it}] LLM proposed an invalid IR ({errs}); asking again", flush=True)
            action = f"REJECTED (invalid IR: {errs}) -- propose a legal one"
            ir = IR.from_dict(history[-1]["ir"]) if history else IR.from_dict(init)
        if ir.digest(backend) in seen:
            print(f"[{it}] IR repeats an earlier design; nudging", flush=True)
            action = "REJECTED (duplicate of an earlier iteration) -- propose a different IR"
        else:
            seen.add(ir.digest(backend))
            extra = []
            for sb in siblings[: n_par - 1]:
                sb_ir = IR.from_dict(sb["ir"])
                if sb_ir.digest(backend) not in seen and not sb_ir.validate():
                    seen.add(sb_ir.digest(backend))
                    extra.append((sb_ir, sb["why"]))
            siblings = []
            print(f"[{it}] evaluating {ir_view(ir, locked)}" + (f"  (+{len(extra)} sibling candidate(s) in parallel)" if extra else ""), flush=True)
            if extra:
                with ThreadPoolExecutor(max_workers=1 + len(extra)) as pool:
                    futs = [pool.submit(evaluate_one, it, ir, action, diagnosis, 0)] + \
                           [pool.submit(evaluate_one, it, x_ir, f"sibling candidate: {why}", diagnosis, k + 1) for k, (x_ir, why) in enumerate(extra)]
                    entries = [f.result() for f in futs]
                # the chosen candidate stays the round's "main" entry; siblings are recorded after it so the
                # planner sees every measurement and pick_best considers all of them
                for e in entries:
                    record(e)
                # continue from the best of the round (feasible by objective, else fewest violations)
                round_best = pick_best(entries, c) or min(entries, key=lambda e: len(e["violations"]))
                if round_best is not entries[0]:
                    print(f"     round winner is sibling #{round_best['slot']}", flush=True)
                    ir = IR.from_dict(round_best["ir"])
                    emit({"kind": "note", "iteration": it, "message": f"sibling candidate #{round_best['slot']} won this round; continuing from it"})
            else:
                record(evaluate_one(it, ir, action, diagnosis, 0))

        if it == max_iter:
            break
        last = history[-1] if not history or not history[-1].get("slot") else max(history[-(n_par):], key=lambda e: (not e["violations"], -len(e["violations"])))
        if last["violations"] and fallbacks and not (control is not None and control.should_stop()):
            nxt = fallbacks.pop(0)
            nxt_ir = IR.from_dict(nxt["ir"])
            if not nxt_ir.validate() and nxt_ir.digest(backend) not in seen:
                diagnosis = f"candidate #{nxt['rank']} from the previous plan (the chosen one violated: {'; '.join(last['violations'])})"
                action = nxt["why"]
                print(f"     trying fallback candidate #{nxt['rank']} without re-planning", flush=True)
                emit({"kind": "plan", "iteration": it + 1, "diagnosis": diagnosis, "action": action, "candidates": [], "fallback": True,
                      "next_ir": ir_view(nxt_ir, locked), "stop": False})
                ir = nxt_ir
                continue
        fallbacks = []
        if control is not None and control.should_stop():
            print("     stopped by user.", flush=True)
            emit({"kind": "plan", "iteration": it + 1, "diagnosis": "", "action": "stopped by user", "next_ir": None, "stop": True})
            break
        steer = control.steer() if control is not None else []
        if steer:
            emit({"kind": "steer", "iteration": it + 1, "messages": steer})
        best = pick_best(history, c)
        last = history[-1]
        sit = memory.situation(design.kind, last["ir"], last["metrics"], last["violations"])
        # the text side of the query: what just happened, in the planner's own words plus the measured facts
        query = " ".join([last.get("diagnosis", "") or "", " ".join(last["violations"]), " ".join(str(seg.get("kind", "")) for seg in (last["metrics"].get("critical_path") or [])[1:5]), design.kind, backend])
        shown_lessons = [] if spec.get("no_memory") else memory.retrieve(design.kind, backend, sit, exclude_run=run_id, query=query)
        if shown_lessons:
            emit({"kind": "memory", "iteration": it + 1, "situation": sit,
                  "lessons": [{"id": l["id"], "text": l["text"], "score": memory.score(l), **({"from": f"{l['design']}/{l['backend']}"} if l.get("transfer") else {})} for l in shown_lessons]})
        user = (f"## Constraints\n{json.dumps(asdict(c))}\n"
                f"## Task-fixed parameters (never change these)\n{json.dumps({k: v for k, v in locked.items() if k != 'coefs'})}\n\n"
                f"## History\n{fmt_history(history, c, design.throughput_unit, locked)}\n\n"
                f"{memory.fmt_lessons(shown_lessons)}\n\n"
                + (("## Instructions from the engineer (override your own judgment where they conflict)\n" + "\n".join(f"- {m}" for m in steer) + "\n\n") if steer else "")
                + f"Current best feasible: {json.dumps(ir_view(IR.from_dict(best['ir']), locked)) if best else 'none'}\n"
                f"Propose iteration {it + 1}.")
        try:
            reply = llm.chat_json(system, user, model=planner_model)
        except RuntimeError as e:
            # planner unavailable: keep what we have rather than losing the run
            print(f"     planner failed: {e}", flush=True)
            emit({"kind": "plan", "iteration": it + 1, "diagnosis": f"planner unavailable: {e}", "action": "stopping with best-so-far", "next_ir": None, "stop": True})
            break
        (out / f"llm_{it:02d}.json").write_text(json.dumps({"system": system, "prompt": user, "reply": reply}, indent=2))
        diagnosis = reply.get("diagnosis", "")
        # accept both the new multi-candidate shape and the old single next_ir
        cands = reply.get("candidates") or ([{"ir": reply["next_ir"], "why": reply.get("action", "")}] if reply.get("next_ir") else [])
        cands = [c for c in cands if isinstance(c, dict) and isinstance(c.get("ir"), dict)][:3]
        if reply.get("stop") and (best or steer) and not cands:
            reason = reply.get("stop_reason") or reply.get("action") or "stop"
            print(f"     diagnosis: {diagnosis}\n     planner says stop: {reason}", flush=True)
            emit({"kind": "plan", "iteration": it + 1, "diagnosis": diagnosis, "action": reason, "stop_reason": reason, "candidates": [], "stop": True})
            break
        if not cands:
            cands = [{"ir": ir.to_dict(), "why": "planner returned no candidate; retrying from the last design"}]
        # normalize: merge with current IR + locked, drop illegal ones, screen the rest
        norm = []
        for cnd in cands:
            merged = merge_locked({**ir.to_dict(), **cnd["ir"]}, locked)
            cand_ir = IR.from_dict(merged)
            errs = cand_ir.validate()
            dup = cand_ir.digest(backend) in seen
            norm.append({"ir": cand_ir.to_dict(), "why": str(cnd.get("why", "")), "invalid": errs, "duplicate": dup})
        legal = [n for n in norm if not n["invalid"] and not n["duplicate"]]
        # the surrogate (GP on the residual of the closed form over this design/device's measured points) fills the
        # round with local moves around the incumbent when the planner brought fewer than three legal candidates
        if best and len(legal) < 3 and not spec.get("no_surrogate"):
            try:
                from . import surrogate
                from .schema import SCHEMAS
                have = {json.dumps(n["ir"], sort_keys=True) for n in legal}
                for x in surrogate.explore(design.kind, backend, best["ir"], locked, c, best, IR, seen, SCHEMAS.get(design.kind, {}), n=3 - len(legal)):
                    if json.dumps(x["ir"], sort_keys=True) not in have:
                        legal.append({"ir": x["ir"], "why": x["why"], "invalid": [], "duplicate": False, "surrogate": True})
            except Exception as e:
                print(f"     surrogate exploration skipped: {e}", flush=True)
        ranked = screen.screen(design.kind, backend, legal, c) if legal else []
        if ranked and not spec.get("no_surrogate"):
            # re-order by constrained expected improvement when the surrogate has data; the point estimate breaks ties
            try:
                from . import surrogate
                scored = []
                for r in ranked:
                    acq = surrogate.acquisition(design.kind, backend, r["ir"], c, best, throughput=design.throughput(IR.from_dict(r["ir"])))
                    r["acq"] = {k: acq[k] for k in ("score", "p_feasible", "pred")} if acq else None
                    scored.append((-(acq["score"] if acq else 0.0), r["rank"], r))
                if any(r["acq"] for r in ranked):
                    # estimated-feasible candidates stay ahead of estimated-infeasible ones (the closed form is exact on
                    # resources); within a group the acquisition decides
                    scored.sort(key=lambda t: (bool(t[2]["est_violations"]), t[0], t[1]))
                    ranked = [t[2] for t in scored]
                    for i, r in enumerate(ranked):
                        r["rank"] = i + 1
            except Exception as e:
                print(f"     surrogate ranking skipped: {e}", flush=True)
        chosen = ranked[0] if ranked else None
        action = f"{chosen['why']}" if chosen else "no legal candidate"
        print(f"     diagnosis: {diagnosis}", flush=True)
        for r in ranked:
            e = r["estimate"]
            acq = r.get("acq")
            band = f"  surrogate fmax~{acq['pred']['fmax_mhz'][0]:.0f} [{acq['pred']['fmax_mhz'][1]:.0f}-{acq['pred']['fmax_mhz'][2]:.0f}] p_feasible {acq['p_feasible']:.2f}" if acq else ""
            print(f"     candidate #{r['rank']}: {ir_view(IR.from_dict(r['ir']), locked)}  est fmax~{e['fmax_mhz']:.0f} lut~{e['lut']} dsp~{e['dsp']}  {r['est_violations'] or 'est-feasible'}{band}  -- {r['why']}", flush=True)
        for n in norm:
            if n["invalid"] or n["duplicate"]:
                print(f"     rejected: {ir_view(IR.from_dict(n['ir']), locked)}  {'invalid: ' + '; '.join(n['invalid']) if n['invalid'] else 'duplicate'}", flush=True)
        emit({"kind": "plan", "iteration": it + 1, "diagnosis": diagnosis, "action": action,
              "candidates": [{"ir": ir_view(IR.from_dict(r["ir"]), locked), "why": r["why"], "rank": r["rank"], "estimate": r["estimate"], "est_violations": r["est_violations"],
                              **({"surrogate": r["acq"]} if r.get("acq") else {}), **({"local": True} if r.get("surrogate") else {})} for r in ranked]
                            + [{"ir": ir_view(IR.from_dict(n["ir"]), locked), "why": n["why"], "rejected": "invalid: " + "; ".join(n["invalid"]) if n["invalid"] else "duplicate"} for n in norm if n["invalid"] or n["duplicate"]],
              "next_ir": ir_view(IR.from_dict(chosen["ir"]), locked) if chosen else None, "stop": False})
        if not chosen:
            action = "REJECTED (all candidates invalid or duplicates) -- propose different IRs"
            continue
        if chosen["est_violations"] and not reply.get("_rescreened"):
            # every candidate looks infeasible by estimate: one re-plan with the screening verdicts attached
            verdicts = "\n".join(f"- {json.dumps(ir_view(IR.from_dict(r['ir']), locked))}: est fmax~{r['estimate']['fmax_mhz']:.0f} MHz, lut~{r['estimate']['lut']}, dsp~{r['estimate']['dsp']} -> {', '.join(r['est_violations'])}" for r in ranked)
            print("     all candidates estimated infeasible; asking the planner once more with the estimates", flush=True)
            emit({"kind": "rescreen", "iteration": it + 1, "verdicts": [{"ir": ir_view(IR.from_dict(r["ir"]), locked), "est_violations": r["est_violations"]} for r in ranked]})
            try:
                reply2 = llm.chat_json(system, user + "\n\n## Screening verdicts on your candidates (closed-form estimates, not EDA)\n" + verdicts +
                                       "\nEvery candidate is estimated to violate a constraint. Propose up to three different candidates that the estimates would accept, or keep one if you believe the estimate is wrong and say why.",
                                       model=planner_model)
                reply2["_rescreened"] = True
                cands2 = [c2 for c2 in (reply2.get("candidates") or []) if isinstance(c2, dict) and isinstance(c2.get("ir"), dict)][:3]
                if cands2:
                    norm2 = []
                    for cnd in cands2:
                        cand_ir = IR.from_dict(merge_locked({**ir.to_dict(), **cnd["ir"]}, locked))
                        if not cand_ir.validate() and cand_ir.digest(backend) not in seen:
                            norm2.append({"ir": cand_ir.to_dict(), "why": str(cnd.get("why", ""))})
                    ranked2 = screen.screen(design.kind, backend, norm2, c) if norm2 else []
                    if ranked2 and (not ranked2[0]["est_violations"] or not chosen["est_violations"]):
                        ranked, chosen = ranked2, ranked2[0]
                        diagnosis = reply2.get("diagnosis", diagnosis)
                        action = chosen["why"]
                        for r in ranked:
                            e = r["estimate"]
                            print(f"     re-candidate #{r['rank']}: {ir_view(IR.from_dict(r['ir']), locked)}  est fmax~{e['fmax_mhz']:.0f} lut~{e['lut']} dsp~{e['dsp']}  {r['est_violations'] or 'est-feasible'}  -- {r['why']}", flush=True)
                        emit({"kind": "plan", "iteration": it + 1, "diagnosis": diagnosis, "action": action, "rescreened": True,
                              "candidates": [{"ir": ir_view(IR.from_dict(r["ir"]), locked), "why": r["why"], "rank": r["rank"], "estimate": r["estimate"], "est_violations": r["est_violations"]} for r in ranked],
                              "next_ir": ir_view(IR.from_dict(chosen["ir"]), locked), "stop": False})
            except RuntimeError as e:
                print(f"     re-plan failed: {e}; proceeding with the best estimated candidate", flush=True)
        ir = IR.from_dict(chosen["ir"])
        runners = [r for r in ranked[1:] if not r["est_violations"]]
        if n_par > 1:
            siblings, fallbacks = runners, []   # measured now, in parallel, instead of maybe later
        else:
            fallbacks = runners

    best = pick_best(history, c)
    summary = {"run_id": run_id, "spec": spec, "design": design.kind, "backend": backend,
               "throughput_unit": design.throughput_unit, "iterations": len(history), "best": best, "history": history}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    emit({"kind": "done", "best": best, "iterations": len(history), "throughput_unit": design.throughput_unit})
    return summary


def _sweep(spec, design, IR, c, backend, locked, init, out, run_id, evaluate_one, record, history, seen, n_par, emit, control) -> dict:
    import itertools
    sweep = {k: list(v) for k, v in spec["sweep"].items() if isinstance(v, (list, tuple)) and v}
    keys = list(sweep)
    grid = [dict(zip(keys, vals)) for vals in itertools.product(*(sweep[k] for k in keys))]
    points = []
    for g in grid:
        try:
            a = IR.from_dict(merge_locked({**init, **g}, locked))
        except Exception as e:
            emit({"kind": "note", "iteration": 0, "message": f"sweep point {g} rejected: {e}"})
            continue
        errs = a.validate()
        if errs:
            emit({"kind": "note", "iteration": 0, "message": f"sweep point {g} invalid: {'; '.join(errs)}"})
            continue
        if a.digest(backend) in seen:
            continue
        seen.add(a.digest(backend))
        points.append((g, a))
    emit({"kind": "plan", "iteration": 1, "diagnosis": f"design-space sweep over {keys}: {len(points)} points", "action": "measure every point", "candidates": [], "stop": False})
    it = 0
    for i in range(0, len(points), n_par):
        if control is not None and control.should_stop():
            break
        batch = points[i:i + n_par]
        it += 1
        if len(batch) == 1:
            record(evaluate_one(it, batch[0][1], f"sweep {batch[0][0]}", "", 0))
        else:
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                futs = [pool.submit(evaluate_one, it, a, f"sweep {g}", "", k) for k, (g, a) in enumerate(batch)]
                for e in [f.result() for f in futs]:
                    record(e)
    best = pick_best(history, c)
    # the sweep table: every point with its knob values, for the UI's Pareto view
    table = [{"point": dict(zip(keys, [e["ir"].get(k) for k in keys])), "iteration": e["iteration"], "slot": e.get("slot", 0),
              "fmax_mhz": e["metrics"]["fmax_mhz"], "lut": e["metrics"]["lut"], "dsp": e["metrics"]["dsp"], "area_um2": e["metrics"].get("area_um2"),
              "throughput": e["throughput"], "feasible": not e["violations"]} for e in history]
    summary = {"run_id": run_id, "spec": spec, "design": design.kind, "backend": backend, "throughput_unit": design.throughput_unit,
               "iterations": len(history), "best": best, "history": history, "sweep": {"knobs": keys, "points": table}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    emit({"kind": "done", "best": best, "iterations": len(history), "throughput_unit": design.throughput_unit, "sweep": {"knobs": keys, "n": len(table)}})
    return summary


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m silicon_agent.loop <spec.json> [max_iter] [--out DIR --events FILE --control FILE]")
        return 2
    spec = json.loads(Path(argv[1]).read_text())
    max_iter = int(argv[2]) if len(argv) > 2 and not argv[2].startswith("--") else 8
    opts = {argv[i]: argv[i + 1] for i in range(2, len(argv) - 1) if argv[i].startswith("--")}
    if "--events" in opts:
        # driven by the job server (a proposal's A/B run on a patched generator tree): events stream to a file as
        # JSON lines, steer/stop arrive through a control file the server rewrites
        evf = open(opts["--events"], "a")
        ctl = Path(opts["--control"]) if "--control" in opts else None

        def on_event(ev):
            evf.write(json.dumps(ev) + "\n"); evf.flush()

        class Control:
            def _read(self):
                try:
                    return json.loads(ctl.read_text()) if ctl and ctl.exists() else {}
                except ValueError:
                    return {}
            def steer(self):
                d = self._read(); msgs = d.get("steer") or []
                if msgs and ctl:
                    d["steer"] = []; ctl.write_text(json.dumps(d))
                return msgs
            def should_stop(self):
                return bool(self._read().get("stop"))
        s = run_loop(spec, max_iter=max_iter, out_dir=Path(opts["--out"]) if "--out" in opts else None, on_event=on_event, control=Control())
        return 0 if s["best"] else 1
    s = run_loop(spec, max_iter=max_iter)
    print("\n=== RESULT ===")
    if s["best"]:
        b = s["best"]
        m = b["metrics"]
        print(f"PASS  ir={ {k: v for k, v in b['ir'].items() if k != 'coefs'} }\nfmax={m['fmax_mhz']:.1f} MHz  lut={m['lut']}  ff={m['ff']}  dsp={m['dsp']}  "
              f"throughput={b['throughput']} {s['throughput_unit']}  latency={b['latency_cycles']} cycles")
    else:
        print("FAIL: no feasible design found")
    print(f"results: results/{s['run_id']}/")
    return 0 if s["best"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
