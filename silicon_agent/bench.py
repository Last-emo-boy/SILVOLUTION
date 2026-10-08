"""Benchmark set + regression: a fixed list of specs the agent must keep solving.

    python -m silicon_agent.bench run   [--only ecp5,ice40] [--max-iter 6] [--tag nightly]   run every case, record a report
    python -m silicon_agent.bench show  [--last 5]                                            compare the last reports
    python -m silicon_agent.bench cases                                                       list the cases

Each case is (name, spec, expectation). A run records per case: feasible?, rounds to first feasible, best
metrics, EDA seconds, planner calls, and whether the expectation held. Reports go to SA_ROOT/bench/<tag>-<ts>.json;
`show` prints a table of the last N so regressions in rounds / Fmax / cost are visible at a glance.
Cases are submitted to the job server as ordinary jobs (task prefixed "[<tag>]"), so they queue behind
interactive work, show up in the worktable, and use the memory and EDA cache the server has -- a nightly run
measures what the memory buys; pass --no-memory for a cold baseline. From the laptop, through the tunnel:

    SA_TOKEN=... python -m silicon_agent.bench run --tag nightly --only ecp5,ice40,gowin --out bench/
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import os

BENCH_DIR = Path(os.environ.get("SA_ROOT", Path(__file__).resolve().parent.parent)) / "bench"

# (name, spec, expect) -- expect: {"feasible": bool, "max_rounds": int} is the bar; a run that needs more rounds
# or fails to find a feasible design where one is expected counts as a regression.
CASES: list[tuple[str, dict, dict]] = [
    ("matmul-ecp5-180", {
        "task": "INT8 matmul, ECP5, 180 MHz, 48 DSP", "design": "matmul", "backend": "ecp5", "locked": {"data_width": 8},
        "constraints": {"fmax_mhz": 180, "max_dsp": 48, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"pe_rows": 4, "pe_cols": 4, "acc_width": 32, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 5}),
    ("matmul-ecp5-32dsp-lean", {
        "task": "INT8 matmul, ECP5, 160 MHz, 32 DSP, lean", "design": "matmul", "backend": "ecp5", "locked": {"data_width": 8},
        "constraints": {"fmax_mhz": 160, "max_dsp": 32, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"pe_rows": 4, "pe_cols": 8, "acc_width": 32, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 4}),
    ("fir-ecp5-120-6dsp", {
        "task": "32-tap FIR, ECP5, 120 MHz, 6 DSP", "design": "fir", "backend": "ecp5", "locked": {"taps": 32, "data_width": 16, "coef_width": 16, "cutoff": 0.2},
        "constraints": {"fmax_mhz": 120, "max_dsp": 6, "objective": ["maximize_throughput", "minimize_dsp"]},
        "initial_ir": {"acc_width": 40, "symmetric": False, "fold": 1, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 5}),
    ("conv3-ecp5-100", {
        "task": "3x3 conv, ECP5, width 640, 100 MHz", "design": "conv2d", "backend": "ecp5", "locked": {"k": 3, "width": 640, "data_width": 8, "coef_width": 8},
        "constraints": {"fmax_mhz": 100, "max_lut": 1500, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"acc_width": 24, "line_mem": "bram", "par_rows": 3, "tree_regs": 0, "pipe_mul": 0, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 3}),
    ("conv3-ice40-60", {
        "task": "3x3 conv, iCE40 UP5K, width 320, 60 MHz", "design": "conv2d", "backend": "ice40", "locked": {"k": 3, "width": 320, "data_width": 8, "coef_width": 8},
        "constraints": {"fmax_mhz": 60, "max_dsp": 8, "max_lut": 1500, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"acc_width": 24, "line_mem": "bram", "par_rows": 3, "tree_regs": 0, "pipe_mul": 0, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 4}),
    ("fir-gowin-100-4dsp", {
        "task": "16-tap FIR, Gowin, 100 MHz, 4 DSP", "design": "fir", "backend": "gowin", "locked": {"taps": 16, "data_width": 12, "coef_width": 12, "cutoff": 0.1},
        "constraints": {"fmax_mhz": 100, "max_dsp": 4, "objective": ["maximize_throughput", "minimize_dsp"]},
        "initial_ir": {"acc_width": 32, "symmetric": False, "fold": 1, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 5}),
    ("matmul-gowin-120", {
        "task": "INT8 matmul, Gowin, 120 MHz, 16 DSP", "design": "matmul", "backend": "gowin", "locked": {"data_width": 8},
        "constraints": {"fmax_mhz": 120, "max_dsp": 16, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"pe_rows": 2, "pe_cols": 4, "acc_width": 24, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 5}),
    ("matmul-ice40-40", {
        "task": "INT8 matmul, iCE40 UP5K, 40 MHz, 8 DSP", "design": "matmul", "backend": "ice40", "locked": {"data_width": 8},
        "constraints": {"fmax_mhz": 40, "max_dsp": 8, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"pe_rows": 2, "pe_cols": 2, "acc_width": 24, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 5}),
    ("fir-ice40-55-4dsp", {
        # the post-accumulator reduction is the limiter on iCE40; only sum_tree moves it (49.5 -> 55 MHz measured)
        "task": "16-tap 12-bit FIR, iCE40, 55 MHz, 4 DSP", "design": "fir", "backend": "ice40", "locked": {"taps": 16, "data_width": 12, "coef_width": 12, "cutoff": 0.25},
        "constraints": {"fmax_mhz": 52, "max_dsp": 4, "objective": ["maximize_throughput", "maximize_fmax"]},
        "initial_ir": {"acc_width": 29, "symmetric": True, "fold": 2, "pipe_mul": 2, "reg_in": True, "dsp_regs": "out", "sum_tree": False}},
     {"feasible": True, "max_rounds": 4}),
    ("fir-ecp5-200-4dsp", {
        # input-side limiter (pre-adder + tap mux into the DSP): pipe_in + sum_tree take it from 151.8 to 219 MHz measured
        "task": "16-tap 12-bit symmetric FIR, ECP5, 200 MHz, 4 DSP", "design": "fir", "backend": "ecp5", "locked": {"taps": 16, "data_width": 12, "coef_width": 12, "cutoff": 0.3},
        "constraints": {"fmax_mhz": 200, "max_dsp": 4, "objective": ["maximize_throughput", "maximize_fmax"]},
        "initial_ir": {"acc_width": 29, "symmetric": True, "fold": 2, "pipe_mul": 0, "reg_in": True, "dsp_regs": "in_pipe_out", "sum_tree": False, "pipe_in": False}},
     {"feasible": True, "max_rounds": 4}),
    ("fir-sky130-200", {
        # the ASIC backend: OpenROAD sky130hd, one flow ~2 min; first measured point 205 MHz / 6143 cells / 55.6k um2
        "task": "16-tap 12-bit FIR, sky130 ASIC, 200 MHz", "design": "fir", "backend": "sky130", "locked": {"taps": 16, "data_width": 12, "coef_width": 12, "cutoff": 0.25},
        "constraints": {"fmax_mhz": 200, "objective": ["maximize_throughput", "minimize_area"]},
        "initial_ir": {"acc_width": 29, "symmetric": True, "fold": 2, "pipe_mul": 1, "reg_in": True, "dsp_regs": "none", "sum_tree": True, "pipe_in": True}},
     {"feasible": True, "max_rounds": 3}),
    ("cordic-ecp5-200", {
        "task": "16-bit sin/cos CORDIC, ECP5, 200 MHz", "design": "cordic", "backend": "ecp5", "locked": {"mode": "rotate", "width": 16},
        "constraints": {"fmax_mhz": 200, "max_lut": 3500, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"iterations": 16, "unroll": 4, "fold": 1, "guard_bits": 2, "scale_comp": False}},
     {"feasible": True, "max_rounds": 4}),
    ("fft64-gowin-100", {
        "task": "64-point 12-bit FFT, Gowin, 100 MHz", "design": "fft", "backend": "gowin", "locked": {"n": 64, "data_width": 12},
        "constraints": {"fmax_mhz": 100, "max_dsp": 20, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"tw_width": 12, "growth": "scale", "tw_mem": "lut", "delay_mem": "auto", "bram_min": 16, "mul_regs": 0, "bf_regs": 1, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 4}),
    ("graph-ecp5-120", {
        "task": "8-tap FIR -> 12-bit CORDIC datapath, ECP5, 120 MHz, 10 DSP", "design": "graph", "backend": "ecp5",
        "locked": {"nodes": [{"kind": "fir", "ir": {"taps": 8, "data_width": 12, "coef_width": 12, "cutoff": 0.4}}, {"kind": "cordic", "ir": {"mode": "rotate", "width": 12}}]},
        "constraints": {"fmax_mhz": 120, "max_dsp": 10, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"nodes": [{"kind": "fir", "ir": {"taps": 8, "data_width": 12, "coef_width": 12, "cutoff": 0.4, "acc_width": 28, "symmetric": False, "fold": 1, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}},
                                 {"kind": "cordic", "ir": {"mode": "rotate", "width": 12, "iterations": 12, "unroll": 1, "fold": 1, "guard_bits": 2, "scale_comp": False}}],
                       "fifo_depth": 32, "adapt": "truncate"}},
     {"feasible": True, "max_rounds": 3}),
    ("fft256-gowin-100", {
        "task": "256-point 16-bit FFT, natural order, Gowin, 100 MHz, 20 MULT", "design": "fft", "backend": "gowin", "locked": {"n": 256, "data_width": 16},
        "constraints": {"fmax_mhz": 100, "max_dsp": 20, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"tw_width": 12, "growth": "scale", "tw_mem": "bram", "delay_mem": "auto", "bram_min": 32, "mul_regs": 1, "bf_regs": 1, "dsp_regs": "in_out", "bit_reverse": True, "cmul": "four"}},
     {"feasible": True, "max_rounds": 3}),
    ("chain-ecp5-120", {
        "task": "8->32 tap FIR chain, ECP5, 120 MHz, 12 DSP", "design": "fir_chain", "backend": "ecp5",
        "locked": {"stages": [{"taps": 8, "data_width": 12, "coef_width": 12, "cutoff": 0.4}, {"taps": 32, "data_width": 12, "coef_width": 12, "cutoff": 0.2}]},
        "constraints": {"fmax_mhz": 120, "max_dsp": 12, "objective": ["maximize_throughput", "minimize_dsp"]},
        "initial_ir": {"stages": [{"acc_width": 32, "symmetric": False, "fold": 1, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}, {"acc_width": 32, "symmetric": False, "fold": 1, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}], "fifo_depth": 16, "round_mode": "truncate"}},
     {"feasible": True, "max_rounds": 5}),
    # Vivado cases: slow (minutes each); included when --only xc7s50 or no filter
    ("matmul-xc7s50-300-32dsp", {
        "task": "INT8 matmul, Spartan-7, 300 MHz, 32 DSP", "design": "matmul", "backend": "xc7s50", "locked": {"data_width": 8},
        "constraints": {"fmax_mhz": 300, "max_dsp": 32, "max_lut": 4000, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"pe_rows": 8, "pe_cols": 8, "acc_width": 32, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 4}),
    ("fir-xc7s50-250-8dsp", {
        "task": "32-tap FIR, Spartan-7, 250 MHz, 8 DSP", "design": "fir", "backend": "xc7s50", "locked": {"taps": 32, "data_width": 16, "coef_width": 16, "cutoff": 0.2},
        "constraints": {"fmax_mhz": 250, "max_dsp": 8, "objective": ["maximize_throughput", "minimize_dsp"]},
        "initial_ir": {"acc_width": 40, "symmetric": False, "fold": 1, "pipe_mul": 0, "reg_in": False, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 8}),
    ("conv5-xc7s50-200", {
        "task": "5x5 conv, Spartan-7, width 640, 200 MHz", "design": "conv2d", "backend": "xc7s50", "locked": {"k": 5, "width": 640, "data_width": 8, "coef_width": 8},
        "constraints": {"fmax_mhz": 200, "max_dsp": 30, "objective": ["maximize_throughput", "minimize_lut"]},
        "initial_ir": {"acc_width": 28, "line_mem": "bram", "par_rows": 5, "tree_regs": 0, "pipe_mul": 0, "dsp_regs": "none"}},
     {"feasible": True, "max_rounds": 4}),
]


def _req(server: str, token: str, method: str, path: str, body=None, retries: int = 30):
    """Tolerates server restarts (deploys) and tunnel hiccups: GETs are retried for a few minutes."""
    import urllib.error, urllib.request
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries if method == "GET" else 1):
        try:
            r = urllib.request.Request(f"{server}/api{path}", data=data, method=method,
                                       headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(r, timeout=120) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            if attempt == retries - 1 or method != "GET":
                raise
            time.sleep(10)


def run_agent_case(server: str, token: str, name: str, spec: dict, tag: str) -> tuple[str | None, dict]:
    """End-to-end through the chat (the Agnes Harness agent): the case's task sentence is the request; the agent
    must interpret it, start the run and report. Returns the job it started and the agent's reply."""
    text = f"{spec['task']}. Run it and report what you measured."
    r = _req(server, token, "POST", "/threads/new/chat", {"text": text})
    tid = r["thread"]
    t0 = time.time()
    while time.time() - t0 < 5400:
        th = _req(server, token, "GET", f"/threads/{tid}")
        if not th.get("busy"):
            break
        time.sleep(10)
    msgs = th.get("messages", [])
    runs = [m["job"] for m in msgs if m.get("kind") == "run" and m.get("job")]
    reply = next((m for m in reversed(msgs) if m.get("role") == "assistant" and m.get("kind") == "text"), {})
    u = reply.get("usage") or {}
    # the harness ledger for the turn: what the agent itself cost on top of the EDA time
    ledger = {"model_calls": u.get("calls"), "tokens": (u.get("input") or 0) + (u.get("cache_read") or 0) + (u.get("output") or 0),
              "usd": round((u.get("usd_micros") or 0) / 1e6, 4), "model": u.get("model")} if u else None
    return (runs[-1] if runs else None), {"thread": tid, "reply": (reply.get("text") or "")[:600], "tools": [t.get("name") for t in reply.get("tools", [])],
                                           "turn_seconds": round(time.time() - t0, 1), "ledger": ledger,
                                           "failed_tools": [t.get("name") for t in reply.get("tools", []) if t.get("status") == "failed"]}


