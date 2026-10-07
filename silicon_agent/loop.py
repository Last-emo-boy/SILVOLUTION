"""The autonomous design loop: spec -> IR -> RTL -> sim -> synth/P&R -> diagnose -> next IR.

Generic over the design registry (silicon_agent.designs) and the backend table
(silicon_agent.eda.BACKENDS). Design-specific planner knowledge lives in prompts.py.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import llm
from .designs import Design, design_for
from .eda import BACKENDS, ROOT, evaluate
from .ir import Constraints, Metrics
from .prompts import PROMPTS

SYSTEM = """You are a senior FPGA architect running an autonomous design-space exploration loop.
The hardware is @WHAT@ on @DEVICE_NAME@.
You never write Verilog. You edit an architecture IR; a verified generator emits the RTL. The EDA tools are ground truth.

@IR_SCHEMA@

@DEVICE_FACTS@

Decision rule: a design is FEASIBLE only if every constraint holds. Among feasible designs, prefer by the objective list in order.
Each iteration you must (1) diagnose the last result from its critical path and resource numbers, (2) propose exactly one next IR.
Be decisive: change the smallest number of fields that your diagnosis justifies. Never repeat an IR already in the history.
When the current best is feasible and you believe no further improvement is likely, set "stop": true.

Respond with a JSON object:
{
  "diagnosis": "<2-4 sentences: what limits the last design, citing the critical path>",
  "action": "<one sentence naming the IR change and why>",
  "next_ir": @IR_JSON@,
  "stop": false
}"""


def build_system(design: Design, backend: str) -> str:
    p = PROMPTS[design.kind]
    return (SYSTEM.replace("@WHAT@", p["what"]).replace("@DEVICE_NAME@", BACKENDS[backend]["name"])
            .replace("@IR_SCHEMA@", p["ir"]).replace("@DEVICE_FACTS@", p["facts"][backend]).replace("@IR_JSON@", p["ir_json"]))


def ir_view(ir, locked: dict) -> dict:
    """IR as shown to the planner: locked fields and bulky task data (coefficients) omitted."""
    return {k: v for k, v in ir.to_dict().items() if k not in locked and k != "coefs"}


def fmt_history(history: list[dict], c: Constraints, unit: str, locked: dict) -> str:
    lines = []
    for h in history:
        m = Metrics(**h["metrics"])
        v = m.violations(c)
        status = "FEASIBLE" if not v else "INFEASIBLE: " + "; ".join(v)
        shown = {k: val for k, val in h["ir"].items() if k not in locked and k != "coefs"}
        lines.append(f"### iteration {h['iteration']}  ir={json.dumps(shown)}")
        lines.append(f"fmax={m.fmax_mhz:.1f}MHz lut={m.lut} ff={m.ff} dsp={m.dsp} bram={m.bram} "
                     f"throughput={h['throughput']} {unit}  latency={h['latency_cycles']} cycles  "
                     f"sim={'pass' if m.sim_passed else 'FAIL'}  -> {status}")
        if m.critical_path:
            lines.append("critical path (ns, logic+route per stage): " + json.dumps(m.critical_path))
        if not m.sim_passed:
            lines.append("sim log tail: " + m.sim_log[-800:])
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
        return tuple(k)

    return min(feas, key=key)


def run_loop(spec: dict, max_iter: int = 8, out_dir: Path | None = None, on_event=None) -> dict:
    design = design_for(spec)
    IR = design.ir_cls
    c = Constraints.from_dict(spec["constraints"])
    backend = spec.get("backend", "ecp5")
    system = build_system(design, backend)
    emit = on_event or (lambda ev: None)
    locked = dict(spec.get("locked", {}))  # task-level fields the planner may not change
    init = {**spec.get("initial_ir", {}), **locked}
    ir = IR.from_dict(init)
    # task data that is part of the design identity but never a planner knob (FIR coefficients)
    if "coefs" in ir.to_dict():
        locked["coefs"] = ir.to_dict()["coefs"]
    out = out_dir or (ROOT / "results" / time.strftime("%Y%m%d-%H%M%S"))
    run_id = out.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "spec.json").write_text(json.dumps(spec, indent=2))
    history: list[dict] = []
    action = "initial design from spec"
    diagnosis = ""
    seen = set()

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
            print(f"[{it}] evaluating {ir_view(ir, locked)}", flush=True)
            emit({"kind": "evaluating", "iteration": it, "ir": ir_view(ir, locked)})
            t0 = time.time()
            m = evaluate(design, ir, c.fmax_mhz, ROOT / "runs", backend=backend)
            entry = {
                "iteration": it,
                "ir": ir.to_dict(),
                "metrics": asdict(m),
                "throughput": design.throughput(ir),
                "latency_cycles": design.latency(ir),
                "violations": m.violations(c),
                "diagnosis": diagnosis,
                "action": action,
                "eda_seconds": round(time.time() - t0, 1),
            }
            history.append(entry)
            (out / f"iter_{it:02d}.json").write_text(json.dumps(entry, indent=2))
            v = entry["violations"]
            print(f"     fmax={m.fmax_mhz:.1f} lut={m.lut} dsp={m.dsp} ff={m.ff} sim={'ok' if m.sim_passed else 'FAIL'} "
                  f"{'FEASIBLE' if not v else 'violations: ' + '; '.join(v)}  ({entry['eda_seconds']}s)", flush=True)
            emit({"kind": "result", **{k: val for k, val in entry.items() if k not in ("metrics", "ir")},
                  "ir": ir_view(ir, locked), "metrics": {k: val for k, val in asdict(m).items() if k != "sim_log"}})

        if it == max_iter:
            break
        best = pick_best(history, c)
        user = (f"## Constraints\n{json.dumps(asdict(c))}\n"
                f"## Task-fixed parameters (never change these)\n{json.dumps({k: v for k, v in locked.items() if k != 'coefs'})}\n\n"
                f"## History\n{fmt_history(history, c, design.throughput_unit, locked)}\n\n"
                f"Current best feasible: {json.dumps(ir_view(IR.from_dict(best['ir']), locked)) if best else 'none'}\n"
                f"Propose iteration {it + 1}.")
        try:
            reply = llm.chat_json(system, user)
        except RuntimeError as e:
            # planner unavailable: keep what we have rather than losing the run
            print(f"     planner failed: {e}", flush=True)
            emit({"kind": "plan", "iteration": it + 1, "diagnosis": f"planner unavailable: {e}", "action": "stopping with best-so-far", "next_ir": None, "stop": True})
            break
        (out / f"llm_{it:02d}.json").write_text(json.dumps({"system": system, "prompt": user, "reply": reply}, indent=2))
        diagnosis = reply.get("diagnosis", "")
        action = reply.get("action", "")
        print(f"     diagnosis: {diagnosis}\n     action: {action}", flush=True)
        emit({"kind": "plan", "iteration": it + 1, "diagnosis": diagnosis, "action": action,
              "next_ir": reply.get("next_ir"), "stop": bool(reply.get("stop"))})
        if reply.get("stop") and best:
            print("     planner says stop.", flush=True)
            break
        proposed = reply.get("next_ir") or {}
        changed = {k: v for k, v in locked.items() if k in proposed and proposed[k] != v and k != "coefs"}
        if changed:
            print(f"     planner tried to change locked fields {changed}; reverting", flush=True)
        ir = IR.from_dict({**ir.to_dict(), **proposed, **locked})

    best = pick_best(history, c)
    summary = {"run_id": run_id, "spec": spec, "design": design.kind, "backend": backend,
               "throughput_unit": design.throughput_unit, "iterations": len(history), "best": best, "history": history}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    emit({"kind": "done", "best": best, "iterations": len(history), "throughput_unit": design.throughput_unit})
    return summary


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m silicon_agent.loop <spec.json> [max_iter]")
        return 2
    spec = json.loads(Path(argv[1]).read_text())
    max_iter = int(argv[2]) if len(argv) > 2 else 8
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
