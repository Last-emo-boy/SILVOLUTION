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
  GET  /api/specs                example specs shipped in specs/
  GET  /api/health
  POST /api/interpret            {"text": "...", "thread"?: id} -> spec + assumptions + alternatives (no run)
  POST /api/estimate             {"spec"} or {"design","backend","ir"} -> screening estimate, no EDA
  POST /api/jobs/<id>/steer      {"message": "..."}  -> read by the planner before its next proposal
  POST /api/jobs/<id>/stop
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
import secrets
import shutil
import signal
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
        spec = json.loads((d / "spec.json").read_text())
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
        _threads[f.stem]["busy"] = False
        for m in _threads[f.stem]["messages"]:
            m.pop("narrating", None)
    # jobs the chat started before thread binding was persisted: bind them from the message log
    for tid, th in _threads.items():
        for m in th["messages"]:
            j = _jobs.get(m.get("job") or "")
            if j and not j.get("thread"):
                j["thread"] = tid
                _save_state(m["job"])
    for tid in _threads:
        threading.Thread(target=_narrate_if_done, args=(tid,), daemon=True).start()


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


def _submit(spec: dict, max_iter: int, sub: str, parent: dict | None = None, thread: str | None = None) -> str:
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    with _lock:
        _jobs[job_id] = {"spec": spec, "max_iter": max_iter, "status": "queued", "events": [], "created": time.time(), "subject": sub,
                         "parent": parent, "thread": thread}
        (RESULTS / job_id).mkdir(parents=True, exist_ok=True)
        (RESULTS / job_id / "spec.json").write_text(json.dumps(spec, indent=2))
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


def _run_note(spec: dict, lang: str) -> str:
    c = spec.get("constraints", {})
    cons = f"≥{c.get('fmax_mhz')} MHz" + (f" · ≤{c['max_dsp']} DSP" if c.get("max_dsp") is not None else "") + (f" · ≤{c['max_lut']} LUT" if c.get("max_lut") is not None else "")
    return (f"开始运行：{spec.get('task', '')}（{spec.get('design')} on {spec.get('backend')}，{cons}）" if lang == "zh"
            else f"Run started: {spec.get('task', '')} ({spec.get('design')} on {spec.get('backend')}, {cons}).")


def _agh_turn(tid: str, text: str, sub: str) -> dict:
    """The same user turn, but run by Agnes Harness: the LLM loop, tool records, approvals and session history
    live in AGH; silicon-agent's capabilities are the `sa_*` tools of the agh-plugin. Runs the tools started
    (sa_run) become run cards in the thread; the assistant text is AGH's final answer; every tool call is kept
    on the message so the chat can show the trajectory of the agent itself."""
    from . import agh
    th = _threads[tid]
    c = agh.client()
    sid = th.get("agh_session")
    if sid:
        try:
            c.load_session(sid, agh.workspace())
        except agh.AghError:
            sid = None
    if not sid:
        sid = c.new_session(agh.workspace(), session_key=f"sa-thread-{tid}")
        with _lock:
            th["agh_session"] = sid
            _save_thread(tid)
    first = next((m["text"] for m in th["messages"] if m["role"] == "user"), text)
    lang = nl._lang(first)
    # the Skill is preloaded only when named; the bracketed line is context for the agent, not part of the user's words
    prompt = (f"[silicon-agent chat · thread {tid} · use the `silicon-agent` skill · the chat renders the spec, every iteration "
              f"and the result as cards beside your reply, so answer in a few plain sentences, no headings or tables]\n{text}")
    started: list[str] = []
    seen: set[str] = set()

    trail: list[dict] = []

    def on_event(kind, upd, meta):
        # the live tool trail: the chat shows what the agent is doing while the turn runs
        if kind == "tool_call":
            trail.append({"id": upd.get("toolCallId"), "name": upd.get("title") or upd.get("kind"), "status": upd.get("status", "pending")})
            with _lock:
                th["live"] = trail[-40:]
        elif kind == "tool_call_update":
            for t in trail:
                if t["id"] == upd.get("toolCallId"):
                    t["status"] = upd.get("status", t["status"])
            with _lock:
                th["live"] = trail[-40:]
        # a run started mid-turn gets its card immediately, not when the whole turn ends
        if kind == "tool_call_update" and (upd.get("title") or "") == "sa_run":
            out = upd.get("_result") or {}
            jid = out.get("id") if isinstance(out, dict) else None
            if jid and jid in _jobs and jid not in seen:
                seen.add(jid)
                started.append(jid)
                with _lock:
                    _jobs[jid]["thread"] = tid
                    _save_state(jid)
                    th["messages"].append({"role": "assistant", "kind": "run", "text": _run_note(_jobs[jid]["spec"], lang), "job": jid, "lang": lang, "t": time.time(),
                                           "fork": bool(_jobs[jid].get("parent")), "source": "agh"})
                    _save_thread(tid)

    r = c.prompt(sid, prompt, on_event=on_event, timeout=3600)
    if r.get("stop") == "cancelled":
        r["text"] = (r["text"] + "\n\n" if r["text"] else "") + ("（已停止）" if lang == "zh" else "(stopped)")
    for t in r["tools"]:
        if t.get("name") == "sa_run" and isinstance(t.get("result"), dict):
            jid = t["result"].get("id")
            if jid and jid in _jobs and jid not in seen:
                seen.add(jid); started.append(jid)
                with _lock:
                    _jobs[jid]["thread"] = tid
                    _save_state(jid)
                    th["messages"].append({"role": "assistant", "kind": "run", "text": _run_note(_jobs[jid]["spec"], lang), "job": jid, "lang": lang, "t": time.time(), "source": "agh"})
    with _lock:
        th.pop("live", None)
        th["messages"].append({"role": "assistant", "kind": "text", "text": r["text"] or ("（没有回复）" if lang == "zh" else "(no reply)"),
                               "job": started[-1] if started else None, "t": time.time(), "source": "agh",
                               "tools": [{"name": t.get("name"), "status": t.get("status"), "args": t.get("args")} for t in r["tools"]][:40]})
        if not th["title"] and started:
            th["title"] = _jobs[started[-1]]["spec"].get("task", "")[:120]
        _save_thread(tid)
    return {"action": "agh", "jobs": started}


