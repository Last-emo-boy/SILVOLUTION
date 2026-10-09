"""LLM client: DeepSeek via the OpenAI-compatible chat completions endpoint.

Key resolution: DEEPSEEK_API_KEY env var, then macOS Keychain (service 'deepseek').
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("SA_MODEL", "deepseek-flash")


def api_key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k
    try:
        return subprocess.check_output(["security", "find-generic-password", "-s", "deepseek", "-w"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit("no DeepSeek key: set DEEPSEEK_API_KEY or add a 'deepseek' item to the macOS Keychain")


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def _extract_json(text: str) -> dict:
    text = _FENCE.sub("", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


EFFORT = os.environ.get("SA_EFFORT", "high")  # deepseek reasoning_effort for the first attempt


MAX_TOKENS = int(os.environ.get("SA_MAX_TOKENS", "131072"))  # model allows up to 393216
LLM_CACHE = Path(os.environ.get("SA_ROOT", Path(__file__).resolve().parent.parent)) / "cache" / "llm"


def chat_json(system: str, user: str, max_tokens: int | None = None, retries: int = 3, model: str | None = None) -> dict:
    """One chat turn, JSON-object response, returned parsed. Retries on transport
    errors and on empty/unparseable content. A reasoning model that spends the whole
    budget thinking returns finish_reason=length with empty content; on that we step
    the reasoning effort down (high -> low) rather than just retrying the same call.
    `model` overrides the default planner model (planner A/B: the same task run under several models)."""
    max_tokens = max_tokens or MAX_TOKENS
    model = model or MODEL
    # replies are deterministic enough for our purpose and a resumed run must replay the same plan
    key = hashlib.sha256(f"{model}\n{system}\n{user}".encode()).hexdigest()[:20]
    hit = LLM_CACHE / f"{key}.json"
    if hit.exists():
        return json.loads(hit.read_text())
    efforts = [EFFORT, EFFORT, "low"]
    last_err: Exception | None = None
    for attempt in range(retries):
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "reasoning_effort": efforts[min(attempt, len(efforts) - 1)],
        }
        try:
            req = urllib.request.Request(
                f"{BASE_URL}/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
            )
            # a reasoning reply that takes longer than this is not worth waiting for: the retry drops the effort
            with urllib.request.urlopen(req, timeout=int(os.environ.get("SA_LLM_TIMEOUT", "480"))) as resp:
                r = json.load(resp)
            choice = r["choices"][0]
            text = choice["message"].get("content") or ""
            if not text.strip():
                raise ValueError(f"empty content (finish_reason={choice.get('finish_reason')}, usage={r.get('usage')})")
            parsed = _extract_json(text)
            LLM_CACHE.mkdir(parents=True, exist_ok=True)
            hit.write_text(json.dumps(parsed))
            return parsed
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as e:
            last_err = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (401, 402, 403):
                # no credit / bad key: retrying with less reasoning does not help
                raise RuntimeError(f"LLM account error: HTTP {e.code} ({'insufficient balance' if e.code == 402 else 'unauthorized'})")
            print(f"     llm attempt {attempt + 1}/{retries} (effort={body['reasoning_effort']}) failed: {e}", flush=True)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}")
