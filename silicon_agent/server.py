"""Job server: runs design loops on the EDA host and exposes them over HTTP,
plus the single-page web UI.

API
  POST /api/jobs                 {"spec": {...}, "max_iter": 8}      -> {"id": ...}
  GET  /api/jobs                 list (newest first), rebuilt from results/ on start
  GET  /api/jobs/<id>            {"status", "events": [...], "summary"}  (?after=N incremental)
  GET  /api/jobs/<id>/files/<p>  any artifact under results/<id>/
  GET  /api/jobs/<id>/package    zip: RTL + AXI-Stream wrapper + testbench/vectors + reports + trajectory + README
  GET  /api/jobs/<id>/axis       the AXI-Stream wrapper alone
  GET  /api/jobs/<id>/formal[?depth=20]  SymbiYosys: bounded equivalence of the measured RTL vs a fresh emit + stream properties
  GET  /api/jobs/<id>/mutation   mutation score of the behavioral testbench on the best design (background, cached)
  GET  /api/specs                example specs shipped in specs/
  GET  /api/health
  POST /api/interpret            {"text": "...", "thread"?: id} -> spec + assumptions + alternatives (no run)
  POST /api/estimate             {"spec"} or {"design","backend","ir"} -> screening estimate, no EDA
  POST /api/jobs/<id>/steer      {"message": "..."}  -> read by the planner before its next proposal
  POST /api/jobs/<id>/stop
  POST /api/proposals            {"diff", "title", "rationale", "designs": [..], "irs"?: [{design, backend, ir}], "ab_spec"?, "thread"?}
                                 a change to the generators: gated on a scratch copy (emit/sim/formal/mutation), then an A/B run
  GET  /api/proposals[/<id>]     gate verdict per step; POST /api/proposals/<id>/merge records the operator's decision (token only)
  GET  /api/memory               lessons per (design, backend), scored
  GET  /api/bench                last nightly regression status + report
  GET  /api/pareto?design=&backend=[&fmax_mhz=&max_dsp=&max_lut=]   every measured point, the Pareto front, best under constraints
  POST /api/memory/<d>/<b>/vote  {"id": ..., "vote": "up"|"down"}
  GET  /api/threads              conversations (newest first)
  GET  /api/threads/<id>         messages + the jobs they refer to
  POST /api/threads/<id>/chat    {"text": ...}  (id "new" opens a thread); interprets, then runs/forks/steers/answers
  POST /api/threads/<id>/title    {"title": ...}  rename
  DELETE /api/threads/<id>        (refused while its job runs)
Conversations are per login subject unless SA_SHARED=1; the CLI token sees all.
  GET  /                         chat UI (static, no build step)
  GET  /work                     engineer's worktable (per-run transcript, inspector)

Legacy paths without /api/ keep working for the `sa` CLI.

Auth: two trust paths.
  1. Bearer token (SA_TOKEN)                 -- the `sa` CLI over the SSH tunnel
  2. Gateway identity headers from sluice    -- browser SSO; the gateway injects
     X-Auth-Subject + X-Auth-Sig (HMAC with the shared GATEWAY_HMAC_KEY), which we
     verify, so the UI needs no token of its own.
One job runs at a time per server (EDA tools are CPU-bound); others queue.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from . import memory, nl
from .designs import DESIGNS
from .eda import ROOT, BACKENDS
from .loop import run_loop

RESULTS = ROOT / "results"
THREADS = ROOT / "threads"
SPECS = Path(__file__).resolve().parent.parent / "specs"
STATIC = Path(__file__).resolve().parent / "web"
TOKEN = os.environ.get("SA_TOKEN", "")
# Shared with sluice (GATEWAY_HMAC_KEY). When set, X-Auth-Subject is trusted only if
# X-Auth-Sig = HMAC-SHA256(key, subject "\n" groups "\n" epoch_minute) for the current or
# previous minute -- the gateway's documented canonical form. Nothing else on the docker
# network can mint that, so a direct dial to this port cannot forge identity.
GATEWAY_KEY = os.environ.get("SA_GATEWAY_HMAC_KEY", "")

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_queue: queue.Queue[str] = queue.Queue()
_shutting_down = threading.Event()
_worker_idle = threading.Event()
_worker_idle.set()


# ---------------------------------------------------------------- persistence
def _load_from_disk() -> None:
    """Rebuild the job table from results/<id>/ so restarts don't lose history. A job that was
    running or queued when the server went down (state.json says so) is re-queued: the EDA cache
    makes the already-evaluated iterations free, so it resumes where it was."""
    if not RESULTS.exists():
        return
    for d in sorted(RESULTS.iterdir()):
        if not d.is_dir() or not (d / "spec.json").exists():
            continue
        try:
            spec = json.loads((d / "spec.json").read_text())
        except ValueError:
            # a submission cut by a restart before its spec landed: nothing to resume
            print(f"results/{d.name}: unreadable spec.json, skipped", flush=True)
            continue
        try:
            state = json.loads((d / "state.json").read_text()) if (d / "state.json").exists() else {}
        except ValueError:
            state = {"status": "queued"}   # truncated by a crash mid-write: treat as interrupted and resume
        job = {"spec": spec, "max_iter": state.get("max_iter", 0), "status": "interrupted", "events": [], "created": d.stat().st_mtime,
               "subject": state.get("subject", ""), "parent": state.get("parent"), "thread": state.get("thread")}
        summ = d / "summary.json"
        if summ.exists():
            s = json.loads(summ.read_text())
            job["summary"] = {k: v for k, v in s.items() if k != "history"}
            job["status"] = "done" if s.get("best") else "failed"
        evlog = d / "events.jsonl"
        if evlog.exists():
            job["events"] = [json.loads(l) for l in evlog.read_text().splitlines() if l.strip()]
        elif summ.exists():
            job["events"] = _events_from_history(s.get("history", []))
        else:
            iters = sorted(d.glob("iter_*.json"))
            job["events"] = _events_from_history([json.loads(f.read_text()) for f in iters])
        if state.get("status") in ("queued", "running") and not summ.exists():
            # interrupted by a restart: requeue (cached iterations replay instantly)
            job["status"] = "queued"
            job["resumed"] = True
            _queue.put(d.name)
        _jobs[d.name] = job


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _save_state(job_id: str) -> None:
    job = _jobs[job_id]
    (RESULTS / job_id).mkdir(parents=True, exist_ok=True)
    tmp = RESULTS / job_id / "state.json.tmp"
    tmp.write_text(json.dumps({"status": job["status"], "max_iter": job["max_iter"], "subject": job.get("subject", ""),
                               "parent": job.get("parent"), "thread": job.get("thread")}))
    tmp.replace(RESULTS / job_id / "state.json")   # atomic: a restart never sees a half-written file


def _events_from_history(history: list[dict]) -> list[dict]:
    evs = []
    for h in history:
        ir = {k: v for k, v in h["ir"].items() if k != "coefs"}
        evs.append({"t": 0, "kind": "evaluating", "iteration": h["iteration"], "ir": ir})
        evs.append({"t": 0, "kind": "result", **{k: v for k, v in h.items() if k not in ("metrics", "ir")},
                    "ir": ir, "metrics": {k: v for k, v in h["metrics"].items() if k != "sim_log"}})
    return evs


# ---------------------------------------------------------------- worker
def _worker():
    while True:
        _worker_idle.set()
        job_id = _queue.get()
        _worker_idle.clear()
        with _lock:
            job = _jobs[job_id]
            if job["status"] == "deleted":
                continue
            job["status"] = "running"
            job["started"] = time.time()
            _save_state(job_id)
        try:
            evlog = (RESULTS / job_id / "events.jsonl")
            evlog.parent.mkdir(parents=True, exist_ok=True)
            if job.get("resumed"):
                # the loop replays from the EDA and planner caches, so the transcript is rebuilt from scratch
                with _lock:
                    job["events"] = [{"t": 0, "kind": "note", "message": "server restarted; replaying from cache"}]
                evlog.write_text(json.dumps(job["events"][0]) + "\n")
                for f in (RESULTS / job_id).glob("iter_*.json"):
                    f.unlink()

            def on_event(ev: dict):
                rec = {"t": round(time.time() - job["started"], 1), **ev}
                with _lock:
                    job["events"].append(rec)
                with evlog.open("a") as f:
                    f.write(json.dumps(rec) + "\n")

            class Control:
                def steer(self):
                    with _lock:
                        msgs, job["steer"] = job.get("steer", []), []
                    return msgs
                def should_stop(self):
                    with _lock:
                        return bool(job.get("stop_requested")) or _shutting_down.is_set()

            if job["spec"].get("generator_tree"):
                summary = _run_loop_on_tree(job, job_id, on_event, Control())
            else:
                summary = run_loop(job["spec"], max_iter=job["max_iter"], out_dir=RESULTS / job_id, on_event=on_event, control=Control())
            with _lock:
                if _shutting_down.is_set() and not job.get("stop_requested"):
                    # loop ended because we are going down: leave it queued for the next start
                    job["status"] = "queued"
                    (RESULTS / job_id / "summary.json").unlink(missing_ok=True)
                else:
                    job["summary"] = {k: v for k, v in summary.items() if k != "history"}
                    job["status"] = "done" if summary["best"] else "failed"
                _save_state(job_id)
        except Exception as e:  # keep the server alive; surface the error to the client
            with _lock:
                job["status"] = "error"
                job["error"] = f"{e}\n{traceback.format_exc()[-2000:]}"
                _save_state(job_id)
        finally:
            with _lock:
                job["finished"] = time.time()
            if job["status"] not in ("queued", "running"):
                _narrate_job_async(job_id)



def _run_loop_on_tree(job: dict, job_id: str, on_event, control) -> dict:
    """A proposal's A/B: the loop runs in a subprocess whose silicon_agent is the patched scratch tree (the server's
    own modules stay what they are). Events come back as JSON lines; steer/stop go through a control file."""
    tree = Path(job["spec"]["generator_tree"])
    out = RESULTS / job_id
    specf = out / "spec.json"; evf = out / "loop_events.jsonl"; ctl = out / "control.json"
    evf.write_text(""); ctl.write_text("{}")
    env = dict(os.environ); env["PYTHONPATH"] = str(tree); env.pop("SA_AGH", None)
    p = subprocess.Popen([sys.executable, "-m", "silicon_agent.loop", str(specf), str(job["max_iter"]), "--out", str(out), "--events", str(evf), "--control", str(ctl)],
                         cwd=str(tree), env=env, stdout=subprocess.DEVNULL, stderr=open(out / "loop_stderr.log", "w"))
    seen = 0
    while True:
        rc = p.poll()
        with evf.open() as f:
            lines = f.read().splitlines()
        for line in lines[seen:]:
            try:
                on_event(json.loads(line))
            except ValueError:
                pass
        seen = len(lines)
        msgs = control.steer()
        if msgs or control.should_stop():
            try:
                d = json.loads(ctl.read_text()) if ctl.exists() else {}
            except ValueError:
                d = {}
            d["steer"] = (d.get("steer") or []) + msgs
            d["stop"] = d.get("stop") or control.should_stop()
            ctl.write_text(json.dumps(d))
        if rc is not None:
            break
        time.sleep(2)
    summ = out / "summary.json"
    if not summ.exists():
        raise RuntimeError(f"A/B loop on {tree} exited {rc} without a summary: {(out / 'loop_stderr.log').read_text()[-1500:]}")
    return json.loads(summ.read_text())


def _gateway_sig_ok(subject: str, groups: str, sig: str) -> bool:
    now = int(time.time()) // 60
    for window in (now, now - 1):
        msg = f"{subject}\n{groups}\n{window}".encode()
        want = hmac.new(GATEWAY_KEY.encode(), msg, hashlib.sha256).hexdigest()
        if hmac.compare_digest(want, sig.lower()):
            return True
    return False


# ---------------------------------------------------------------- conversation threads
# A thread is the user-facing unit: a list of messages, some of which are bound to jobs.
# Messages: {"role": "user"|"assistant", "text", "t", "job": <id>|None, "kind": "text"|"run"|"spec"}
_threads: dict[str, dict] = {}


def _load_threads() -> None:
    THREADS.mkdir(parents=True, exist_ok=True)
    for f in sorted(THREADS.glob("*.json")):
        _threads[f.stem] = json.loads(f.read_text())


def _resume_turns() -> None:
    """A thread left `busy` by a restart had an agent turn in flight. The AGH session is another process and may
    still be running that turn: attach to it and take its outcome as the reply (runs it starts get their cards);
    if the turn is already over, a short prompt in the same session makes the agent report from its own records.
    Without AGH the turn is simply marked interrupted."""
    from . import agh
    for tid, th in list(_threads.items()):
        if not th.get("busy"):
            continue
        if agh.enabled() and (th.get("agh_session") or th.get("agh_author_session")):
            def go(tid=tid):
                th = _threads[tid]
                first = next((m["text"] for m in th["messages"] if m["role"] == "user"), "")
                lang = nl._lang(first)
                try:
                    c = agh.client()
                    author = (agh.MODES.get(th.get("mode") or "") or {}).get("session") == "author" and th.get("agh_author_session")
                    sid = th["agh_author_session"] if author else th["agh_session"]
                    c.load_session(sid, _author_tree(tid) if author else agh.workspace())
                    # the harness aborts a turn whose client connection went away (that is what a restart looks
                    # like to it), so usually there is nothing to follow; when there is, take its outcome
                    r = c.wait_idle(sid, timeout=3600, on_event=_run_watcher(tid, lang)) if (c.project(sid) or {}).get("opState") else None
                    if r and (r["text"] or r["tools"]):
                        r["usage"] = c.usage(sid)
                        _record_turn(tid, r, lang)
                    else:
                        raise agh.AghError("turn already over")
                except Exception:
                    # ask the agent to carry on from its own tool records: the runs it started are still on the
                    # job server, and the session history holds everything it had found out
                    nudge = ("服务器刚重启过，上一轮被打断。请根据你之前的工具记录继续刚才的任务：查一下你启动的运行现在的状态（sa_jobs / sa_job），需要的话继续后续步骤，然后把结果报告出来。"
                             if lang == "zh" else
                             "The server was restarted and the previous turn was cut short. Continue the task from your own tool records: check the runs you started (sa_jobs / sa_job), finish any remaining steps, then report the result.")
                    try:
                        _agh_turn(tid, nudge, th.get("subject", "cli"), resumed=True)
                    except Exception as e2:
                        th["messages"].append({"role": "assistant", "kind": "text", "text": f"Something went wrong: {e2}", "t": time.time()})
                finally:
                    _turn_done(tid)
            threading.Thread(target=go, daemon=True).start()
        else:
            th["busy"] = False
            th.pop("live", None); th.pop("live_note", None)
            th["messages"].append({"role": "assistant", "kind": "text", "text": "（服务器重启，这一轮被打断了）" if nl._lang(th["messages"][0]["text"] if th["messages"] else "") == "zh" else "(the server restarted; this turn was interrupted)", "t": time.time()})
            _save_thread(tid)


def _save_thread(tid: str) -> None:
    THREADS.mkdir(parents=True, exist_ok=True)
    (THREADS / f"{tid}.json").write_text(json.dumps(_threads[tid], ensure_ascii=False, indent=1))


SHARED = os.environ.get("SA_SHARED", "") == "1"  # 1: every login sees every conversation (single-team mode)


def _visible(th: dict, sub: str) -> bool:
    """Conversations belong to the SSO subject that opened them. The token identity ("cli") is the operator:
    it sees everything, and what it starts is visible to every login (there is no per-user CLI token)."""
    return SHARED or sub in ("cli", "anonymous") or th.get("subject", "") in ("", "cli", sub)


def _thread_jobs(tid: str) -> list[str]:
    """Job ids bound to a thread, oldest first."""
    th = _threads.get(tid) or {"messages": []}
    ids = {jid for jid, j in _jobs.items() if j.get("thread") == tid} | {m["job"] for m in th["messages"] if m.get("job") and m["job"] in _jobs}
    return sorted(ids, key=lambda j: _jobs[j]["created"])


def _thread_note_job(tid: str, job_id: str, text: str, fork: bool) -> None:
    """A job started from the thread's worktable: give it a turn in the conversation so it gets cards and a report."""
    th = _threads.get(tid)
    if not th:
        return
    first = next((m["text"] for m in th["messages"] if m["role"] == "user"), "")
    with _lock:
        th["messages"].append({"role": "assistant", "kind": "run", "text": text, "job": job_id, "lang": nl._lang(first), "t": time.time(),
                               "fork": fork, "source": "worktable"})
        _save_thread(tid)