def run_case(server: str, token: str, name: str, spec: dict, expect: dict, max_iter: int, no_memory: bool, tag: str, reuse: str | None = None,
             planner: str | None = None, agent: bool = False) -> dict:
    """Submit through the job server (so it serializes with everything else and shows in the UI), wait, score.
    With `agent`, the case goes through the chat instead: the harness agent interprets the task sentence itself."""
    spec = json.loads(json.dumps(spec))
    spec["task"] = f"[{tag}] {spec['task']}"
    if no_memory:
        spec["no_memory"] = True
    if planner:
        spec["planner"] = planner
    t0 = time.time()
    agent_info = None
    if agent and not reuse:
        jid, agent_info = run_agent_case(server, token, name, spec, tag)
        if not jid:
            return {"name": name, "job": None, "design": spec["design"], "backend": spec["backend"], "status": "no-run", "feasible": False,
                    "first_feasible": None, "rounds": 0, "planner_calls": 0, "eda_seconds": 0, "wall_seconds": round(time.time() - t0, 1),
                    "best": None, "ok": False, "error": "the agent did not start a run", "agent": agent_info}
    else:
        jid = reuse or _req(server, token, "POST", "/jobs", {"spec": spec, "max_iter": max_iter})["id"]
    while True:
        j = _req(server, token, "GET", f"/jobs/{jid}")
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(5)
    results = [e for e in j["events"] if e["kind"] == "result"]
    plans = sum(1 for e in j["events"] if e["kind"] == "plan")
    first = next((e["iteration"] for e in results if not e["violations"]), None)
    best = (j.get("summary") or {}).get("best")
    eda_s = sum(e.get("eda_seconds", 0) for e in results)
    ok = bool(best) == expect.get("feasible", True) and (first is None or first <= expect.get("max_rounds", 99))
    if expect.get("feasible", True) and best is None:
        ok = False
    return {"name": name, "job": jid, "design": spec["design"], "backend": spec["backend"], "status": j["status"], "feasible": bool(best), **({"agent": agent_info} if agent_info else {}),
            "first_feasible": first, "rounds": len(results), "planner_calls": plans, "eda_seconds": round(eda_s, 1), "wall_seconds": round(time.time() - t0, 1),
            "best": {"fmax_mhz": best["metrics"]["fmax_mhz"], "lut": best["metrics"]["lut"], "dsp": best["metrics"]["dsp"], "throughput": best["throughput"]} if best else None,
            "ok": ok, "expect": expect, "error": j.get("error")}


