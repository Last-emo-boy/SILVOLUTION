"""Read the collected run records (report/collect.py) into plain facts for the timelines and the report.

Every number the report prints is computed here from a record file; nothing is typed by hand.
"""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))


def load(p: Path):
    return json.loads(Path(p).read_text())


def events(results: Path) -> list[dict]:
    """events.jsonl as the loop wrote it (values are JSON already)."""
    return [json.loads(l) for l in (results / "events.jsonl").read_text().splitlines() if l.strip()]


def when(ts: float | str | None) -> str:
    if ts is None:
        return "—"
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    return datetime.fromtimestamp(float(ts), CST).strftime("%Y-%m-%d %H:%M:%S")


def mmss(sec: float) -> str:
    sec = float(sec)
    return f"{int(sec // 60)} 分 {int(sec % 60):02d} 秒" if sec >= 60 else f"{sec:.1f} 秒"


def usd(micros) -> str:
    return "—" if micros is None else f"${micros / 1e6:.3f}"


def ir_str(ir: dict | None) -> str:
    if not ir:
        return "—"
    return " ".join(f"{k}={v}" for k, v in ir.items() if k != "coefs" and not isinstance(v, (list, dict)))


# ---------------------------------------------------------------- Agnes Harness session records
def agh_session(path: Path, key: str | None = None) -> dict:
    d = load(path)
    k = key or next(iter(d))
    return d[k]


def agh_tools(sess: dict) -> list[dict]:
    """The session's tool calls in order (the harness's projection: name, status, argument/result previews)."""
    return [n for n in sess.get("nodes") or [] if n.get("kind") == "tool"]


def agh_turns(sess: dict) -> list[dict]:
    out = []
    for t in sess.get("turns") or []:
        u = t.get("usage") or {}
        tot = u.get("totals") or {}
        calls = u.get("calls") or []
        models = sorted({c.get("model") for c in calls if c.get("model")})
        out.append({"turn": t.get("turn"), "started": when(t.get("startedAt")), "ended": when(t.get("endedAt")), "seconds": (t.get("durationMs") or 0) / 1000,
                    "reason": t.get("reason"), "model": t.get("finalModel") or ", ".join(models), "calls": len(calls),
                    "input": tot.get("input", 0), "output": tot.get("output", 0), "cache": tot.get("cacheRead", 0), "reasoning": tot.get("reasoning", 0),
                    "usd_micros": (u.get("cost") or {}).get("usdMicros"),
                    "tools": [n.get("name") for n in sess.get("nodes") or [] if n.get("kind") == "tool" and n.get("id") in set(t.get("nodeIds") or [])]})
    return out


def brief(text: str | None, n: int = 160) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def tool_rows(sess: dict, collapse: tuple[str, ...] = ("sa_studio_status", "sa_job", "sa_tournament_status")) -> list[dict]:
    """Tool calls for a table: consecutive polls of the same status tool are folded into one row (×n)."""
    rows: list[dict] = []
    for n in agh_tools(sess):
        name = n.get("name")
        if rows and name in collapse and rows[-1]["name"] == name:
            rows[-1]["n"] += 1
            rows[-1]["result"] = brief(n.get("resultPreview"), 220)
            rows[-1]["seq_to"] = n.get("seq")
            continue
        rows.append({"seq": n.get("seq"), "seq_to": n.get("seq"), "name": name, "status": n.get("status"), "n": 1,
                     "args": brief(n.get("argsPreview"), 220), "result": brief(n.get("resultPreview"), 220)})
    return rows


