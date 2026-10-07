"""Job server: runs design loops on the EDA host and exposes them over HTTP,
plus the single-page web UI.

API
  POST /api/jobs                 {"spec": {...}, "max_iter": 8}      -> {"id": ...}
  GET  /api/jobs                 list (newest first), rebuilt from results/ on start
  GET  /api/jobs/<id>            {"status", "events": [...], "summary"}  (?after=N incremental)
  GET  /api/jobs/<id>/files/<p>  any artifact under results/<id>/
  GET  /api/specs                example specs shipped in specs/
  GET  /api/health
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
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

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


# ---------------------------------------------------------------- persistence
def _load_from_disk() -> None:
    """Rebuild the job table from results/<id>/ so restarts don't lose history."""
    if not RESULTS.exists():
        return
    for d in sorted(RESULTS.iterdir()):
        if not d.is_dir() or not (d / "spec.json").exists():
            continue
        spec = json.loads((d / "spec.json").read_text())
        job = {"spec": spec, "max_iter": 0, "status": "interrupted", "events": [], "created": d.stat().st_mtime,
               "subject": ""}
        summ = d / "summary.json"
        if summ.exists():
            s = json.loads(summ.read_text())
            job["summary"] = {k: v for k, v in s.items() if k != "history"}
            job["status"] = "done" if s.get("best") else "failed"
            job["events"] = _events_from_history(s.get("history", []))
        else:
            iters = sorted(d.glob("iter_*.json"))
            job["events"] = _events_from_history([json.loads(f.read_text()) for f in iters])
        _jobs[d.name] = job


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
        job_id = _queue.get()
        with _lock:
            job = _jobs[job_id]
            job["status"] = "running"
            job["started"] = time.time()
        try:
            def on_event(ev: dict):
                with _lock:
                    job["events"].append({"t": round(time.time() - job["started"], 1), **ev})

            summary = run_loop(job["spec"], max_iter=job["max_iter"], out_dir=RESULTS / job_id, on_event=on_event)
            with _lock:
                job["summary"] = {k: v for k, v in summary.items() if k != "history"}
                job["status"] = "done" if summary["best"] else "failed"
        except Exception as e:  # keep the server alive; surface the error to the client
            with _lock:
                job["status"] = "error"
                job["error"] = f"{e}\n{traceback.format_exc()[-2000:]}"
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
        if path == "/jobs":
            with _lock:
                rows = [{"id": j, "status": v["status"], "task": v["spec"].get("task", ""),
                         "design": v["spec"].get("design", "matmul"), "backend": v["spec"].get("backend", "ecp5"),
                         "created": v["created"], "subject": v.get("subject", ""),
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
                                            "created": job["created"], "subject": job.get("subject", "")})
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
        if path != "/jobs":
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", "0"))
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
                             "created": time.time(), "subject": sub}
        _queue.put(job_id)
        self._json(202, {"id": job_id})


def main():
    host = os.environ.get("SA_HOST", "127.0.0.1")
    port = int(os.environ.get("SA_PORT", "8765"))
    RESULTS.mkdir(parents=True, exist_ok=True)
    _load_from_disk()
    threading.Thread(target=_worker, daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"silicon-agent server on http://{host}:{port}  root={ROOT}  backends={list(BACKENDS)} designs={list(DESIGNS)}  "
          f"jobs={len(_jobs)}  auth={'token' if TOKEN else ''}{'+gateway' if GATEWAY_KEY else ''}{'none' if not TOKEN and not GATEWAY_KEY else ''}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