def cmd_run(a) -> int:
    server = a.server or os.environ.get("SA_SERVER", "http://127.0.0.1:8765")
    token = a.token or os.environ.get("SA_TOKEN", "")
    only = set(a.only.split(",")) if a.only else None
    cases = [c for c in CASES if not only or c[1]["backend"] in only or c[0] in only]
    rows = []
    existing = {j["task"]: j["id"] for j in _req(server, token, "GET", "/jobs")} if a.reuse else {}
    for name, spec, expect in cases:
        print(f"== {name} ({spec['design']}/{spec['backend']})", flush=True)
        r = run_case(server, token, name, spec, expect, a.max_iter, a.no_memory, a.tag, existing.get(f"[{a.tag}] {spec['task']}"), a.planner, a.agent)
        rows.append(r)
        b = r["best"]
        ag = r.get("agent") or {}
        lg = ag.get("ledger") or {}
        print(f"   {'OK ' if r['ok'] else 'REG'} feasible={r['feasible']} first@{r['first_feasible']} rounds={r['rounds']} "
              f"{(f'{b['fmax_mhz']:.1f} MHz {b['lut']} LUT {b['dsp']} DSP thr={b['throughput']}' if b else '-')} eda={r['eda_seconds']}s wall={r['wall_seconds']}s job={r['job']}"
              + (f"  agent: {len(ag.get('tools') or [])} tools, {lg.get('model_calls')} model calls, {lg.get('tokens')} tok, ${lg.get('usd')}" if lg else "")
              + (f"  FAILED TOOLS {ag['failed_tools']}" if ag.get("failed_tools") else "")
              + (f"  ERROR {str(r['error'])[:160]}" if r["error"] else ""), flush=True)
    rep = {"tag": a.tag, "t": time.time(), "max_iter": a.max_iter, "no_memory": a.no_memory, "planner": a.planner, "agent": a.agent, "cases": rows,
           "summary": {"n": len(rows), "ok": sum(r["ok"] for r in rows), "feasible": sum(r["feasible"] for r in rows),
                       "mean_first_feasible": round(sum(r["first_feasible"] or a.max_iter + 1 for r in rows) / max(len(rows), 1), 2),
                       "eda_seconds": round(sum(r["eda_seconds"] for r in rows), 1),
                       **({"agent_usd": round(sum((r.get("agent") or {}).get("ledger", {}).get("usd", 0) or 0 for r in rows), 4),
                           "agent_failed_tools": sum(len((r.get("agent") or {}).get("failed_tools") or []) for r in rows)} if a.agent else {})}}
    out = Path(a.out) if a.out else BENCH_DIR
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{a.tag}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    f.write_text(json.dumps(rep, indent=1))
    s = rep["summary"]
    print(f"\n{s['ok']}/{s['n']} within expectation, {s['feasible']} feasible, mean first-feasible round {s['mean_first_feasible']}, EDA {s['eda_seconds']}s -> {f}")
    prev = [r for r in sorted(out.glob(f"{a.tag}-*.json")) if r != f]
    if prev:
        alerts = compare(json.loads(prev[-1].read_text()), rep)
        rep["alerts"] = alerts
        f.write_text(json.dumps(rep, indent=1))
        if alerts:
            print("\nREGRESSION ALERTS vs " + prev[-1].name)
            for al in alerts:
                print("  ! " + al)
        else:
            print("no regressions vs " + prev[-1].name)
    return 0 if s["ok"] == s["n"] and not rep.get("alerts") else 1