# ---------------------------------------------------------------- runs
def run_rounds(job: dict) -> list[dict]:
    """One row per evaluated design (iteration.slot), with the plan that proposed it and the revise events of its round."""
    ev = job["events"]
    t0 = ev[0]["t"] if ev else 0
    plans = {e["iteration"]: e for e in ev if e["kind"] == "plan"}
    revs = {}
    for e in ev:
        if e["kind"] in ("revising", "revised"):
            revs.setdefault(e["iteration"], []).append(e)
    lessons = {e["iteration"]: e for e in ev if e["kind"] == "lesson"}
    rows = []
    for e in ev:
        if e["kind"] != "result":
            continue
        m = e.get("metrics") or {}
        it, slot = e["iteration"], e.get("slot") or 0
        cp = (m.get("critical_path") or [{}])[0].get("total_ns")
        rows.append({"it": it, "slot": slot, "t": e["t"] - t0, "ir": e.get("ir") or {}, "fmax": m.get("fmax_mhz"), "lut": m.get("lut"), "ff": m.get("ff"),
                     "dsp": m.get("dsp"), "bram": m.get("bram"), "seeds": m.get("fmax_seeds") or [], "cp_ns": cp, "violations": e.get("violations") or [],
                     "feasible": not e.get("violations"), "action": e.get("action") or "", "diagnosis": e.get("diagnosis") or (plans.get(it) or {}).get("diagnosis") or "",
                     "sim_stages": m.get("sim_stages") or {}, "sim_passed": m.get("sim_passed"), "eda_s": e.get("eda_seconds"),
                     "revise": [r for r in revs.get(it, [])] if not slot else [], "lesson": ((lessons.get(it) or {}).get("lesson") or {}) if not slot else {}})
    return sorted(rows, key=lambda r: (r["it"], r["slot"]))


def action_zh(a: str) -> str:
    """The loop's fixed phrases in Chinese (the planner's own prose is kept as written)."""
    a = a or ""
    a = re.sub(r"^designer revision #(\d+)(?: \(asked:.*)?$", r"设计师修订 #\1（改写后实测）", a, flags=re.S)
    a = re.sub(r"^sibling candidate: ", "并行候选：", a)
    a = re.sub(r"^sibling candidate #(\d+) won this round; continuing from it$", r"并行候选 #\1 赢得本轮，从它继续", a)
    return {"initial design from spec": "起点设计", "initial design": "起点设计"}.get(a, a)


def run_stops(job: dict) -> list[dict]:
    t0 = job["events"][0]["t"] if job["events"] else 0
    return [{"it": e["iteration"], "t": e["t"] - t0, "reason": e.get("stop_reason") or e.get("action"), "diagnosis": e.get("diagnosis")} for e in job["events"] if e["kind"] == "plan" and e.get("stop")]


def best(job: dict) -> dict | None:
    b = (job.get("summary") or {}).get("best")
    if not b:
        return None
    m = b["metrics"]
    return {"it": b["iteration"], "ir": b["ir"], "fmax": m["fmax_mhz"], "lut": m["lut"], "ff": m.get("ff"), "dsp": m["dsp"], "bram": m.get("bram"),
            "seeds": m.get("fmax_seeds") or [], "latency": b.get("latency_cycles"), "throughput": b.get("throughput"), "unit": (job.get("summary") or {}).get("throughput_unit", ""),
            "cp": m.get("critical_path") or [], "sim_stages": m.get("sim_stages") or {}}


def run_seconds(job: dict) -> float:
    ev = job["events"]
    return (ev[-1]["t"] - ev[0]["t"]) if ev else 0


# ---------------------------------------------------------------- studio
def studio_checks(record_dir: Path) -> list[dict]:
    """Every check report the studio wrote (checks/<tag>/report.json), oldest first."""
    out = []
    for d in sorted((record_dir / "checks").iterdir()):
        f = d / "report.json"
        if not f.exists():
            continue
        r = load(f)
        out.append({"tag": d.name, "ok": r.get("ok"), "seconds": r.get("seconds"), "steps": r.get("steps") or [], "mtime": f.stat().st_mtime})
    return out


def studio_turns(record: dict) -> list[dict]:
    """The designer turns the studio record kept (text, tools, the harness ledger after the turn)."""
    out = []
    for t in record.get("turns") or []:
        u = (t.get("usage") or {}).get("turn") if isinstance((t.get("usage") or {}).get("turn"), dict) else (t.get("usage") or {})
        out.append({"t": when(t.get("t")), "tools": t.get("tools") or [], "text": t.get("text") or ""})
    return out


def literal(s):
    """Some old records hold Python reprs; accept both."""
    if isinstance(s, str):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return ast.literal_eval(s)
    return s