def _thread_context(th: dict) -> dict | None:
    """The run the conversation is currently about (the thread's newest job), flattened for the LLM."""
    ids = _thread_jobs(th["id"])
    if not ids:
        return None
    jid = ids[-1]
    job = _jobs[jid]
    results = [e for e in job["events"] if e["kind"] == "result"]
    last = results[-1] if results else None
    return {"job": jid, "spec": job["spec"], "status": job["status"], "best": (job.get("summary") or {}).get("best"),
            "last": {k: last[k] for k in ("iteration", "ir", "violations", "throughput")} | {"metrics": {k: last["metrics"][k] for k in ("fmax_mhz", "lut", "dsp")}} if last else None,
            "iterations": len(results), "max_iter": job.get("max_iter")}


def _narrate_if_done(tid: str) -> None:
    """Append the assistant's prose report once the thread's current job has finished (idempotent, blocking LLM call)."""
    th = _threads.get(tid)
    if not th:
        return
    for m in th["messages"]:
        if m.get("kind") != "run" or m.get("narrated"):
            continue
        if m.get("source") == "agh":
            # the agent reported on its own runs in its turn text; a second narration would repeat it
            m["narrated"] = True
            continue
        job = _jobs.get(m["job"])
        if not job or job["status"] in ("queued", "running"):
            continue
        with _lock:
            if m.get("narrating"):
                continue
            m["narrating"] = True
        if job["status"] in ("done", "failed"):
            text = nl.narrate({**(job.get("summary") or {}), "spec": job["spec"]}, job["events"], m.get("lang", "en"))
        elif job["status"] == "interrupted":
            text = "已停止。" if m.get("lang") == "zh" else "Stopped."
        else:
            text = f"Run ended with an error: {job.get('error', '')[:300]}"
        with _lock:
            th["messages"].append({"role": "assistant", "kind": "report", "text": text, "job": m["job"], "t": time.time()})
            m["narrated"] = True
            m.pop("narrating", None)
            _save_thread(tid)


def _narrate_job_async(job_id: str) -> None:
    for tid, th in list(_threads.items()):
        if any(m.get("job") == job_id and m.get("kind") == "run" for m in th["messages"]):
            threading.Thread(target=_narrate_if_done, args=(tid,), daemon=True).start()
    threading.Thread(target=_notify, args=(job_id,), daemon=True).start()


