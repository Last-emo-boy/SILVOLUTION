"""The autonomous design loop: spec -> IR -> RTL -> sim -> synth/P&R -> diagnose -> next IR."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import llm
from .eda import ROOT, evaluate
from .emit import drain_latency
from .ir import Constraints, MatmulArch, Metrics, throughput_macs_per_cycle

SYSTEM = """You are a senior FPGA architect running an autonomous design-space exploration loop.
The hardware is an output-stationary systolic INT matrix-multiply accelerator on a Lattice ECP5 (LFE5U-45F, speed grade 6).
You never write Verilog. You edit an architecture IR; a verified generator emits the RTL. The EDA tools are ground truth.

IR fields and legal ranges:
  pe_rows    int 1..32   rows of processing elements (throughput = pe_rows*pe_cols MACs/cycle)
  pe_cols    int 1..32
  data_width int in {4,8,12,16}  operand width (bits)
  acc_width  int 2*data_width+8 .. 48   accumulator width
  pipe_mul   int 0..3    fabric register stages between the multiplier output and the accumulator
  reg_in     bool        register the A/B inputs at the array boundary
  dsp_regs   str in {"none","out","in_out","in_pipe_out"}  which MULT18X18D internal registers to enable
                         (none = combinational DSP; each extra stage adds 1 cycle latency, no fabric cost)

ECP5 facts: each PE uses one MULT18X18D (DSP) when data_width<=16; the accumulator is a LUT4 carry chain whose
delay grows linearly with acc_width (about 0.07 ns per bit plus routing); routing between the DSP output and the
carry chain is the usual timing limiter unless both dsp_regs and pipe_mul isolate it; reg_in breaks the IO->array
path; the drain mux that selects one of pe_rows accumulator rows onto c_row grows with pe_rows and can become critical
at high Fmax. LUT4 count scales ~ pe_rows*pe_cols*acc_width. The 45F has 44k LUT4, 72 MULT18X18D, 108 DP16KD.

Decision rule: a design is FEASIBLE only if every constraint holds. Among feasible designs, prefer by the objective list in order.
Each iteration you must (1) diagnose the last result from its critical path and resource numbers, (2) propose exactly one next IR.
Be decisive: change the smallest number of fields that your diagnosis justifies. Never repeat an IR already in the history.
When the current best is feasible and you believe no further improvement is likely, set "stop": true.

Respond with a JSON object:
{
  "diagnosis": "<2-4 sentences: what limits the last design, citing the critical path>",
  "action": "<one sentence naming the IR change and why>",
  "next_ir": {"pe_rows":..,"pe_cols":..,"data_width":..,"acc_width":..,"pipe_mul":..,"reg_in":..,"dsp_regs":".."},
  "stop": false
}"""


def fmt_history(history: list[dict], c: Constraints) -> str:
    lines = []
    for h in history:
        m = Metrics(**h["metrics"])
        v = m.violations(c)
        status = "FEASIBLE" if not v else "INFEASIBLE: " + "; ".join(v)
        lines.append(f"### iteration {h['iteration']}  ir={json.dumps(h['ir'])}")
        lines.append(f"fmax={m.fmax_mhz:.1f}MHz lut={m.lut} ff={m.ff} dsp={m.dsp} bram={m.bram} "
                     f"throughput={h['throughput']} MAC/cycle  sim={'pass' if m.sim_passed else 'FAIL'}  -> {status}")
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
        return tuple(k)

    return min(feas, key=key)


def run_loop(spec: dict, max_iter: int = 8, out_dir: Path | None = None) -> dict:
    c = Constraints.from_dict(spec["constraints"])
    locked = spec.get("locked", {})  # task-level fields the planner may not change, e.g. {"data_width": 8}
    ir = MatmulArch.from_dict({**spec.get("initial_ir", {}), **locked})
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out = out_dir or (ROOT / "results" / run_id)
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
            ir = MatmulArch.from_dict(history[-1]["ir"]) if history else MatmulArch()
        if ir.digest() in seen:
            print(f"[{it}] IR repeats an earlier design; nudging", flush=True)
            action = "REJECTED (duplicate of an earlier iteration) -- propose a different IR"
        else:
            seen.add(ir.digest())
            print(f"[{it}] evaluating {ir.to_dict()}", flush=True)
            t0 = time.time()
            m = evaluate(ir, c.fmax_mhz, ROOT / "runs")
            entry = {
                "iteration": it,
                "ir": ir.to_dict(),
                "metrics": asdict(m),
                "throughput": throughput_macs_per_cycle(ir),
                "latency_cycles": drain_latency(ir) + ir.pe_rows,
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

        if it == max_iter:
            break
        user = (f"## Constraints\n{json.dumps(asdict(c))}\n"
                f"## Locked fields (part of the task, never change these)\n{json.dumps(locked)}\n\n"
                f"## History\n{fmt_history(history, c)}\n\n"
                f"Current best feasible: {json.dumps(pick_best(history, c)['ir']) if pick_best(history, c) else 'none'}\n"
                f"Propose iteration {it + 1}.")
        reply = llm.chat_json(SYSTEM, user)
        (out / f"llm_{it:02d}.json").write_text(json.dumps({"prompt": user, "reply": reply}, indent=2))
        diagnosis = reply.get("diagnosis", "")
        action = reply.get("action", "")
        print(f"     diagnosis: {diagnosis}\n     action: {action}", flush=True)
        if reply.get("stop") and pick_best(history, c):
            print("     planner says stop.", flush=True)
            break
        proposed = reply["next_ir"]
        changed = {k: v for k, v in locked.items() if k in proposed and proposed[k] != v}
        if changed:
            print(f"     planner tried to change locked fields {changed}; reverting", flush=True)
        ir = MatmulArch.from_dict({**proposed, **locked})

    best = pick_best(history, c)
    summary = {"run_id": run_id, "spec": spec, "iterations": len(history), "best": best, "history": history}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
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
        print(f"PASS  ir={b['ir']}\nfmax={m['fmax_mhz']:.1f} MHz  lut={m['lut']}  ff={m['ff']}  dsp={m['dsp']}  "
              f"throughput={b['throughput']} MAC/cycle  latency={b['latency_cycles']} cycles")
    else:
        print("FAIL: no feasible design found")
    print(f"results: results/{s['run_id']}/")
    return 0 if s["best"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
