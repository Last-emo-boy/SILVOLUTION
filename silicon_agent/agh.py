"""Agnes Harness (AGH) as the agent runtime: silicon-agent's chat turns run inside an AGH session.

AGH owns the model loop (DeepSeek through its provider layer), the tool records, approvals, session
history and compaction; silicon-agent contributes the EDA loop as tools through the `agh-plugin/`
package and keeps the job server as the execution engine. This module is the Python side: a JSON-RPC
client for the daemon's unix socket (the same ACP-style handshake `@agnes/sdk` performs), session
creation per conversation thread, and one `prompt()` per chat turn that returns the assistant text
plus the tool calls AGH recorded.

Environment:
  SA_AGH            "1" to route chat turns through AGH (default: off, nl.py answers directly)
  SA_AGH_HOME       AGH home (default ~/.agh); the daemon's owner.json lives in <home>/data/daemon/
  SA_AGH_ENTRY      path to the built agnes.mjs, used to start the daemon when it is not running
  SA_AGH_PROFILE    profile name (default local-dev)
  SA_AGH_WORKSPACE  working directory AGH sessions run in (default SA_ROOT/agh-workspace)
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

META = "ai.agnes.harness"
AGH_HOME = Path(os.environ.get("SA_AGH_HOME", Path.home() / ".agh"))
AGH_ENTRY = os.environ.get("SA_AGH_ENTRY", "")
AGH_PROFILE = os.environ.get("SA_AGH_PROFILE", "local-dev")


def enabled() -> bool:
    return os.environ.get("SA_AGH", "") == "1"


class AghError(RuntimeError):
    pass


class AghClient:
    """One JSON-RPC connection to the AGH daemon. Thread-safe for concurrent requests; notifications
    (session/update) are routed to the session that owns them."""

    def __init__(self, socket_path: str, client_id: str | None = None):
        self.socket_path = socket_path
        self.client_id = client_id or f"sa-{secrets.token_hex(8)}"
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._next = 1
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._listeners: dict[str, list] = {}
        self._reader: threading.Thread | None = None
        self.initialized = False

    # ------------------------------------------------------------ transport
    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(self.socket_path)
        s.settimeout(None)
        self._sock = s
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        r = self.call("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "_meta": {META: {"capabilities": {"permission": True}}}},
            "_meta": {META: {"clientId": self.client_id, "auth": {"kind": "local"}}},
        }, timeout=20)
        if r.get("protocolVersion") != 1:
            raise AghError(f"unsupported protocol {r.get('protocolVersion')}")
        self.initialized = True

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _send(self, msg: dict) -> None:
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        with self._lock:
            if not self._sock:
                raise AghError("not connected")
            self._sock.sendall(data)

    def _read_loop(self) -> None:
        buf = b""
        sock = self._sock
        while sock:
            try:
                chunk = sock.recv(1 << 16)
            except OSError:
                chunk = b""
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    self._dispatch(json.loads(line))
                except Exception as e:  # a bad frame must not kill the reader
                    print(f"agh: bad frame: {e}", flush=True)
        for ev, slot in list(self._pending.values()):
            slot.append({"error": {"code": -1, "message": "connection closed"}})
            ev.set()

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            p = self._pending.pop(msg["id"], None)
            if p:
                p[1].append(msg)
                p[0].set()
            return
        method = msg.get("method")
        if method == "session/request_permission":
            # the EDA tools declare their own approval class; anything AGH still asks about (shell etc.)
            # is answered allow-once: silicon-agent's own authorization is the bearer token on the job server
            opts = msg.get("params", {}).get("options") or []
            pick = next((o for o in opts if o.get("kind") == "allow_once"), opts[0] if opts else None)
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": {"outcome": "selected", "optionId": pick["optionId"]} if pick else {"outcome": "cancelled"}}})
            return
        if method:
            sid = (msg.get("params") or {}).get("sessionId")
            for fn in self._listeners.get(sid, []) + self._listeners.get("*", []):
                try:
                    fn(method, msg.get("params") or {})
                except Exception as e:
                    print(f"agh: listener error: {e}", flush=True)

    def call(self, method: str, params: dict, timeout: float | None = 60) -> dict:
        with self._lock:
            rid = self._next
            self._next += 1
        ev = threading.Event()
        slot: list = []
        self._pending[rid] = (ev, slot)
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if not ev.wait(timeout):
            self._pending.pop(rid, None)
            raise AghError(f"{method}: timeout")
        msg = slot[0]
        if "error" in msg:
            e = msg["error"]
            raise AghError(f"{method}: {e.get('message')} {json.dumps(e.get('data'))[:300]}")
        return msg.get("result") or {}

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def listen(self, session_id: str, fn) -> None:
        self._listeners.setdefault(session_id, []).append(fn)

    def unlisten(self, session_id: str, fn) -> None:
        lst = self._listeners.get(session_id, [])
        if fn in lst:
            lst.remove(fn)

    # ------------------------------------------------------------ sessions
    def new_session(self, cwd: str, preset: str | None = None, session_key: str | None = None) -> str:
        meta = {}
        if preset:
            meta["preset"] = preset
        if session_key:
            meta["sessionKey"] = session_key
        try:
            self.call("_agnes/v1/workspace.add", {"path": cwd})
        except AghError:
            pass
        r = self.call("session/new", {"cwd": cwd, "mcpServers": [], "_meta": {META: meta}})
        return r["sessionId"]

    def load_session(self, session_id: str, cwd: str) -> str:
        r = self.call("session/load", {"sessionId": session_id, "cwd": cwd, "mcpServers": []}, timeout=60)
        return r.get("sessionId", session_id)

    def prompt(self, session_id: str, text: str, on_event=None, timeout: float = 3600) -> dict:
        """Run one turn. Returns {"text": assistant text, "tools": [{name, args, result}], "stop": reason}."""
        tools: list[dict] = []
        calls: dict[str, dict] = {}
        texts: list[str] = []
        state = {"last_assistant": ""}

        def on_update(method: str, params: dict) -> None:
            if method != "session/update":
                return
            upd = params.get("update") or {}
            kind = upd.get("sessionUpdate")
            ev = (params.get("_meta") or {}).get(META) or {}
            if kind == "agent_message_chunk":
                c = upd.get("content") or {}
                if c.get("type") == "text":
                    texts.append(c.get("text", ""))
            elif kind == "tool_call":
                calls[upd.get("toolCallId")] = {"name": upd.get("title") or upd.get("kind"), "raw": upd.get("rawInput"), "status": upd.get("status")}
            elif kind == "tool_call_update":
                c = calls.setdefault(upd.get("toolCallId"), {})
                c["status"] = upd.get("status", c.get("status"))
                if upd.get("title") and not c.get("name"):
                    c["name"] = upd["title"]
                if upd.get("rawOutput") is not None:
                    c["result"] = upd.get("rawOutput")
                elif upd.get("content"):
                    # ACP carries the result as content blocks; our tools emit one JSON text block
                    txt = "".join(b.get("content", {}).get("text", "") for b in upd["content"] if isinstance(b, dict) and b.get("type") == "content")
                    try:
                        c["result"] = json.loads(txt) if txt else None
                    except json.JSONDecodeError:
                        c["result"] = txt
                if upd.get("rawInput") is not None:
                    c["raw"] = upd.get("rawInput")
                # a listener may want to react to a finished tool with its result in hand
                upd = {**upd, "_result": c.get("result"), "title": c.get("name")}
            if on_event:
                on_event(kind, upd, ev)

        self.listen(session_id, on_update)
        try:
            r = self.call("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]}, timeout=timeout)
        finally:
            self.unlisten(session_id, on_update)
        for cid, c in calls.items():
            tools.append({"id": cid, "name": c.get("name"), "args": c.get("raw"), "result": c.get("result"), "status": c.get("status")})
        return {"text": "".join(texts).strip() or state["last_assistant"], "tools": tools, "stop": r.get("stopReason")}

    def cancel(self, session_id: str) -> None:
        self.notify("session/cancel", {"sessionId": session_id})

    def set_model(self, session_id: str, route: str, model: str, thinking: str | None = None) -> dict:
        p: dict = {"sessionId": session_id, "route": route, "model": model}
        if thinking:
            p["thinking"] = thinking
        return self.call("_agnes/v1/session.setModel", p)

    def config(self) -> dict:
        return self.call("_agnes/v1/config.get", {})


# ---------------------------------------------------------------- daemon discovery / start
def owner_file() -> Path:
    return AGH_HOME / "data" / "daemon" / "owner.json"


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DEEPSEEK")}
    env["AGH_HOME"] = str(AGH_HOME)
    env["AGNES_PROFILE"] = AGH_PROFILE
    return env


def daemon_running() -> str | None:
    """Socket path of a reachable daemon, else None."""
    f = owner_file()
    if not f.exists():
        return None
    try:
        path = json.loads(f.read_text())["socketPath"]
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(path)
        s.close()
        return path
    except Exception:
        return None


def ensure_daemon(timeout: float = 60) -> str:
    path = daemon_running()
    if path:
        return path
    if not AGH_ENTRY:
        raise AghError("AGH daemon is not running and SA_AGH_ENTRY is unset")
    subprocess.run(["node", AGH_ENTRY, "daemon", "start"], env=_env(), capture_output=True, text=True, timeout=timeout, check=False)
    t0 = time.time()
    while time.time() - t0 < timeout:
        path = daemon_running()
        if path:
            return path
        time.sleep(0.5)
    raise AghError("AGH daemon did not come up")


_client: AghClient | None = None
_client_lock = threading.Lock()


def client() -> AghClient:
    global _client
    with _client_lock:
        if _client and _client._sock:
            return _client
        c = AghClient(ensure_daemon())
        c.connect()
        _client = c
        return c


def workspace() -> str:
    root = Path(os.environ.get("SA_AGH_WORKSPACE", Path(os.environ.get("SA_ROOT", Path(__file__).resolve().parent.parent)) / "agh-workspace"))
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def configure_provider(api_key: str, model: str = "deepseek-flash", provider: str = "deepseek", base_url: str | None = None) -> dict:
    """Save the DeepSeek account into AGH's configuration service (the key goes to its credential store)."""
    c = client()
    cur = c.config()
    body = {"providerId": provider, "apiKey": api_key, "model": model, "expectedRevision": cur.get("revision", 0)}
    if base_url:
        body["baseUrl"] = base_url
    return c.call("_agnes/v1/config.save", body, timeout=90)
