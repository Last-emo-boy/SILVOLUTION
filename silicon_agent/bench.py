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


def _req(server: str, token: str, method: str, path: str, body=None):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{server}/api{path}", data=data, method=method,
                               headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read())


def run_case(server: str, token: str, name: str, spec: dict, expect: dict, max_iter: int, no_memory: bool, tag: str) -> dict:
    """Submit through the job server (so it serializes with everything else and shows in the UI), wait, score."""
    spec = json.loads(json.dumps(spec))
    spec["task"] = f"[{tag}] {spec['task']}"
    if no_memory:
        spec["no_memory"] = True
    t0 = time.time()
    jid = _req(server, token, "POST", "/jobs", {"spec": spec, "max_iter": max_iter})["id"]
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
    return {"name": name, "job": jid, "design": spec["design"], "backend": spec["backend"], "status": j["status"], "feasible": bool(best),
            "first_feasible": first, "rounds": len(results), "planner_calls": plans, "eda_seconds": round(eda_s, 1), "wall_seconds": round(time.time() - t0, 1),
            "best": {"fmax_mhz": best["metrics"]["fmax_mhz"], "lut": best["metrics"]["lut"], "dsp": best["metrics"]["dsp"], "throughput": best["throughput"]} if best else None,
            "ok": ok, "expect": expect, "error": j.get("error")}


def cmd_run(a) -> int:
    server = a.server or os.environ.get("SA_SERVER", "http://127.0.0.1:8765")
    token = a.token or os.environ.get("SA_TOKEN", "")
    only = set(a.only.split(",")) if a.only else None
    cases = [c for c in CASES if not only or c[1]["backend"] in only or c[0] in only]
    rows = []
    for name, spec, expect in cases:
        print(f"== {name} ({spec['design']}/{spec['backend']})", flush=True)
        r = run_case(server, token, name, spec, expect, a.max_iter, a.no_memory, a.tag)
        rows.append(r)
        b = r["best"]
        print(f"   {'OK ' if r['ok'] else 'REG'} feasible={r['feasible']} first@{r['first_feasible']} rounds={r['rounds']} "
              f"{(f'{b['fmax_mhz']:.1f} MHz {b['lut']} LUT {b['dsp']} DSP thr={b['throughput']}' if b else '-')} eda={r['eda_seconds']}s wall={r['wall_seconds']}s job={r['job']}"
              + (f"  ERROR {str(r['error'])[:160]}" if r["error"] else ""), flush=True)
    rep = {"tag": a.tag, "t": time.time(), "max_iter": a.max_iter, "no_memory": a.no_memory, "cases": rows,
           "summary": {"n": len(rows), "ok": sum(r["ok"] for r in rows), "feasible": sum(r["feasible"] for r in rows),
                       "mean_first_feasible": round(sum(r["first_feasible"] or a.max_iter + 1 for r in rows) / max(len(rows), 1), 2),
                       "eda_seconds": round(sum(r["eda_seconds"] for r in rows), 1)}}
    out = Path(a.out) if a.out else BENCH_DIR
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{a.tag}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    f.write_text(json.dumps(rep, indent=1))
    s = rep["summary"]
    print(f"\n{s['ok']}/{s['n']} within expectation, {s['feasible']} feasible, mean first-feasible round {s['mean_first_feasible']}, EDA {s['eda_seconds']}s -> {f}")
    return 0 if s["ok"] == s["n"] else 1


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