def _notify(job_id: str) -> None:
    """Run-finished notification: POST a small JSON to SA_WEBHOOK (and to the job's own `webhook` when it names one),
    so a user does not have to keep the tab open. Payload: id, status, task, design, backend, best metrics, link."""
    job = _jobs.get(job_id)
    if not job:
        return
    urls = [u for u in (os.environ.get("SA_WEBHOOK", ""), (job.get("spec") or {}).get("webhook", "")) if u]
    if not urls:
        return
    best = (job.get("summary") or {}).get("best")
    payload = {"id": job_id, "status": job["status"], "task": job["spec"].get("task", ""), "design": job["spec"].get("design"), "backend": job["spec"].get("backend"),
               "best": {k: best["metrics"].get(k) for k in ("fmax_mhz", "lut", "dsp", "area_um2")} | {"throughput": best.get("throughput")} if best else None,
               "link": f"{os.environ.get('SA_PUBLIC_URL', '').rstrip('/')}/work?job={job_id}" if os.environ.get("SA_PUBLIC_URL") else None,
               "text": (f"{'✓' if best else '✕'} {job['spec'].get('task', job_id)[:80]} — " + (f"{best['metrics']['fmax_mhz']:.1f} MHz · {best['metrics']['lut']} {'cells' if best['metrics'].get('area_um2') else 'LUT'} · {best['metrics']['dsp']} DSP" if best else "no feasible design"))}
    import urllib.request
    for u in urls:
        try:
            body = payload
            if "hooks.slack.com" in u or "discord.com/api/webhooks" in u:
                body = {"text": payload["text"] + (f" {payload['link']}" if payload["link"] else ""), "content": payload["text"]}
            req = urllib.request.Request(u, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:
            print(f"webhook {u[:40]}...: {e}", flush=True)


def _submit(spec: dict, max_iter: int, sub: str, parent: dict | None = None, thread: str | None = None) -> str:
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    with _lock:
        _jobs[job_id] = {"spec": spec, "max_iter": max_iter, "status": "queued", "events": [], "created": time.time(), "subject": sub,
                         "parent": parent, "thread": thread}
        (RESULTS / job_id).mkdir(parents=True, exist_ok=True)
        _write_atomic(RESULTS / job_id / "spec.json", json.dumps(spec, indent=2))
        _save_state(job_id)
    _queue.put(job_id)
    return job_id


def _handle_chat(tid: str, text: str, sub: str) -> dict:
    """One user turn. Decides: new run, fork of the current run, steer the running job, or just answer."""
    th = _threads[tid]
    ctx = _thread_context(th)
    lang = nl._lang(text)
    # a running job: requests steer it instead of starting another run; questions are just answered
    if ctx and ctx["status"] in ("queued", "running"):
        ans = nl.reply(text, ctx, None)
        if not ans["is_request"]:
            th["messages"].append({"role": "assistant", "kind": "text", "text": ans["text"], "job": ctx["job"], "t": time.time()})
            _save_thread(tid)
            return {"action": "answer", "job": ctx["job"]}
        job = _jobs[ctx["job"]]
        with _lock:
            job.setdefault("steer", []).append(text)
            rec = {"t": round(time.time() - job.get("started", time.time()), 1), "kind": "user", "message": text, "subject": sub}
            job["events"].append(rec)
        with (RESULTS / ctx["job"] / "events.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")
        th["messages"].append({"role": "assistant", "kind": "text", "job": ctx["job"], "t": time.time(),
                               "text": "收到，会在下一轮规划前生效。" if lang == "zh" else "Got it — the planner will take that into account before its next proposal."})
        _save_thread(tid)
        return {"action": "steer", "job": ctx["job"]}
    # otherwise: is it a question about the run, or a (new/refined) request?
    if ctx:
        from . import pareto
        with _lock:
            fr = pareto.front(pareto.points(_jobs, ctx["spec"].get("design", "matmul"), ctx["spec"].get("backend", "ecp5")))
        ans = nl.reply(text, ctx, fr)
        if not ans["is_request"]:
            th["messages"].append({"role": "assistant", "kind": "text", "text": ans["text"], "job": ctx["job"], "t": time.time()})
            _save_thread(tid)
            return {"action": "answer", "job": ctx["job"]}
    it = nl.interpret(text, ctx)
    spec = it["spec"]
    if it["errors"]:
        th["messages"].append({"role": "assistant", "kind": "text", "text": ("我没法把这个变成合法的设计：" if lang == "zh" else "I could not turn that into a valid design: ") + "; ".join(it["errors"]), "t": time.time()})
        _save_thread(tid)
        return {"action": "error"}
    parent = None
    if it["fork"] and ctx:
        # continue from where the previous run got to: its best design, else its closest miss
        src = _jobs[ctx["job"]]
        results = [e for e in src["events"] if e["kind"] == "result"]
        start = (src.get("summary") or {}).get("best") or (min(results, key=lambda e: (len(e["violations"]), -e["iteration"])) if results else None)
        if start:
            seed = {k: v for k, v in start["ir"].items() if k != "coefs" and k not in spec["locked"]}
            cand = {**spec["initial_ir"], **seed}
            if not DESIGNS[spec["design"]].ir_cls.from_dict(cand).validate():
                spec["initial_ir"] = cand
            parent = {"id": ctx["job"], "iteration": start.get("iteration")}
    max_iter = 8
    job_id = _submit(spec, max_iter, sub, parent, thread=tid)
    if not th["title"]:
        th["title"] = spec["task"]
    lines = [it["summary"]] + [("假设：" if lang == "zh" else "Assuming ") + a for a in it["assumptions"][:3]]
    if it["questions"]:
        lines.append(("先按上面的理解开始；如果不对告诉我：" if lang == "zh" else "Starting with that; tell me if this is wrong: ") + " ".join(it["questions"][:2]))
    th["messages"].append({"role": "assistant", "kind": "run", "text": "\n".join(l for l in lines if l), "job": job_id, "lang": lang, "t": time.time(), "fork": bool(parent),
                           "alternatives": it.get("alternatives") or []})
    _save_thread(tid)
    return {"action": "fork" if parent else "run", "job": job_id}


_formal_jobs: dict[str, threading.Thread] = {}


def _formal_start(job_id: str, job: dict, k: int, fresh_req: bool) -> dict:
    """Return the cached report, or start the check in a thread and return {"status": "running"}."""
    from . import formal
    from .designs import design_for
    best = job["summary"]["best"]
    d = design_for(job["spec"]); ir = d.ir_cls.from_dict(best["ir"]); backend = job["spec"].get("backend", "ecp5")
    fresh = d.emit(ir, backend=backend)
    h = hashlib.sha256(f"{backend}\n{fresh}".encode()).hexdigest()[:16]
    work = RESULTS / job_id / "formal"
    rep = work / "report.json"
    with _lock:
        t = _formal_jobs.get(job_id)
        if t and t.is_alive():
            return {"status": "running", "depth": k, "note": "formal check in progress; call again in a minute"}
        if rep.exists() and not fresh_req:
            try:
                prev = json.loads(rep.read_text())
                if prev.get("rtl_hash") == h and prev.get("depth_requested") == k:
                    return prev
            except ValueError:
                pass

        def run():
            measured_rtl = ROOT / "runs" / h / "rtl" / f"{d.top}.sv"
            out = {"status": "done", "depth": k, "depth_requested": k, "rtl_hash": h, "measured_rtl_present": measured_rtl.exists(), "started": time.time()}
            try:
                if measured_rtl.exists():
                    out["equivalence"] = formal.equivalence(d, ir, measured_rtl.read_text(), fresh, work / "equiv", k=k)
                else:
                    out["equivalence"] = {"ok": None, "log": "generator output differs from the measured RTL hash; no stored copy to compare against"}
                out["properties"] = formal.properties(d, ir, fresh, work / "props", k=k)
            except Exception as e:
                out["error"] = str(e)[:500]
            out["finished"] = time.time()
            work.mkdir(parents=True, exist_ok=True)
            rep.write_text(json.dumps(out, indent=1))

        t = threading.Thread(target=run, daemon=True)
        _formal_jobs[job_id] = t
        t.start()
    return {"status": "running", "depth": k, "note": "formal check started; call again in a minute"}


# ---------------------------------------------------------------- bring-your-own RTL bundles
RTL_DIR = ROOT / "rtl"


def _load_rtl_bundles() -> None:
    from .designs import register_rtl
    if not RTL_DIR.exists():
        return
    for d in sorted(RTL_DIR.iterdir()):
        f = d / "bundle.json"
        if f.exists():
            try:
                register_rtl(json.loads(f.read_text()))
            except Exception as e:
                print(f"rtl bundle {d.name}: {e}", flush=True)


def _rtl_check(bundle: dict) -> dict:
    """Elaborate the user's files + wrapper with iverilog and run the bench once on the behavioral RTL (defaults), so a
    broken upload is refused with the tool's message instead of failing every run."""
    import tempfile
    from . import rtl as _rtl, eda
    from .designs import Design
    A = _rtl.arch_class(bundle)
    a = A.from_dict({})
    text = _rtl.emit(bundle, a)
    w = Path(tempfile.mkdtemp(prefix="sa_rtl_")); (w / "rtl").mkdir(); (w / "rtl" / f"{bundle['top']}.sv").write_text(text)
    d = Design(kind="rtl:check", ir_cls=A, top=bundle["top"], emit=lambda x, backend="ecp5": _rtl.emit(bundle, x, backend), test_module=None,
               sim_env=lambda x: {}, throughput=lambda x: 1.0, throughput_unit="", latency=lambda x: 1, tb=lambda x, period: bundle.get("tb", ""))
    ok, log, _ = eda.simulate(d, a, w, timeout=300)
    return {"ok": ok, "log": log[-2000:], "ports": [{"dir": dd, "type": t, "name": n} for dd, t, n in _rtl._ports(bundle)]}


# ---------------------------------------------------------------- proposals (changes to the generators, gated)
PROPOSALS = ROOT / "proposals"
_proposals: dict[str, dict] = {}
_proposal_threads: dict[str, threading.Thread] = {}


def _load_proposals() -> None:
    if not PROPOSALS.exists():
        return
    for d in sorted(PROPOSALS.iterdir()):
        meta = d / "proposal.json"
        if meta.exists():
            try:
                pr = json.loads(meta.read_text())
                rep = d / "gate" / "report.json"
                if rep.exists():
                    pr["report"] = json.loads(rep.read_text())
                if pr.get("status") == "gating":
                    pr["status"] = "interrupted"
                _proposals[d.name] = pr
            except ValueError:
                pass


def _save_proposal(pid: str) -> None:
    pr = _proposals[pid]
    (PROPOSALS / pid).mkdir(parents=True, exist_ok=True)
    (PROPOSALS / pid / "proposal.json").write_text(json.dumps({k: v for k, v in pr.items() if k != "report"}, ensure_ascii=False, indent=1))


def _proposal_view(pid: str) -> dict:
    pr = _proposals[pid]
    rep = pr.get("report") or {}
    steps = [{"name": st["name"], "ok": st.get("ok"), "seconds": st.get("seconds"), **({"error": st["error"][-400:]} if st.get("error") else {}),
              **({"mutation": st["mutation"]} if "mutation" in st else {}), **({"equivalence": st["equivalence"]} if "equivalence" in st else {}),
              **({"failures": st["failures"]} if st.get("failures") else {}), **({"sims": [x for x in st["sims"] if not x["ok"]]} if st.get("sims") and not st["ok"] else {})}
             for st in rep.get("steps", [])]
    return {"id": pid, "title": pr.get("title"), "rationale": pr.get("rationale"), "status": pr.get("status"), "designs": pr.get("designs"),
            "files": rep.get("files") or pr.get("files"), "diff_lines": rep.get("diff_lines"), "created": pr.get("created"), "thread": pr.get("thread"), "subject": pr.get("subject"),
            "gate_ok": rep.get("ok"), "scope_errors": rep.get("scope_errors"), "apply_error": rep.get("apply_error"), "steps": steps,
            "ab": pr.get("ab"), "merged": pr.get("merged")}


def _start_proposal(pid: str) -> None:
    from . import proposals as P
    pr = _proposals[pid]

    def run():
        try:
            rep = P.gate(pr["diff"], PROPOSALS / pid / "gate", designs=pr["designs"], irs=pr.get("irs") or [], timeout=5400, reference=pr.get("reference"))
        except Exception as e:
            rep = {"ok": False, "steps": [], "log": f"gate crashed: {e}"}
        with _lock:
            pr["report"] = rep
            pr["status"] = "passed" if rep.get("ok") else "failed"
            _save_proposal(pid)
        # a measured A/B for the IRs the proposal names: the patched generator's RTL goes through the real flow as a
        # normal run bound to the proposal's thread, so the chat shows its cards next to the gate verdict
        if rep.get("ok") and pr.get("ab_spec"):
            try:
                spec = dict(pr["ab_spec"]); spec["task"] = f"[proposal {pid}] {spec.get('task', '')}"[:200]
                spec["generator_tree"] = str(PROPOSALS / pid / "gate" / "tree")
                jid = _submit(spec, int(pr.get("ab_max_iter") or 3), pr.get("subject", "cli"), thread=pr.get("thread"))
                with _lock:
                    pr["ab"] = {"job": jid}
                    _save_proposal(pid)
                if pr.get("thread") in _threads:
                    _note_agent_run(pr["thread"], jid, nl._lang(pr.get("title") or ""))
            except Exception as e:
                with _lock:
                    pr["ab"] = {"error": str(e)[:300]}
                    _save_proposal(pid)

    t = threading.Thread(target=run, daemon=True)
    _proposal_threads[pid] = t
    t.start()


_mutation_jobs: dict[str, threading.Thread] = {}


def _mutation_start(job_id: str, job: dict) -> dict:
    """Mutation score of the behavioral testbench on the best design: how many injected RTL bugs it catches. Cached per
    RTL hash under results/<job>/mutation/report.json; {"status": "running"} while the mutants simulate."""
    from . import mutate
    from .designs import design_for
    best = job["summary"]["best"]
    d = design_for(job["spec"]); ir = d.ir_cls.from_dict(best["ir"]); backend = job["spec"].get("backend", "ecp5")
    rtl = d.emit(ir, backend=backend)
    h = hashlib.sha256(f"{backend}\n{rtl}".encode()).hexdigest()[:16]
    work = RESULTS / job_id / "mutation"
    rep = work / "report.json"
    with _lock:
        t = _mutation_jobs.get(job_id)
        if t and t.is_alive():
            return {"status": "running", "note": "mutants are simulating; call again in a minute"}
        if rep.exists():
            try:
                prev = json.loads(rep.read_text())
                if prev.get("rtl_hash") == h:
                    return prev
            except ValueError:
                pass

        def run():
            out = {"status": "done", "rtl_hash": h, "started": time.time()}
            try:
                out.update(mutate.score(d, ir, rtl, work / "mutants", limit=24))
            except Exception as e:
                out["error"] = str(e)[:500]
            out["finished"] = time.time()
            work.mkdir(parents=True, exist_ok=True)
            rep.write_text(json.dumps(out, indent=1))

        t = threading.Thread(target=run, daemon=True)
        _mutation_jobs[job_id] = t
        t.start()
    return {"status": "running", "note": "mutation scoring started; call again in a minute"}


def _export_md(tid: str) -> str:
    th = _threads[tid]
    out = [f"# silicon-agent · {th.get('title') or tid}", "", f"_conversation {tid} · exported {time.strftime('%Y-%m-%d %H:%M')}_", ""]
    for m in th["messages"]:
        t = time.strftime("%H:%M", time.localtime(m.get("t", 0)))
        if m["role"] == "user":
            out += [f"**You** · {t}", "", m["text"], ""]
            continue
        if m.get("kind") == "run" and m.get("job") in _jobs:
            j = _jobs[m["job"]]; sp = j["spec"]; c = sp.get("constraints", {})
            out += [f"**Run `{m['job']}`** · {t} — {sp.get('task', '')}", "",
                    f"- design `{sp.get('design')}` on `{sp.get('backend')}`; constraints: ≥{c.get('fmax_mhz')} MHz" + (f", ≤{c['max_dsp']} DSP" if c.get("max_dsp") is not None else "") + (f", ≤{c['max_lut']} LUT" if c.get("max_lut") is not None else "") + f"; objective {c.get('objective')}",
                    f"- locked: `{json.dumps({k: v for k, v in sp.get('locked', {}).items() if k != 'coefs'})}`", ""]
            res = [e for e in j["events"] if e["kind"] == "result"]
            if res:
                asic = any(e["metrics"].get("area_um2") is not None for e in res)
                out += ["| iter | " + ("cells | area µm² " if asic else "LUT | DSP ") + "| MHz | throughput | verdict | change |", "|---|---|---|---|---|---|---|"]
                for e in res:
                    mm = e["metrics"]
                    it_s = str(e["iteration"]) + ("." + str(e["slot"]) if e.get("slot") else "")
                    res_s = ("%.0f" % mm.get("area_um2", 0)) if asic else str(mm["dsp"])
                    verdict = "feasible" if not e["violations"] else "; ".join(e["violations"])
                    out.append("| %s | %s | %s | %.1f | %s | %s | %s |" % (it_s, mm["lut"], res_s, mm["fmax_mhz"], e["throughput"], verdict, (e.get("action") or "")[:90]))
                out.append("")
            best = (j.get("summary") or {}).get("best")
            if best:
                out += [f"**Best**: iteration {best['iteration']} — {best['metrics']['fmax_mhz']:.1f} MHz, {best['metrics']['lut']} {'cells' if best['metrics'].get('area_um2') else 'LUT'}, {best['metrics']['dsp']} DSP, {best.get('throughput')} {(j.get('summary') or {}).get('throughput_unit', '')}; IR `{json.dumps({k: v for k, v in best['ir'].items() if k != 'coefs'})}`", ""]
            stop = next((e for e in j["events"] if e["kind"] == "plan" and e.get("stop")), None)
            if stop:
                out += [f"_Stopped: {stop.get('stop_reason') or stop.get('action')}_", ""]
            continue
        if m.get("kind") == "proposal":
            pr = _proposals.get(m.get("proposal"))
            if pr:
                v = _proposal_view(m["proposal"])
                out += [f"**Generator change proposal `{m['proposal']}`** · {t} — {v['title']} ({v['status']})", ""] + [f"- {'✓' if st['ok'] else '✕'} {st['name']}" for st in v["steps"]] + [""]
            continue
        if m.get("text"):
            out += [f"**Agent** · {t}" + (" · resumed" if m.get("resumed") else ""), "", m["text"], ""]
            if m.get("usage"):
                u = m["usage"]
                out += [f"_{u.get('calls')} model calls · {(u.get('input') or 0) + (u.get('cache_read') or 0) + (u.get('output') or 0)} tokens · ${(u.get('usd_micros') or 0) / 1e6:.4f} · {u.get('model')}_", ""]
    return "\n".join(out)


def _run_note(spec: dict, lang: str) -> str:
    c = spec.get("constraints", {})
    cons = f"≥{c.get('fmax_mhz')} MHz" + (f" · ≤{c['max_dsp']} DSP" if c.get("max_dsp") is not None else "") + (f" · ≤{c['max_lut']} LUT" if c.get("max_lut") is not None else "") + (f" · ≤{c['max_area_um2']} µm²" if c.get("max_area_um2") is not None else "")
    return (f"开始运行：{spec.get('task', '')}（{spec.get('design')} on {spec.get('backend')}，{cons}）" if lang == "zh"
            else f"Run started: {spec.get('task', '')} ({spec.get('design')} on {spec.get('backend')}, {cons}).")


def _tool_brief(name: str, args) -> str:
    """One short line saying what a tool call is about, for the live trail chip."""
    if not isinstance(args, dict):
        return ""
    if name == "sa_run":
        sp = args.get("spec") or {}
        c = sp.get("constraints") or {}
        bits = [sp.get("design", ""), sp.get("backend", "")]
        if c.get("fmax_mhz"):
            bits.append(f"≥{c['fmax_mhz']} MHz")
        if c.get("max_dsp") is not None:
            bits.append(f"≤{c['max_dsp']} DSP")
        return " · ".join(b for b in bits if b)
    if name == "sa_estimate":
        ir = args.get("ir") or {}
        knobs = " ".join(f"{k}={v}" for k, v in list(ir.items())[:4] if not isinstance(v, (list, dict)))
        return f"{args.get('design', '')} · {args.get('backend', '')} · {knobs}"[:80]
    if name in ("sa_job", "sa_formal", "sa_stop", "sa_steer", "sa_job_file"):
        return " · ".join(str(v) for k, v in args.items() if k in ("job", "path", "depth") and v)[:80]
    if name == "sa_interpret":
        return (args.get("request") or "")[:80]
    if name == "sa_pareto":
        return f"{args.get('design', '')} · {args.get('backend', '')}"
    return ""


def _run_watcher(tid: str, lang: str):
    """Event hook for an AGH turn: keeps the live tool trail on the thread and gives a run the agent starts its
    card the moment sa_run returns (not when the whole turn ends)."""
    th = _threads[tid]
    trail: list[dict] = []
    seen: set[str] = set()

    note = {"text": ""}

    def on_event(kind, upd, meta):
        if kind == "turn_start":
            trail.clear()
            note["text"] = ""
            with _lock:
                th["live"] = []
                th.pop("live_note", None)
        elif kind == "agent_message_chunk":
            # the agent's working narration between tool calls: shown as the status line while it works
            c = upd.get("content") or {}
            if c.get("type") == "text":
                note["text"] = (note["text"] + c.get("text", ""))[-600:]
                with _lock:
                    th["live_note"] = note["text"].strip()
        elif kind == "tool_call":
            # the narration stays on screen while the calls it announced run; the next narration replaces it
            note["text"] = ""
            trail.append({"id": upd.get("toolCallId"), "name": upd.get("title") or upd.get("kind"), "status": upd.get("status", "pending"), "args": _tool_brief(upd.get("title") or "", upd.get("rawInput"))})
            with _lock:
                th["live"] = trail[-40:]
        elif kind == "tool_call_update":
            for t in trail:
                if t["id"] == upd.get("toolCallId"):
                    t["status"] = upd.get("status", t["status"])
            with _lock:
                th["live"] = trail[-40:]
        if kind == "tool_call_update" and (upd.get("title") or "") == "sa_run":
            out = upd.get("_result") or {}
            jid = out.get("id") if isinstance(out, dict) else None
            if jid and jid in _jobs and jid not in seen:
                seen.add(jid)
                _note_agent_run(tid, jid, lang)
    on_event.seen = seen
    return on_event


def _note_agent_run(tid: str, jid: str, lang: str) -> None:
    th = _threads[tid]
    with _lock:
        if any(m.get("kind") == "run" and m.get("job") == jid for m in th["messages"]):
            return
        _jobs[jid]["thread"] = tid
        _save_state(jid)
        th["messages"].append({"role": "assistant", "kind": "run", "text": _run_note(_jobs[jid]["spec"], lang), "job": jid, "lang": lang, "t": time.time(),
                               "fork": bool(_jobs[jid].get("parent")), "source": "agh"})
        _save_thread(tid)


def _record_turn(tid: str, r: dict, lang: str) -> list[str]:
    """Append the agent's reply (and any run it started that the watcher missed) to the thread."""
    th = _threads[tid]
    started: list[str] = []
    for t in r["tools"]:
        if t.get("name") == "sa_run" and isinstance(t.get("result"), dict):
            jid = t["result"].get("id")
            if jid and jid in _jobs:
                started.append(jid)
                _note_agent_run(tid, jid, lang)
    by_id = {t.get("id"): t for t in r["tools"]}
    if r.get("stop") == "cancelled" and _shutting_down.is_set():
        # the harness aborted the turn because we are going down: leave the thread busy and record nothing, so the
        # next start nudges the session to continue from its records (the user sees "resumed", not a dead end)
        with _lock:
            th["interrupted"] = True
            th.pop("live", None); th.pop("live_note", None)
            _save_thread(tid)
        return started
    if r.get("stop") == "cancelled":
        # a cancel from the stop button
        r["text"] = (r["text"] + "\n\n" if r["text"] else "") + ("（这一轮被中止）" if lang == "zh" else "(turn cut short)")
    with _lock:
        th.pop("live", None); th.pop("live_note", None)
        th["messages"].append({"role": "assistant", "kind": "text", "text": r["text"] or ("（没有回复）" if lang == "zh" else "(no reply)"),
                               "job": started[-1] if started else (_thread_jobs(tid) or [None])[-1], "t": time.time(), "source": "agh", **({"resumed": True} if r.get("resumed") else {}),
                               **({"usage": r["usage"]} if r.get("usage") else {}),
                               "tools": [{"name": t.get("name"), "status": t.get("status"), "args": t.get("args")} for t in r["tools"]][:40],
                               "steps": [{"kind": st["kind"], "text": st.get("text", "")[:600], "name": st.get("name"), "status": st.get("status"),
                                          **({"brief": _tool_brief(st.get("name") or "", by_id.get(st.get("id"), {}).get("args"))} if st["kind"] == "tool" else {})}
                                         for st in r.get("steps", [])][:80]})
        if not th["title"] and started:
            th["title"] = _jobs[started[-1]]["spec"].get("task", "")[:120]
        _save_thread(tid)
    return started


def _author_tree(tid: str) -> str:
    """The scratch copy of the package an author session edits: made once per conversation, under SA_ROOT so the
    AGH worker (another container sharing /srv/sa) can read and write it. A git repo, so `git diff` yields the
    proposal; the base commit is the deployed code."""
    from . import proposals as P
    root = ROOT / "author" / tid
    if not (root / "silicon_agent").exists():
        # `a/` is the deployed code, untouched; the author edits `b/`. No git in the containers, so the proposal is
        # `diff -ruN a b` (the gate applies it with patch -p1, so the a/ b/ prefixes are exactly right)
        P.scratch_copy(root / "a"); P.scratch_copy(root / "b")
        (root / "README-author.md").write_text(
            "Scratch copies of silicon_agent/ for one proposal.\n"
            "  a/  the deployed generators (do not edit)\n"
            "  b/  your working copy: edit here, test here\n"
            "Test:    cd b && python3 -c 'from silicon_agent.fir import ...'   (PYTHONPATH is b/)\n"
            "Diff:    diff -ruN a b   -> paths come out as a/silicon_agent/x.py b/silicon_agent/x.py, which is what sa_propose wants\n")
    return str(root)


def _agh_turn(tid: str, text: str, sub: str, resumed: bool = False) -> dict:
    """The same user turn, but run by Agnes Harness: the LLM loop, tool records, approvals and session history
    live in AGH; silicon-agent's capabilities are the `sa_*` tools of the agh-plugin. Runs the agent starts become
    run cards in the thread; the assistant text is AGH's final answer; every tool call is kept on the message so
    the chat can show the trajectory of the agent itself."""
    from . import agh
    th = _threads[tid]
    c = agh.client()
    mode_name = th.get("mode") or agh.DEFAULT_MODE
    mode = agh.MODES.get(mode_name) or agh.MODES[agh.DEFAULT_MODE]
    # author mode runs in its own AGH session whose workspace is a scratch copy of the package (made once per
    # conversation); chat modes share the conversation's session
    author = mode.get("session") == "author"
    skey = "agh_author_session" if author else "agh_session"
    sid = th.get(skey)
    cwd = _author_tree(tid) if author else agh.workspace()
    if sid:
        try:
            c.load_session(sid, cwd)
        except agh.AghError:
            sid = None
    if not sid:
        sid = c.new_session(cwd, preset=mode.get("preset") or os.environ.get("SA_AGH_PRESET") or None,
                            session_key=f"sa-author-{tid}" if author else f"sa-thread-{tid}")
        with _lock:
            th[skey] = sid
            _save_thread(tid)
    # the conversation's agent mode selects the harness model slot; applied before every turn so a session created
    # under one mode follows a later switch
    applied_key = "agh_author_mode_applied" if author else "agh_mode_applied"
    if th.get(applied_key) != mode_name:
        try:
            c.set_model(sid, mode["route"], mode["model"], mode["thinking"])
            with _lock:
                th[applied_key] = mode_name
        except agh.AghError as e:
            print(f"agh: setModel failed for {tid}: {e}", flush=True)
    first = next((m["text"] for m in th["messages"] if m["role"] == "user"), text)
    lang = nl._lang(first)
    # the method itself is injected by the plugin's context hook (sessions keyed sa-thread-*); the bracketed line
    # only carries the thread id the tools need
    # the reply follows the language of this message (a thread opened in English may continue in Chinese)
    prompt = f"[silicon-agent {'author' if author else 'chat'} · thread {tid} · answer in {'Chinese' if nl._lang(text) == 'zh' else 'English'}]\n{text}"
    r = c.prompt(sid, prompt, on_event=_run_watcher(tid, lang), timeout=3600)
    # the harness's ledger for this turn (tokens, estimated cost, wall time): shown under the reply
    r["usage"] = c.usage(sid)
    if resumed:
        r["resumed"] = True
    started = _record_turn(tid, r, lang)
    return {"action": "agh", "jobs": started}


def _chat_turn(tid: str, text: str, sub: str) -> None:
    try:
        from . import agh
        if agh.enabled():
            _agh_turn(tid, text, sub)
        else:
            _handle_chat(tid, text, sub)
    except Exception as e:
        if _shutting_down.is_set() or "connection closed" in str(e) or "connection lost" in str(e):
            # the daemon went away under us (a deploy restarts the agh service): keep the turn busy so the next start
            # resumes it from the session's records instead of ending the conversation with an error
            with _lock:
                _threads[tid]["interrupted"] = True
        else:
            _threads[tid]["messages"].append({"role": "assistant", "kind": "text", "text": f"Something went wrong: {e}", "t": time.time()})
    finally:
        _turn_done(tid)


def _turn_done(tid: str) -> None:
    """End of a turn: the thread is free again, unless a message queued while the agent worked -- then it runs next."""
    th = _threads[tid]
    with _lock:
        th.pop("live", None); th.pop("live_note", None)
        if th.pop("interrupted", None):
            # a shutdown cut the turn: stay busy so _resume_turns() picks it up after the restart
            _save_thread(tid)
            return
        nxt = (th.get("queue") or [None]).pop(0) if th.get("queue") else None
        if nxt:
            th["messages"].append({"role": "user", "kind": "text", "text": nxt["text"], "t": time.time()})
        else:
            th["busy"] = False
        _save_thread(tid)
    if nxt:
        threading.Thread(target=_chat_turn, args=(tid, nxt["text"], nxt["sub"]), daemon=True).start()


# ---------------------------------------------------------------- http
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8", ".sv": "text/plain; charset=utf-8",
        ".log": "text/plain; charset=utf-8"}


