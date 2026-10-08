"""Job server: runs design loops on the EDA host and exposes them over HTTP,
plus the single-page web UI.

API
  POST /api/jobs                 {"spec": {...}, "max_iter": 8}      -> {"id": ...}
  GET  /api/jobs                 list (newest first), rebuilt from results/ on start
  GET  /api/jobs/<id>            {"status", "events": [...], "summary"}  (?after=N incremental)
  GET  /api/jobs/<id>/files/<p>  any artifact under results/<id>/
  GET  /api/specs                example specs shipped in specs/
  GET  /api/health
  POST /api/jobs/<id>/steer      {"message": "..."}  -> read by the planner before its next proposal
  POST /api/jobs/<id>/stop
  GET  /api/memory               lessons per (design, backend), scored
  POST /api/memory/<d>/<b>/vote  {"id": ..., "vote": "up"|"down"}
  GET  /                         web UI (static, no build step)

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

from . import memory
from .designs import DESIGNS
from .eda import ROOT, BACKENDS
from .loop import run_loop

RESULTS = ROOT / "results"
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
        state = json.loads((d / "state.json").read_text()) if (d / "state.json").exists() else {}
        job = {"spec": spec, "max_iter": state.get("max_iter", 0), "status": "interrupted", "events": [], "created": d.stat().st_mtime,
               "subject": state.get("subject", ""), "parent": state.get("parent")}
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
    (RESULTS / job_id / "state.json").write_text(json.dumps({"status": job["status"], "max_iter": job["max_iter"],
                                                             "subject": job.get("subject", ""), "parent": job.get("parent")}))


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



def _gateway_sig_ok(subject: str, groups: str, sig: str) -> bool:
    now = int(time.time()) // 60
    for window in (now, now - 1):
        msg = f"{subject}\n{groups}\n{window}".encode()
        want = hmac.new(GATEWAY_KEY.encode(), msg, hashlib.sha256).hexdigest()
        if hmac.compare_digest(want, sig.lower()):
            return True
    return False


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
                                    "queued": _queue.qsize(), "jobs": len(_jobs)})
        # static UI (public: sluice gates it before it reaches us)
        if path == "/" or path == "/index.html":
            return self._file(STATIC / "index.html")
        if path.startswith("/static/"):
            target = (STATIC / path[len("/static/"):]).resolve()
            if STATIC.resolve() in target.parents and target.is_file():
                return self._file(target, cache="public, max-age=300")
            return self._json(404, {"error": "not found"})

        sub = self._subject()
        if sub is None:
            return self._json(401, {"error": "unauthorized"})
        if path == "/me":
            return self._json(200, {"subject": sub})
        if path == "/specs":
            out = []
            for f in sorted(SPECS.glob("*.json")):
                s = json.loads(f.read_text())
                out.append({"name": f.stem, "task": s.get("task", ""), "design": s.get("design", "matmul"),
                            "backend": s.get("backend", "ecp5"), "spec": s})
            return self._json(200, out)
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
            with _lock:
                rows = [{"id": j, "status": v["status"], "task": v["spec"].get("task", ""),
                         "design": v["spec"].get("design", "matmul"), "backend": v["spec"].get("backend", "ecp5"),
                         "created": v["created"], "subject": v.get("subject", ""), "parent": v.get("parent"),
                         "best": (v.get("summary") or {}).get("best"), "iterations": sum(1 for e in v["events"] if e["kind"] == "result")}
                        for j, v in _jobs.items()]
            rows.sort(key=lambda r: r["created"], reverse=True)
            return self._json(200, rows)
        parts = path.strip("/").split("/")
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
                                            "created": job["created"], "subject": job.get("subject", ""), "parent": job.get("parent"), "max_iter": job.get("max_iter")})
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
                                 "subject": sub, "parent": {"id": parts[1], "iteration": pick.get("iteration") if pick else None}}
                (RESULTS / job_id).mkdir(parents=True, exist_ok=True)
                (RESULTS / job_id / "spec.json").write_text(json.dumps(spec, indent=2))
                _save_state(job_id)
            _queue.put(job_id)
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
        with _lock:
            _jobs[job_id] = {"spec": spec, "max_iter": max_iter, "status": "queued", "events": [],
                             "created": time.time(), "subject": sub, "parent": body.get("parent")}
            (RESULTS / job_id).mkdir(parents=True, exist_ok=True)
            (RESULTS / job_id / "spec.json").write_text(json.dumps(spec, indent=2))
            _save_state(job_id)
        _queue.put(job_id)
        self._json(202, {"id": job_id})

    def do_DELETE(self):
        sub = self._subject()
        if sub is None:
            return self._json(401, {"error": "unauthorized"})
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            path = path[4:]
        parts = path.strip("/").split("/")
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
