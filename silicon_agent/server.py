"""Job server: runs design loops on the EDA host and exposes them over HTTP.

POST /jobs            {"spec": {...}, "max_iter": 8}            -> {"id": ...}
GET  /jobs            list
GET  /jobs/<id>       {"status", "events": [...], "summary"}   (?after=N for incremental events)
GET  /jobs/<id>/files/<path>   any artifact under results/<id>/ (text/JSON)
GET  /health

One job runs at a time per server (EDA tools are CPU-bound); others queue.
Auth: optional bearer token via SA_TOKEN. Bind to 127.0.0.1 and tunnel over SSH,
or put it behind the reverse proxy.
"""

from __future__ import annotations

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
TOKEN = os.environ.get("SA_TOKEN", "")

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_queue: queue.Queue[str] = queue.Queue()


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


class Handler(BaseHTTPRequestHandler):
    server_version = "silicon-agent/0.1"

    def _auth(self) -> bool:
        if not TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter than the default
        if "/health" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/health":
            return self._json(200, {"ok": True, "backends": list(BACKENDS), "designs": list(DESIGNS), "queued": _queue.qsize()})
        if not self._auth():
            return self._json(401, {"error": "unauthorized"})
        if u.path == "/jobs":
            with _lock:
                return self._json(200, [{"id": j, "status": v["status"], "task": v["spec"].get("task", ""),
                                         "design": v["spec"].get("design", "matmul"),
                                         "backend": v["spec"].get("backend", "ecp5"), "created": v["created"]}
                                        for j, v in _jobs.items()])
        parts = u.path.strip("/").split("/")
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
                                            "summary": job.get("summary"), "error": job.get("error")})
            if len(parts) >= 4 and parts[2] == "files":
                rel = "/".join(parts[3:])
                base = (RESULTS / job_id).resolve()
                target = (base / rel).resolve()
                if base not in target.parents or not target.is_file():
                    return self._json(404, {"error": "no such file"})
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json" if rel.endswith(".json") else "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth():
            return self._json(401, {"error": "unauthorized"})
        if urlsplit(self.path).path != "/jobs":
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            spec = body["spec"]
            backend = spec.get("backend", "ecp5")
            if backend not in BACKENDS:
                return self._json(400, {"error": f"unknown backend {backend!r}; have {list(BACKENDS)}"})
            design = spec.get("design", "matmul")
            if design not in DESIGNS:
                return self._json(400, {"error": f"unknown design {design!r}; have {list(DESIGNS)}"})
            max_iter = int(body.get("max_iter", 8))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._json(400, {"error": f"bad request: {e}"})
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        with _lock:
            _jobs[job_id] = {"spec": spec, "max_iter": max_iter, "status": "queued", "events": [], "created": time.time()}
        _queue.put(job_id)
        self._json(202, {"id": job_id})


def main():
    host = os.environ.get("SA_HOST", "127.0.0.1")
    port = int(os.environ.get("SA_PORT", "8765"))
    RESULTS.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_worker, daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"silicon-agent server on http://{host}:{port}  root={ROOT}  backends={list(BACKENDS)}  auth={'on' if TOKEN else 'off'}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