def compare(old: dict, new: dict, fmax_tol: float = 0.06) -> list[str]:
    """Regressions between two reports: lost feasibility, more rounds to first feasible, Fmax down by more than
    the P&R seed noise (6%), LUT/DSP up by more than 15% with the same throughput."""
    alerts = []
    by = {c["name"]: c for c in old["cases"]}
    for c in new["cases"]:
        o = by.get(c["name"])
        if not o:
            continue
        if o["feasible"] and not c["feasible"]:
            alerts.append(f"{c['name']}: was feasible, now not")
            continue
        if not c["feasible"]:
            continue
        if o["first_feasible"] and c["first_feasible"] and c["first_feasible"] > o["first_feasible"] + 1:
            alerts.append(f"{c['name']}: first feasible at round {c['first_feasible']} (was {o['first_feasible']})")
        ob, nb = o["best"], c["best"]
        if ob and nb:
            if nb["throughput"] == ob["throughput"]:
                if nb["fmax_mhz"] < ob["fmax_mhz"] * (1 - fmax_tol):
                    alerts.append(f"{c['name']}: best Fmax {nb['fmax_mhz']:.1f} < {ob['fmax_mhz']:.1f} MHz (beyond seed noise)")
                if nb["lut"] > ob["lut"] * 1.15:
                    alerts.append(f"{c['name']}: best LUT {nb['lut']} > {ob['lut']} (+15%)")
                if nb["dsp"] > ob["dsp"]:
                    alerts.append(f"{c['name']}: best DSP {nb['dsp']} > {ob['dsp']}")
            elif nb["throughput"] < ob["throughput"]:
                alerts.append(f"{c['name']}: best throughput {nb['throughput']} < {ob['throughput']}")
    return alerts


