#!/usr/bin/env python3
"""silicon-agent as an MCP server (stdio): the job server's API as tools for any MCP client (Claude Code, Claude
Desktop, Cursor ...). The same tools the Agnes Harness plugin exposes, so an outside agent can interpret, estimate,
run, follow, steer, read artifacts and formal reports, upload its own RTL, and propose generator changes.

  SA_SERVER  job server base URL (default http://127.0.0.1:8765; the laptop reaches netcup through the SSH tunnel)
  SA_TOKEN   bearer token (or the macOS Keychain item `silicon-agent-token` when unset)

Claude Code:  claude mcp add silicon-agent -- python3 /path/to/silicon-agent/sa_mcp.py
No dependencies beyond the standard library: JSON-RPC 2.0 over stdin/stdout, MCP protocol 2024-11-05.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

SERVER = os.environ.get("SA_SERVER", "http://127.0.0.1:8765").rstrip("/")


def _token() -> str:
    t = os.environ.get("SA_TOKEN", "")
    if t:
        return t
    try:
        return subprocess.run(["security", "find-generic-password", "-s", "silicon-agent-token", "-w"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def api(method: str, path: str, body=None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{SERVER}/api{path}", data=data, method=method, headers={"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode()
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return {"error": f"HTTP {e.code}", **json.loads(txt)}
        except ValueError:
            return {"error": f"HTTP {e.code}: {txt[:500]}"}
    try:
        return json.loads(txt)
    except ValueError:
        return {"raw": txt[:4000]}


def _summarize(j: dict) -> dict:
    results = [e for e in j.get("events", []) if e.get("kind") == "result"]
    plans = [e for e in j.get("events", []) if e.get("kind") == "plan"]
    best = (j.get("summary") or {}).get("best")
    return {
        "status": j.get("status"), "design": (j.get("spec") or {}).get("design"), "backend": (j.get("spec") or {}).get("backend"), "task": (j.get("spec") or {}).get("task"),
        "constraints": (j.get("spec") or {}).get("constraints"),
        "iterations": [{"iteration": e.get("iteration"), "slot": e.get("slot", 0), "ir": e.get("ir"), "action": e.get("action"),
                        "fmax_mhz": e["metrics"].get("fmax_mhz"), "lut": e["metrics"].get("lut"), "dsp": e["metrics"].get("dsp"), "area_um2": e["metrics"].get("area_um2"),
                        "power_mw": e["metrics"].get("power_mw"), "throughput": e.get("throughput"), "latency_cycles": e.get("latency_cycles"), "sim_passed": e["metrics"].get("sim_passed"),
                        "violations": e.get("violations"), "critical_path": (e["metrics"].get("critical_path") or [])[:6], "eda_seconds": e.get("eda_seconds")} for e in results],
        "plans": [{"iteration": p.get("iteration"), "diagnosis": p.get("diagnosis"), "action": p.get("action"), "stop": p.get("stop", False), "stop_reason": p.get("stop_reason")} for p in plans],
        "best": {"iteration": best["iteration"], "ir": best["ir"], "metrics": {k: best["metrics"].get(k) for k in ("fmax_mhz", "lut", "dsp", "bram", "area_um2", "power_mw")}, "throughput": best.get("throughput")} if best else None,
        "error": (j.get("error") or "")[:600] or None,
        **({"sweep": (j.get("summary") or {}).get("sweep")} if (j.get("summary") or {}).get("sweep") else {}),
    }


TOOLS = [
    {"name": "sa_schema", "description": "Generators (matmul, fir, decim, nco, iir, conv2d, cordic, fft, fir_chain, graph), their IR knobs and ranges, the devices (FPGAs + sky130 ASIC) and measured device facts.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "sa_interpret", "description": "Natural-language hardware request -> complete spec (design, device, locked task fields, constraints, objectives, initial IR) + assumptions + alternatives. Does not start a run.", "inputSchema": {"type": "object", "properties": {"request": {"type": "string"}, "thread": {"type": "string"}}, "required": ["request"]}},
    {"name": "sa_estimate", "description": "Closed-form + calibrated estimate (Fmax, LUT/cells, DSP, throughput) of one IR on one device, no EDA.", "inputSchema": {"type": "object", "properties": {"design": {"type": "string"}, "backend": {"type": "string"}, "ir": {"type": "object"}}, "required": ["design", "backend", "ir"]}},
    {"name": "sa_run", "description": "Start an autonomous optimization run from a spec (generate, simulate, synthesize, place-and-route, diagnose, iterate). Returns the job id; follow with sa_job.", "inputSchema": {"type": "object", "properties": {"spec": {"type": "object"}, "max_iter": {"type": "integer"}, "thread": {"type": "string"}}, "required": ["spec"]}},
    {"name": "sa_sweep", "description": "Design-space sweep: measure every point of the product of the knob values (e.g. {\"fold\":[1,2,4]}) on the device, no planner; the run's summary carries the table with Pareto points.", "inputSchema": {"type": "object", "properties": {"spec": {"type": "object"}, "sweep": {"type": "object"}, "thread": {"type": "string"}}, "required": ["spec", "sweep"]}},
    {"name": "sa_job", "description": "Status and full trajectory of a run (per-iteration IR, measured numbers, violations, critical path, planner diagnosis, best design). wait_seconds > 0 blocks up to that long (max 300) while the run is going.", "inputSchema": {"type": "object", "properties": {"job": {"type": "string"}, "wait_seconds": {"type": "integer"}}, "required": ["job"]}},
    {"name": "sa_steer", "description": "Instruction for the running planner's next proposal.", "inputSchema": {"type": "object", "properties": {"job": {"type": "string"}, "message": {"type": "string"}}, "required": ["job", "message"]}},
    {"name": "sa_stop", "description": "Stop a run after its current iteration.", "inputSchema": {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]}},
    {"name": "sa_jobs", "description": "Recent runs, newest first (optionally one thread's).", "inputSchema": {"type": "object", "properties": {"thread": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "sa_pareto", "description": "Every measured point of one generator on one device, the Pareto front, the best under optional constraints.", "inputSchema": {"type": "object", "properties": {"design": {"type": "string"}, "backend": {"type": "string"}, "fmax_mhz": {"type": "number"}, "max_dsp": {"type": "integer"}, "max_lut": {"type": "integer"}}, "required": ["design", "backend"]}},
    {"name": "sa_job_file", "description": "Read a run artifact: 'rtl' (best design's SystemVerilog), summary.json, iter_NN.json, llm_NN.json, events.jsonl, formal/report.json.", "inputSchema": {"type": "object", "properties": {"job": {"type": "string"}, "path": {"type": "string"}, "max_bytes": {"type": "integer"}}, "required": ["job", "path"]}},
    {"name": "sa_formal", "description": "SymbiYosys bounded check of a finished run: equivalence of the measured RTL vs a fresh emit + stream properties. Runs in the background; call again until status is done.", "inputSchema": {"type": "object", "properties": {"job": {"type": "string"}, "depth": {"type": "integer"}}, "required": ["job"]}},
    {"name": "sa_mutation", "description": "Mutation score of a finished run's testbench (24 injected RTL bugs; killed/total; survivors listed). Background: call again until done.", "inputSchema": {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]}},
    {"name": "sa_package_url", "description": "URL of the delivery package (zip: RTL, AXI-Stream wrapper, testbench, reports, trajectory; GDS on sky130) of a finished run.", "inputSchema": {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]}},
    {"name": "sa_rtl_upload", "description": "Bring your own RTL: files ({name: text}), top module, a self-checking SV bench printing RESULT PASS/FAIL, and the parameters the planner may tune ({NAME: {default, min, max | choices, hint}}). Returns design id rtl:<id>.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "top": {"type": "string"}, "files": {"type": "object"}, "tb": {"type": "string"}, "params": {"type": "object"}, "port_widths": {"type": "object"}, "latency": {"type": "string"}, "throughput": {"type": "string"}, "throughput_unit": {"type": "string"}, "description": {"type": "string"}}, "required": ["top", "files", "tb", "params"]}},
    {"name": "sa_rtl_list", "description": "RTL bundles uploaded so far.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "sa_propose", "description": "Propose a change to silicon-agent's generators as a unified diff (paths silicon_agent/...). Gated: emit/elaborate, behavioral sims, formal equivalence of unchanged IRs, mutation score, optional A/B run. Never merged automatically.", "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "rationale": {"type": "string"}, "diff": {"type": "string"}, "designs": {"type": "array", "items": {"type": "string"}}, "irs": {"type": "array"}, "ab_spec": {"type": "object"}, "ab_max_iter": {"type": "integer"}}, "required": ["title", "rationale", "diff", "designs"]}},
    {"name": "sa_proposal", "description": "Gate verdict of a proposal (per step) and its A/B run.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "sa_bench", "description": "Last nightly regression result.", "inputSchema": {"type": "object", "properties": {}}},
]


def call(name: str, a: dict):
    if name == "sa_schema":
        return api("GET", "/schema")
    if name == "sa_interpret":
        return api("POST", "/interpret", {"text": a["request"], "thread": a.get("thread")})
    if name == "sa_estimate":
        return api("POST", "/estimate", {"design": a["design"], "backend": a["backend"], "ir": a["ir"]})
    if name == "sa_run":
        return api("POST", "/jobs", {"spec": a["spec"], "max_iter": a.get("max_iter", 8), "thread": a.get("thread")})
    if name == "sa_sweep":
        return api("POST", "/jobs", {"spec": {**a["spec"], "sweep": a["sweep"]}, "max_iter": 1, "thread": a.get("thread")})
    if name == "sa_job":
        deadline = time.time() + min(int(a.get("wait_seconds") or 0), 300)
        while True:
            j = api("GET", f"/jobs/{a['job']}")
            if "error" in j and "status" not in j:
                return j
            if j.get("status") not in ("queued", "running") or time.time() >= deadline:
                out = _summarize(j)
                out["note"] = "finished" if j.get("status") not in ("queued", "running") else "still running: call again"
                return out
            time.sleep(5)
    if name == "sa_steer":
        return api("POST", f"/jobs/{a['job']}/steer", {"message": a["message"]})
    if name == "sa_stop":
        return api("POST", f"/jobs/{a['job']}/stop", {})
    if name == "sa_jobs":
        js = api("GET", "/jobs" + (f"?thread={a['thread']}" if a.get("thread") else ""))
        return js[: a.get("limit", 20)] if isinstance(js, list) else js
    if name == "sa_pareto":
        q = "&".join(f"{k}={a[k]}" for k in ("design", "backend", "fmax_mhz", "max_dsp", "max_lut") if a.get(k) is not None)
        return api("GET", f"/pareto?{q}")
    if name == "sa_job_file":
        p = a["path"].strip("/")
        path = f"/jobs/{a['job']}/rtl" if p == "rtl" else f"/jobs/{a['job']}/files/{p}"
        req = urllib.request.Request(f"{SERVER}/api{path}", headers={"Authorization": f"Bearer {_token()}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                t = r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code} for {p}"}
        cap = int(a.get("max_bytes") or 32768)
        return {"text": t[:cap] + (f"\n...[truncated {len(t) - cap} bytes]" if len(t) > cap else ""), "bytes": len(t)}
    if name == "sa_formal":
        return api("GET", f"/jobs/{a['job']}/formal?depth={a.get('depth', 16)}")
    if name == "sa_mutation":
        return api("GET", f"/jobs/{a['job']}/mutation")
    if name == "sa_package_url":
        return {"url": f"{SERVER}/api/jobs/{a['job']}/package", "note": "GET with the bearer token; a zip"}
    if name == "sa_rtl_upload":
        return api("POST", "/rtl", a, timeout=400)
    if name == "sa_rtl_list":
        return api("GET", "/rtl")
    if name == "sa_propose":
        return api("POST", "/proposals", a)
    if name == "sa_proposal":
        r = api("GET", f"/proposals/{a['id']}")
        r.pop("diff", None)
        return r
    if name == "sa_bench":
        return api("GET", "/bench")
    return {"error": f"unknown tool {name}"}


def main() -> None:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "initialize":
            res = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "silicon-agent", "version": "0.4"}}
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            try:
                r = call(params.get("name", ""), params.get("arguments") or {})
                res = {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}], "isError": isinstance(r, dict) and "error" in r and len(r) <= 2}
            except Exception as e:
                res = {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}], "isError": True}
        elif method == "ping":
            res = {}
        else:
            if mid is not None:
                out.write(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}) + "\n"); out.flush()
            continue
        if mid is not None:
            out.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}, ensure_ascii=False) + "\n"); out.flush()


if __name__ == "__main__":
    main()