def _chat_turn(tid: str, text: str, sub: str) -> None:
    try:
        from . import agh
        if agh.enabled():
            _agh_turn(tid, text, sub)
        else:
            _handle_chat(tid, text, sub)
    except Exception as e:
        _threads[tid]["messages"].append({"role": "assistant", "kind": "text", "text": f"Something went wrong: {e}", "t": time.time()})
    finally:
        with _lock:
            _threads[tid]["busy"] = False
            _threads[tid].pop("live", None)
            _save_thread(tid)


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
            return self._json(200, {"ok": True, "backends": list(BACKENDS), "designs": list(DESIGNS),
                                    "queued": _queue.qsize(), "jobs": len(_jobs), "runtime": "agh" if os.environ.get("SA_AGH") == "1" else "builtin"})
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
                         "created": t["created"], "job": (_thread_jobs(t["id"]) or [None])[-1], "subject": t.get("subject", "")}
                        for t in _threads.values() if _visible(t, sub)]
            for r in rows:
                j = _jobs.get(r["job"]) if r["job"] else None
                r["status"] = j["status"] if j else None
                r["best"] = (j.get("summary") or {}).get("best") if j else None
            rows.sort(key=lambda r: r["created"], reverse=True)
            return self._json(200, rows)
        if len(parts) == 2 and parts[0] == "threads":
            th = _threads.get(parts[1])
            if not th or not _visible(th, sub):
                return self._json(404, {"error": "no such thread"})
            with _lock:
                jobs_in = {jid: _jobs[jid] for jid in _thread_jobs(parts[1])}
            return self._json(200, {**th, "jobs": {jid: {"status": j["status"], "spec": j["spec"], "summary": j.get("summary"), "error": j.get("error"),
                                                        "n_events": len(j["events"]), "max_iter": j.get("max_iter"), "parent": j.get("parent"),
                                                        "created": j["created"], "task": j["spec"].get("task", "")} for jid, j in jobs_in.items()}})
        if path == "/bench":
            bdir = ROOT / "bench"
            reps = sorted(bdir.glob("*.json")) if bdir.exists() else []
            last = json.loads(reps[-1].read_text()) if reps else None
            status = (bdir / "LAST").read_text().strip() if (bdir / "LAST").exists() else ""
            return self._json(200, {"status": status, "reports": [r.name for r in reps[-10:]], "last": last})
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
            stopped = {"turn": False, "jobs": []}
            if th.get("agh_session"):
                try:
                    from . import agh
                    agh.client().cancel(th["agh_session"])
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
                text = str(body.get("text", "")).strip()[:4000]
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
                    return self._json(409, {"error": "still thinking"})
                th["busy"] = True
                th["messages"].append({"role": "user", "kind": "text", "text": text, "t": time.time()})
                _save_thread(tid)
            threading.Thread(target=_chat_turn, args=(tid, text, sub), daemon=True).start()
            return self._json(200, {"thread": tid})
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
                (RESULTS / job_id / "spec.json").write_text(json.dumps(spec, indent=2))
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
            (RESULTS / job_id / "spec.json").write_text(json.dumps(spec, indent=2))
            _save_state(job_id)
        _queue.put(job_id)
        if thread and not via_agent:
            c = spec.get("constraints", {})
            cons = f"≥{c.get('fmax_mhz')} MHz" + (f" · ≤{c['max_dsp']} DSP" if c.get("max_dsp") is not None else "") + (f" · ≤{c['max_lut']} LUT" if c.get("max_lut") is not None else "")
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
    _load_from_disk()
    _load_threads()
    threading.Thread(target=_worker, daemon=True).start()
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