class Handler(BaseHTTPRequestHandler):
    server_version = "silicon-agent/0.3"

    # -- auth --
    def _subject(self) -> str | None:
        """Who is calling, or None if unauthenticated."""
        auth = self.headers.get("Authorization", "")
        if TOKEN and hmac.compare_digest(auth, f"Bearer {TOKEN}"):
            return "cli"
        if GATEWAY_KEY:
            sub = self.headers.get("X-Auth-Subject", "")
            sig = self.headers.get("X-Auth-Sig", "")
            groups = self.headers.get("X-Auth-Groups", "")
            if sub and sig and _gateway_sig_ok(sub, groups, sig):
                return sub
        if not TOKEN and not GATEWAY_KEY:
            return "anonymous"  # dev mode: no auth configured at all
        return None

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, cache: str = "no-store") -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        if args and "/health" in str(args[0]):
            return
        super().log_message(fmt, *args)

    # -- routing --
    def do_GET(self):
        u = urlsplit(self.path)
        path = u.path
        if path.startswith("/api/"):
            path = path[4:]
        if path in ("/health", "/healthz"):
            from . import agh
            return self._json(200, {"ok": True, "backends": list(BACKENDS), "designs": list(DESIGNS),
                                    "queued": _queue.qsize(), "jobs": len(_jobs), "runtime": "agh" if agh.enabled() else "builtin",
                                    **({"modes": {k: v["model"] + (f" · thinking {v['thinking']}" if v["thinking"] != "off" else "") for k, v in agh.MODES.items()},
                                        "default_mode": agh.DEFAULT_MODE} if agh.enabled() else {})})
        # static UI (public: sluice gates it before it reaches us)
        if path == "/" or path == "/index.html":
            return self._file(STATIC / "index.html")
        if path == "/work":
            return self._file(STATIC / "work.html")
        if path.startswith("/static/"):
            target = (STATIC / path[len("/static/"):]).resolve()
            if STATIC.resolve() in target.parents and target.is_file():
                return self._file(target, cache="public, max-age=300")
            return self._json(404, {"error": "not found"})

        sub = self._subject()
        if sub is None:
            return self._json(401, {"error": "unauthorized"})
        parts = path.strip("/").split("/")
        if path == "/me":
            return self._json(200, {"subject": sub})
        if path == "/specs":
            out = []
            for f in sorted(SPECS.glob("*.json")):
                s = json.loads(f.read_text())
                out.append({"name": f.stem, "task": s.get("task", ""), "design": s.get("design", "matmul"),
                            "backend": s.get("backend", "ecp5"), "spec": s})
            return self._json(200, out)
        if path == "/threads":
            with _lock:
                rows = [{"id": t["id"], "title": t.get("title") or (t["messages"][0]["text"][:60] if t["messages"] else "untitled"),
                         "created": t["created"], "job": (_thread_jobs(t["id"]) or [None])[-1], "subject": t.get("subject", ""),
                         "busy": bool(t.get("busy")), "n_runs": len(_thread_jobs(t["id"])),
                         "last": max([m.get("t", 0) for m in t["messages"]] + [t["created"]]),
                         "agent": (t.get("live") or [{}])[-1].get("name") if t.get("busy") and t.get("live") else None}
                        for t in _threads.values() if _visible(t, sub)]
            for r in rows:
                j = _jobs.get(r["job"]) if r["job"] else None
                r["status"] = j["status"] if j else None
                r["best"] = (j.get("summary") or {}).get("best") if j else None
                r["design"] = j["spec"].get("design") if j else None
                r["backend"] = j["spec"].get("backend") if j else None
            rows.sort(key=lambda r: r["last"], reverse=True)
            return self._json(200, rows)
        if len(parts) == 3 and parts[0] == "threads" and parts[2] == "export.md":
            # the conversation as Markdown: messages, run specs and results, proposals -- a report to share or file
            th = _threads.get(parts[1])
            if not th or not _visible(th, sub):
                return self._json(404, {"error": "no such thread"})
            data = _export_md(parts[1]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="silicon-agent-{parts[1]}.md"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if len(parts) == 2 and parts[0] == "threads":
            th = _threads.get(parts[1])
            if not th or not _visible(th, sub):
                return self._json(404, {"error": "no such thread"})
            with _lock:
                jobs_in = {jid: _jobs[jid] for jid in _thread_jobs(parts[1])}
                props = {pid: _proposal_view(pid) for pid, pr in _proposals.items() if pr.get("thread") == parts[1]}
            return self._json(200, {**th, "jobs": {jid: {"status": j["status"], "spec": j["spec"], "summary": j.get("summary"), "error": j.get("error"),
                                                        "n_events": len(j["events"]), "max_iter": j.get("max_iter"), "parent": j.get("parent"),
                                                        "created": j["created"], "task": j["spec"].get("task", "")} for jid, j in jobs_in.items()},
                                    "proposals": props})
        if path == "/bench":
            bdir = ROOT / "bench"
            reps = sorted(bdir.glob("*.json")) if bdir.exists() else []
            last = json.loads(reps[-1].read_text()) if reps else None
            status = (bdir / "LAST").read_text().strip() if (bdir / "LAST").exists() else ""
            # the latest report per tag (nightly, agent, planner-pro, knob ...) as a compact table: case -> ok/first/best
            latest: dict = {}
            for r in reps:
                tag = r.name.rsplit("-", 2)[0]
                latest[tag] = r
            by_tag = {}
            for tag, r in latest.items():
                try:
                    rep = json.loads(r.read_text())
                except ValueError:
                    continue
                by_tag[tag] = {"file": r.name, "t": rep.get("t"), "planner": rep.get("planner"), "agent": rep.get("agent"), "summary": rep.get("summary"),
                               "cases": {c["name"]: {"ok": c["ok"], "first": c["first_feasible"], "rounds": c["rounds"], "best": c["best"], "job": c["job"]} for c in rep.get("cases", [])}}
            return self._json(200, {"status": status, "reports": [r.name for r in reps[-10:]], "last": last, "by_tag": by_tag})
        if path == "/pareto":
            from . import pareto
            q = parse_qs(u.query)
            design, backend = q.get("design", ["matmul"])[0], q.get("backend", ["ecp5"])[0]
            with _lock:
                pts = pareto.points(_jobs, design, backend)
            fr = pareto.front(pts)
            want = {k: float(q[k][0]) for k in ("fmax_mhz", "max_dsp", "max_lut") if k in q}
            best = pareto.best_under(pts, want.get("fmax_mhz"), want.get("max_dsp"), want.get("max_lut")) if want else None
            return self._json(200, {"design": design, "backend": backend, "n_points": len(pts), "front": fr, "points": pts, "best_under": best})
        if path == "/schema":
            from .schema import SCHEMAS, OBJECTIVES
            return self._json(200, {"designs": SCHEMAS, "objectives": OBJECTIVES, "backends": {k: v["name"] for k, v in BACKENDS.items()},
                                    "backend_facts": {k: dict(v) for k, v in BACKENDS.items()}})
        if path == "/calibration":
            from . import screen as _screen
            return self._json(200, {f"{d}/{b}": _screen.calibration_report(d, b) for d in DESIGNS for b in BACKENDS})
        if path == "/memory":
            out = []
            for f in sorted(memory.MEM_DIR.glob("*.jsonl")):
                if f.name.startswith("obs_"):
                    continue
                design, backend = f.stem.split("_", 1)
                ls = memory.load(design, backend)
                out.append({"design": design, "backend": backend, "count": len(ls),
                            "lessons": sorted(({**l, "score": memory.score(l)} for l in ls), key=lambda l: -l["score"])})
            return self._json(200, out)
        if path == "/rtl":
            out = []
            for d in sorted(RTL_DIR.iterdir()) if RTL_DIR.exists() else []:
                f = d / "bundle.json"
                if f.exists():
                    b = json.loads(f.read_text())
                    if _visible({"subject": b.get("subject", "")}, sub):
                        out.append({"id": b["id"], "design": f"rtl:{b['id']}", "name": b.get("name"), "top": b["top"], "files": sorted(b["files"]), "params": b.get("params"),
                                    "description": b.get("description", ""), "created": b.get("created"), "check": b.get("check", {}).get("ok")})
            return self._json(200, out)
        if len(parts) == 2 and parts[0] == "rtl":
            f = RTL_DIR / parts[1] / "bundle.json"
            if not f.exists():
                return self._json(404, {"error": "no such bundle"})
            b = json.loads(f.read_text())
            if not _visible({"subject": b.get("subject", "")}, sub):
                return self._json(404, {"error": "no such bundle"})
            return self._json(200, b)
        if path == "/proposals":
            with _lock:
                rows = [_proposal_view(pid) for pid in _proposals]
            rows.sort(key=lambda r: r.get("created") or 0, reverse=True)
            return self._json(200, rows)
        if len(parts) == 2 and parts[0] == "proposals":
            with _lock:
                if parts[1] not in _proposals:
                    return self._json(404, {"error": "no such proposal"})
                v = _proposal_view(parts[1])
                v["diff"] = _proposals[parts[1]].get("diff", "")
            return self._json(200, v)
        if len(parts) == 3 and parts[0] == "proposals" and parts[2] == "report":
            rep = PROPOSALS / parts[1] / "gate" / "report.json"
            if not rep.exists():
                return self._json(404, {"error": "no report yet"})
            return self._json(200, json.loads(rep.read_text()))
        if path == "/jobs":
            # ?thread=<id>: that conversation's runs; ?thread= (empty): runs bound to no conversation; absent: everything
            q = parse_qs(u.query, keep_blank_values=True)
            want = q["thread"][0] if "thread" in q else None
            with _lock:
                rows = [{"id": j, "status": v["status"], "task": v["spec"].get("task", ""),
                         "design": v["spec"].get("design", "matmul"), "backend": v["spec"].get("backend", "ecp5"),
                         "created": v["created"], "subject": v.get("subject", ""), "parent": v.get("parent"), "thread": v.get("thread"),
                         "best": (v.get("summary") or {}).get("best"), "iterations": sum(1 for e in v["events"] if e["kind"] == "result")}
                        for j, v in _jobs.items() if want is None or (v.get("thread") or "") == want]
            rows.sort(key=lambda r: r["created"], reverse=True)
            return self._json(200, rows)
        if len(parts) >= 2 and parts[0] == "jobs":
            job_id = parts[1]
            with _lock:
                job = _jobs.get(job_id)
            if not job:
                return self._json(404, {"error": "no such job"})
            if len(parts) == 2:
                after = int(parse_qs(u.query).get("after", ["0"])[0])
                with _lock:
                    return self._json(200, {"id": job_id, "status": job["status"], "spec": job["spec"],
                                            "events": job["events"][after:], "n_events": len(job["events"]),
                                            "summary": job.get("summary"), "error": job.get("error"),
                                            "created": job["created"], "subject": job.get("subject", ""), "parent": job.get("parent"),
                                            "thread": job.get("thread"), "max_iter": job.get("max_iter")})
            if len(parts) >= 4 and parts[2] == "files":
                rel = "/".join(parts[3:])
                base = (RESULTS / job_id).resolve()
                target = (base / rel).resolve()
                if base not in target.parents or not target.is_file():
                    return self._json(404, {"error": "no such file"})
                return self._file(target)
            if len(parts) == 3 and parts[2] == "rtl":
                # best design's generated RTL, looked up through runs/<hash>/
                return self._rtl(job)
            if len(parts) == 3 and parts[2] == "package":
                from . import deliver
                try:
                    data = deliver.package({**job, "id": job_id}, RESULTS / job_id, ROOT / "runs")
                except ValueError as e:
                    return self._json(404, {"error": str(e)})
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{job["spec"].get("design", "design")}-{job_id}.zip"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if len(parts) == 3 and parts[2] == "formal":
                # the BMC runs in the background (it can take minutes on a deep pipeline); the endpoint returns 202 while
                # it runs and the finished report, cached per generator revision, afterwards
                best = (job.get("summary") or {}).get("best")
                if not best:
                    return self._json(404, {"error": "no feasible design yet"})
                k = int(parse_qs(u.query).get("depth", ["20"])[0])
                fresh_req = "fresh" in parse_qs(u.query)
                st = _formal_start(job_id, job, k, fresh_req)
                return self._json(202 if st.get("status") == "running" else 200, st)
            if len(parts) == 3 and parts[2] == "mutation":
                best = (job.get("summary") or {}).get("best")
                if not best:
                    return self._json(404, {"error": "no feasible design yet"})
                st = _mutation_start(job_id, job)
                return self._json(202 if st.get("status") == "running" else 200, st)
            if len(parts) == 3 and parts[2] == "axis":
                from . import deliver
                from .designs import design_for
                best = (job.get("summary") or {}).get("best")
                if not best:
                    return self._json(404, {"error": "no feasible design yet"})
                d = design_for(job["spec"])
                body = deliver.axis_wrapper(d, d.ir_cls.from_dict(best["ir"])).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        self._json(404, {"error": "not found"})

    def _rtl(self, job: dict) -> None:
        best = (job.get("summary") or {}).get("best")
        if not best:
            return self._json(404, {"error": "no feasible design yet"})
        from .designs import design_for
        from .eda import evaluate  # noqa: F401  (hash scheme lives next to it)
        d = design_for(job["spec"])
        ir = d.ir_cls.from_dict(best["ir"])
        backend = job["spec"].get("backend", "ecp5")
        text = d.emit(ir, backend=backend)
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        sub = self._subject()
        if sub is None:
            return self._json(401, {"error": "unauthorized"})
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            path = path[4:]
        n = int(self.headers.get("Content-Length", "0"))
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] in ("steer", "stop"):
            with _lock:
                job = _jobs.get(parts[1])
                if not job:
                    return self._json(404, {"error": "no such job"})
                if job["status"] not in ("queued", "running"):
                    return self._json(409, {"error": f"job is {job['status']}"})
                if parts[2] == "stop":
                    job["stop_requested"] = True
                    return self._json(200, {"ok": True})
                try:
                    msg = str(json.loads(self.rfile.read(n) or b"{}").get("message", "")).strip()[:2000]
                except json.JSONDecodeError:
                    return self._json(400, {"error": "bad json"})
                if not msg:
                    return self._json(400, {"error": "empty message"})
                job.setdefault("steer", []).append(msg)
                rec = {"t": round(time.time() - job.get("started", time.time()), 1), "kind": "user", "message": msg, "subject": sub}
                job["events"].append(rec)
                with (RESULTS / parts[1] / "events.jsonl").open("a") as f:
                    f.write(json.dumps(rec) + "\n")
                return self._json(200, {"ok": True, "queued_for_iteration": sum(1 for e in job["events"] if e["kind"] == "result") + 2})
        if len(parts) == 3 and parts[0] == "threads" and parts[2] == "stop":
            # stop the agent's turn: cancel the AGH session's prompt and ask any running job of the thread to stop
            th = _threads.get(parts[1])
            if not th or not _visible(th, sub):
                return self._json(404, {"error": "no such thread"})
            stopped = {"turn": False, "jobs": [], "dropped": len(th.get("queue") or [])}
            with _lock:
                # stop means stop: what was waiting behind the turn is dropped too
                th.pop("queue", None)
            for key in ("agh_session", "agh_author_session"):
                if th.get(key):
                    try:
                        from . import agh
                        agh.client().cancel(th[key])
                        stopped["turn"] = True
                    except Exception as e:
                        stopped["error"] = str(e)[:200]
            with _lock:
                for jid in _thread_jobs(parts[1]):
                    j = _jobs.get(jid)
                    if j and j["status"] in ("queued", "running"):
                        j["stop_requested"] = True
                        stopped["jobs"].append(jid)
            return self._json(200, stopped)
        if len(parts) == 3 and parts[0] == "threads" and parts[2] == "mode":
            # the agent mode of a conversation (fast / deep); takes effect from the next turn
            from . import agh
            th = _threads.get(parts[1])
            if not th or not _visible(th, sub):
                return self._json(404, {"error": "no such thread"})
            try:
                mode = str(json.loads(self.rfile.read(n) or b"{}").get("mode", ""))
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            if mode not in agh.MODES:
                return self._json(400, {"error": f"mode must be one of {list(agh.MODES)}"})
            with _lock:
                th["mode"] = mode
                _save_thread(parts[1])
            return self._json(200, {"ok": True, "mode": mode})
        if len(parts) == 3 and parts[0] == "threads" and parts[2] == "title":
            th = _threads.get(parts[1])
            if not th or not _visible(th, sub):
                return self._json(404, {"error": "no such thread"})
            try:
                title = str(json.loads(self.rfile.read(n) or b"{}").get("title", "")).strip()[:120]
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            with _lock:
                th["title"] = title
                _save_thread(parts[1])
            return self._json(200, {"ok": True, "title": title})
        if len(parts) == 3 and parts[0] == "threads" and parts[2] == "chat":
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                text = str(body.get("text", "")).strip()
                # a message carrying attached RTL (fenced blocks) may be long; plain prose is capped
                text = text[:400_000] if "```" in text else text[:4000]
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            if not text:
                return self._json(400, {"error": "empty"})
            tid = parts[1] if parts[1] != "new" else time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
            with _lock:
                if tid in _threads and not _visible(_threads[tid], sub):
                    return self._json(404, {"error": "no such thread"})
                th = _threads.setdefault(tid, {"id": tid, "title": "", "messages": [], "created": time.time(), "subject": sub})
                if th.get("busy"):
                    # the agent is mid-turn: the message waits its turn (the chat shows it as queued) and runs next
                    if len(th.get("queue") or []) >= 5:
                        return self._json(429, {"error": "too many queued messages"})
                    th.setdefault("queue", []).append({"text": text, "sub": sub, "t": time.time()})
                    _save_thread(tid)
                    return self._json(200, {"thread": tid, "queued": len(th["queue"])})
                th["busy"] = True
                th["messages"].append({"role": "user", "kind": "text", "text": text, "t": time.time()})
                _save_thread(tid)
            threading.Thread(target=_chat_turn, args=(tid, text, sub), daemon=True).start()
            return self._json(200, {"thread": tid})
        if path == "/proposals":
            # a change to the generators: gated on a scratch copy (emit, sim, formal equivalence, mutation score), then an
            # optional measured A/B; the diff is merged only by the operator (POST /proposals/<id>/merge from the CLI)
            from . import proposals as P
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            diff = str(body.get("diff", ""))
            if not diff.strip() or len(diff) > 400_000:
                return self._json(400, {"error": "diff missing or too large"})
            errs = P.check_scope(diff)
            if errs:
                return self._json(400, {"error": "out of scope", "scope_errors": errs})
            # a proposal may add a design kind: names not in the registry yet are accepted as new (the gate verifies they
            # exist in the patched tree and asks for an independent reference model)
            designs = [str(d) for d in (body.get("designs") or []) if re.match(r"^[a-z][a-z0-9_]{0,31}$", str(d))]
            if not designs:
                return self._json(400, {"error": f"designs: which generators the change touches (existing: {list(DESIGNS)}; a new kind by its name)"})
            pid = time.strftime("%Y%m%d-%H%M%S") + "-p" + secrets.token_hex(2)
            pr = {"id": pid, "title": str(body.get("title", ""))[:160], "rationale": str(body.get("rationale", ""))[:4000], "diff": diff,
                  "designs": designs, "irs": body.get("irs") or [], "ab_spec": body.get("ab_spec"), "ab_max_iter": body.get("ab_max_iter"),
                  "reference": str(body.get("reference"))[:60000] if body.get("reference") else None,
                  "thread": body.get("thread"), "subject": sub, "created": time.time(), "status": "gating", "files": P._files_in_diff(diff)}
            if pr["ab_spec"] and pr["ab_spec"].get("design") not in designs:
                return self._json(400, {"error": "ab_spec.design must be one of the proposal's designs"})
            with _lock:
                _proposals[pid] = pr
                _save_proposal(pid)
            _start_proposal(pid)
            if pr["thread"] in _threads:
                th = _threads[pr["thread"]]
                with _lock:
                    th["messages"].append({"role": "assistant", "kind": "proposal", "proposal": pid, "text": pr["title"], "t": time.time(), "source": "agh"})
                    _save_thread(pr["thread"])
            return self._json(200, {"id": pid, "status": "gating", "note": "the gate takes 1-10 minutes; poll GET /proposals/<id>"})
        if path == "/rtl":
            # upload a bring-your-own-RTL bundle: files + a self-checking SV bench + the parameters the planner may move
            from . import rtl as _rtl
            from .designs import register_rtl
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            files = body.get("files") or {}
            if not isinstance(files, dict) or not files or not all(isinstance(v, str) for v in files.values()):
                return self._json(400, {"error": "files: {name: text} of SystemVerilog sources"})
            if sum(len(v) for v in files.values()) > 2_000_000:
                return self._json(400, {"error": "bundle too large (2 MB)"})
            top = str(body.get("top", "")).strip()
            if not re.match(r"^[A-Za-z_]\w*$", top):
                return self._json(400, {"error": "top: the top module name"})
            tb = body.get("tb")
            if not isinstance(tb, str) or "RESULT" not in tb:
                return self._json(400, {"error": "tb: a self-checking SystemVerilog bench that instantiates the top and prints RESULT PASS or RESULT FAIL"})
            params = body.get("params") or {}
            if not isinstance(params, dict) or any(not isinstance(v, dict) for v in params.values()):
                return self._json(400, {"error": "params: {NAME: {default, min, max | choices, hint}}"})
            bid = time.strftime("%Y%m%d-%H%M%S") + "-r" + secrets.token_hex(2)
            bundle = {"id": bid, "name": str(body.get("name") or top)[:120], "top": top, "files": files, "tb": tb, "params": params,
                      "port_widths": body.get("port_widths") or {}, "latency": body.get("latency"), "throughput": body.get("throughput"),
                      "throughput_unit": body.get("throughput_unit", "items/cycle"), "description": str(body.get("description", ""))[:2000],
                      "notes": str(body.get("notes", ""))[:4000], "subject": sub, "created": time.time()}
            bundle["hash"] = _rtl.bundle_hash(bundle)
            try:
                chk = _rtl_check(bundle)
            except Exception as e:
                return self._json(400, {"error": f"bundle check failed: {e}"})
            if not chk["ok"]:
                return self._json(400, {"error": "the bench does not pass on the behavioral RTL with default parameters", "log": chk["log"], "ports": chk["ports"]})
            bundle["check"] = chk
            (RTL_DIR / bid).mkdir(parents=True, exist_ok=True)
            (RTL_DIR / bid / "bundle.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=1))
            register_rtl(bundle)
            return self._json(200, {"id": bid, "design": f"rtl:{bid}", "ports": chk["ports"], "params": params,
                                    "note": f"use design \"rtl:{bid}\" in a spec; locked = {{}}; initial_ir = parameter values"})
        if path == "/author/check":
            # the author's inner loop: emit + elaborate + behavioral sim of one IR on the conversation's working copy
            from . import proposals as P
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            tid = str(body.get("thread", ""))
            if tid not in _threads or not _visible(_threads[tid], sub):
                return self._json(404, {"error": "no such thread"})
            design = body.get("design"); backend = body.get("backend", "ecp5")
            if design not in DESIGNS or backend not in BACKENDS:
                return self._json(400, {"error": f"design/backend: have {list(DESIGNS)} / {list(BACKENDS)}"})
            tree = Path(_author_tree(tid)) / "b"
            return self._json(200, P.quick_check(tree, design, backend, body.get("ir") or {}))
        if len(parts) == 3 and parts[0] == "proposals" and parts[2] == "merge":
            # operator only (token identity): records the decision; the actual `git apply` is the deploy script's job
            if sub != "cli":
                return self._json(403, {"error": "merge is an operator action"})
            with _lock:
                pr = _proposals.get(parts[1])
                if not pr:
                    return self._json(404, {"error": "no such proposal"})
                if pr.get("status") != "passed":
                    return self._json(409, {"error": f"proposal is {pr.get('status')}, not passed"})
                pr["merged"] = {"t": time.time(), "by": sub}
                _save_proposal(parts[1])
            return self._json(200, {"ok": True, "diff_path": str(PROPOSALS / parts[1] / "gate" / "tree" / "proposal.diff")})
        if path == "/interpret":
            # the NL interpreter alone: spec + assumptions + alternatives, no run (the AGH plugin drives runs separately)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                text = str(body.get("text", "")).strip()[:4000]
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            if not text:
                return self._json(400, {"error": "empty"})
            ctx = None
            tid = body.get("thread")
            if tid and tid in _threads:
                ctx = _thread_context(_threads[tid])
            try:
                return self._json(200, nl.interpret(text, ctx))
            except Exception as e:
                return self._json(500, {"error": f"interpreter failed: {e}"})
        if path == "/estimate":
            # cheap screening estimate + validation for the form / the agent, no EDA. Accepts either a full
            # {"spec": {...}} or a bare {"design", "backend", "ir"[, "constraints"]}.
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                from . import screen as _screen
                from .ir import Constraints as _C
                if "spec" in body:
                    spec = body["spec"]
                    design = spec.get("design", "matmul"); backend = spec.get("backend", "ecp5")
                    ir = DESIGNS[design].ir_cls.from_dict({**spec.get("initial_ir", {}), **spec.get("locked", {})})
                    cons = _C.from_dict(spec["constraints"])
                else:
                    design = body.get("design", "matmul"); backend = body.get("backend", "ecp5")
                    if design not in DESIGNS or backend not in BACKENDS:
                        return self._json(400, {"error": f"unknown design/backend: have {list(DESIGNS)} / {list(BACKENDS)}"})
                    ir = DESIGNS[design].ir_cls.from_dict(body.get("ir") or {})
                    cons = _C.from_dict(body.get("constraints") or {"fmax_mhz": 1})
                errs = ir.validate()
                if errs:
                    return self._json(200, {"valid": False, "errors": errs})
                ranked = _screen.screen(design, backend, [{"ir": ir.to_dict(), "why": ""}], cons)
                return self._json(200, {"valid": True, "estimate": ranked[0]["estimate"], "est_violations": ranked[0]["est_violations"],
                                        "throughput": DESIGNS[design].throughput(ir), "latency_cycles": DESIGNS[design].latency(ir)})
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
                return self._json(400, {"error": f"bad request: {e}"})
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "fork":
            with _lock:
                src = _jobs.get(parts[1])
            if not src:
                return self._json(404, {"error": "no such job"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            spec = json.loads(json.dumps(src["spec"]))
            # start the fork from a chosen iteration's design (default: the best feasible, else the last)
            results = [e for e in src["events"] if e["kind"] == "result"]
            pick = None
            if body.get("from_iteration") is not None:
                pick = next((e for e in results if e["iteration"] == int(body["from_iteration"])), None)
            elif src.get("summary", {}).get("best"):
                pick = {"ir": src["summary"]["best"]["ir"]}
            elif results:
                pick = results[-1]
            if pick:
                full_ir = pick["ir"]
                # events carry the planner-visible IR (locked fields stripped); re-add them
                spec["initial_ir"] = {**spec.get("initial_ir", {}), **{k: v for k, v in full_ir.items() if k != "coefs"}}
            for k, v in (body.get("spec_overrides") or {}).items():
                if k == "constraints" and isinstance(v, dict):
                    spec["constraints"] = {**spec["constraints"], **v}
                elif k in ("task", "locked", "initial_ir", "no_memory", "backend"):
                    spec[k] = v if k != "initial_ir" else {**spec.get("initial_ir", {}), **v}
            spec["task"] = body.get("task") or spec.get("task", "") + " (fork)"
            max_iter = max(1, min(int(body.get("max_iter", src.get("max_iter") or 8)), 20))
            job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
            with _lock:
                _jobs[job_id] = {"spec": spec, "max_iter": max_iter, "status": "queued", "events": [], "created": time.time(),
                                 "subject": sub, "parent": {"id": parts[1], "iteration": pick.get("iteration") if pick else None},
                                 "thread": body.get("thread") or src.get("thread")}
                (RESULTS / job_id).mkdir(parents=True, exist_ok=True)
                _write_atomic(RESULTS / job_id / "spec.json", json.dumps(spec, indent=2))
                _save_state(job_id)
            _queue.put(job_id)
            tid = body.get("thread") or src.get("thread")
            if tid:
                c = spec["constraints"]
                cons = f"≥{c['fmax_mhz']} MHz" + (f" · ≤{c['max_dsp']} DSP" if c.get("max_dsp") is not None else "") + (f" · ≤{c['max_lut']} LUT" if c.get("max_lut") is not None else "")
                frm = f"iteration {pick['iteration']}" if pick and pick.get("iteration") else "the best design"
                _thread_note_job(tid, job_id, f"Forked in the worktable from {frm} of the previous run with {cons}.", True)
            return self._json(202, {"id": job_id, "spec": spec})
        if len(parts) == 4 and parts[0] == "memory" and parts[3] == "vote":
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                delta = 1 if body.get("vote") == "up" else -1
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            l = memory.update(parts[1], parts[2], body.get("id", ""), votes=delta)
            return self._json(200 if l else 404, {"ok": bool(l), "score": memory.score(l) if l else None})
        if path != "/jobs":
            return self._json(404, {"error": "not found"})
        if n > 1_000_000:
            return self._json(413, {"error": "spec too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            spec = body["spec"]
            backend = spec.get("backend", "ecp5")
            if backend not in BACKENDS:
                return self._json(400, {"error": f"unknown backend {backend!r}; have {list(BACKENDS)}"})
            design = spec.get("design", "matmul")
            if design not in DESIGNS:
                return self._json(400, {"error": f"unknown design {design!r}; have {list(DESIGNS)}"})
            max_iter = max(1, min(int(body.get("max_iter", 8)), 20))
            # validate the initial IR now so the UI gets an immediate error instead of a failed job
            ir = DESIGNS[design].ir_cls.from_dict({**spec.get("initial_ir", {}), **spec.get("locked", {})})
            errs = ir.validate()
            if errs:
                return self._json(400, {"error": "invalid initial_ir: " + "; ".join(errs)})
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
            return self._json(400, {"error": f"bad request: {e}"})
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        thread = body.get("thread")
        if thread and thread not in _threads:
            return self._json(404, {"error": "no such thread"})
        via_agent = bool(thread) and _threads[thread].get("busy") and os.environ.get("SA_AGH") == "1"
        with _lock:
            _jobs[job_id] = {"spec": spec, "max_iter": max_iter, "status": "queued", "events": [],
                             "created": time.time(), "subject": sub, "parent": body.get("parent"), "thread": thread}
            (RESULTS / job_id).mkdir(parents=True, exist_ok=True)
            _write_atomic(RESULTS / job_id / "spec.json", json.dumps(spec, indent=2))
            _save_state(job_id)
        _queue.put(job_id)
        if thread and not via_agent:
            c = spec.get("constraints", {})
            cons = f"≥{c.get('fmax_mhz')} MHz" + (f" · ≤{c['max_dsp']} DSP" if c.get("max_dsp") is not None else "") + (f" · ≤{c['max_lut']} LUT" if c.get("max_lut") is not None else "") + (f" · ≤{c['max_area_um2']} µm²" if c.get("max_area_um2") is not None else "")
            _thread_note_job(thread, job_id, f"Started in the worktable: {spec.get('task', '')} ({spec.get('design', 'matmul')} on {spec.get('backend', 'ecp5')}, {cons}).", False)
        self._json(202, {"id": job_id})

    def do_DELETE(self):
        sub = self._subject()
        if sub is None:
            return self._json(401, {"error": "unauthorized"})
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            path = path[4:]
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "threads":
            th = _threads.get(parts[1])
            if not th or not _visible(th, sub):
                return self._json(404, {"error": "no such thread"})
            if any(_jobs[j]["status"] in ("queued", "running") for j in _thread_jobs(parts[1])):
                return self._json(409, {"error": "stop the running job first"})
            _threads.pop(parts[1], None)
            (THREADS / f"{parts[1]}.json").unlink(missing_ok=True)
            with _lock:
                for jid, j in _jobs.items():
                    if j.get("thread") == parts[1]:
                        j["thread"] = None
                        _save_state(jid)
            return self._json(200, {"ok": True})
        if len(parts) == 2 and parts[0] == "jobs":
            with _lock:
                job = _jobs.get(parts[1])
                if not job:
                    return self._json(404, {"error": "no such job"})
                if job["status"] in ("running",):
                    return self._json(409, {"error": "stop the job before deleting it"})
                job["status"] = "deleted"
                del _jobs[parts[1]]
            target = (RESULTS / parts[1]).resolve()
            if RESULTS.resolve() in target.parents and target.is_dir():
                shutil.rmtree(target)
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})


def main():
    host = os.environ.get("SA_HOST", "127.0.0.1")
    port = int(os.environ.get("SA_PORT", "8765"))
    RESULTS.mkdir(parents=True, exist_ok=True)
    _load_rtl_bundles()
    _load_from_disk()
    _load_threads()
    _load_proposals()
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_resume_turns, daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"silicon-agent server on http://{host}:{port}  root={ROOT}  backends={list(BACKENDS)} designs={list(DESIGNS)}  "
          f"jobs={len(_jobs)}  auth={'token' if TOKEN else ''}{'+gateway' if GATEWAY_KEY else ''}{'none' if not TOKEN and not GATEWAY_KEY else ''}", flush=True)

    def on_term(signum, frame):
        # stop between iterations; the running job is left queued and resumes from cache on restart
        print("SIGTERM: finishing the current iteration, then exiting", flush=True)
        _shutting_down.set()
        with _lock:
            for jid, j in _jobs.items():
                if j["status"] in ("running", "queued"):
                    j["status"] = "queued"
                    _save_state(jid)
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    srv.serve_forever()
    _worker_idle.wait(timeout=1800)  # let the in-flight iteration finish so its EDA result lands in the cache
    sys.exit(0)


if __name__ == "__main__":
    main()