def cmd_show(a) -> int:
    reps = sorted(Path(a.dir).glob("*.json"))[-a.last:] if a.dir else sorted(BENCH_DIR.glob("*.json"))[-a.last:]
    if not reps:
        print("no reports"); return 0
    data = [json.loads(r.read_text()) for r in reps]
    names = sorted({c["name"] for d in data for c in d["cases"]})
    hdr = f"{'case':28s}" + "".join(f"{r.stem[-15:]:>22s}" for r in reps)
    print(hdr)
    for n in names:
        row = f"{n:28s}"
        for d in data:
            c = next((c for c in d["cases"] if c["name"] == n), None)
            if not c:
                row += f"{'-':>22s}"
            elif c["best"]:
                row += f"{('' if c['ok'] else '!') + f'@{c['first_feasible']} {c['best']['fmax_mhz']:.0f}MHz {c['best']['lut']}L {c['best']['dsp']}D':>22s}"
            else:
                row += f"{'! infeasible':>22s}"
        print(row)
    print(f"{'':28s}" + "".join(f"{str(d['summary']['ok']) + '/' + str(d['summary']['n']) + ' ok, ' + str(d['summary']['eda_seconds']) + 's':>22s}" for d in data))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="silicon_agent.bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--only"); r.add_argument("--max-iter", type=int, default=6); r.add_argument("--tag", default="bench"); r.add_argument("--no-memory", action="store_true")
    r.add_argument("--server"); r.add_argument("--token"); r.add_argument("--out", help="report directory (default SA_ROOT/bench)")
    r.add_argument("--reuse", action="store_true", help="attach to jobs with the same [tag] task already on the server instead of resubmitting")
    r.add_argument("--planner", help="planner model for every case (A/B: run the suite once per model with distinct --tag)")
    r.add_argument("--agent", action="store_true", help="drive each case through the chat (Agnes Harness agent) instead of submitting the spec directly")
    s = sub.add_parser("show"); s.add_argument("--last", type=int, default=5); s.add_argument("--dir")
    sub.add_parser("cases")
    a = ap.parse_args(argv)
    if a.cmd == "cases":
        for n, sp, ex in CASES:
            print(f"{n:28s} {sp['design']:7s} {sp['backend']:7s} {sp['constraints']}  expect {ex}")
        return 0
    return cmd_run(a) if a.cmd == "run" else cmd_show(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
