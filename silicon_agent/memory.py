"""Design memory: lessons distilled from past runs, retrieved by situation, scored by outcome.

A lesson records one planner move and what actually happened:

  situation  -> which constraints were violated, what dominated the critical path,
                which structural class the design was in (pipelined / folded / ...)
  action     -> the IR fields the planner changed and in which direction
  effect     -> measured deltas (fmax, lut, dsp), whether the design became feasible
  text       -> one sentence the planner can use next time, written by the LLM from the numbers
  score      -> confirmations - contradictions, updated automatically whenever a later run
                takes a matching action in a matching situation (and by human votes)

Retrieval is a plain feature overlap on (design, backend, situation); no embeddings.
Storage is one JSONL file per (design, backend) under SA_ROOT/memory/.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

from .ir import Constraints, Metrics

ROOT = Path(os.environ.get("SA_ROOT", Path(__file__).resolve().parent.parent))
MEM_DIR = ROOT / "memory"
_lock = threading.Lock()

TOP_K = 6


# ---------------------------------------------------------------- situation features
def dominant_stage(critical_path: list[dict]) -> str:
    """The kind of the stage with the largest logic+route delay, normalized across backends."""
    stages = [s for s in critical_path if "kind" in s]
    if not stages:
        return "none"
    s = max(stages, key=lambda e: e.get("logic_ns", 0) + e.get("route_ns", 0))
    k = s["kind"].lower()
    route_heavy = s.get("route_ns", 0) > 1.5 * max(s.get("logic_ns", 0), 0.01)
    if "mult" in k or "dsp48" in k:
        return "dsp"
    if "carry" in k:
        return "carry-chain"
    if "lut" in k or "logic" in k:
        return "routing" if route_heavy else "lut-logic"
    if "ff" in k or "fdre" in k:
        return "routing"
    return k


def violation_kinds(violations: list[str]) -> list[str]:
    kinds = []
    for v in violations:
        if v.startswith("fmax"):
            kinds.append("timing")
        elif v.startswith("dsp"):
            kinds.append("dsp")
        elif v.startswith("lut"):
            kinds.append("lut")
        elif "simulation" in v:
            kinds.append("sim")
        elif "fit" in v:
            kinds.append("overflow")
    return sorted(set(kinds)) or ["feasible"]


def structure_class(design: str, ir: dict) -> list[str]:
    tags = []
    if ir.get("dsp_regs", "none") != "none":
        tags.append("dsp-registered")
    else:
        tags.append("dsp-combinational")
    if ir.get("pipe_mul", 0) > 0:
        tags.append("fabric-pipe")
    if design == "fir":
        tags.append("folded" if ir.get("fold", 1) > 1 else "transposed")
        if ir.get("symmetric"):
            tags.append("symmetric")
    if design == "matmul":
        tags.append("acc-in-dsp" if ir.get("acc_in_dsp", True) else "acc-in-fabric")
    return tags


def situation(design: str, ir: dict, metrics: dict, violations: list[str]) -> dict:
    return {
        "violations": violation_kinds(violations),
        "critical": dominant_stage(metrics.get("critical_path", [])),
        "structure": structure_class(design, ir),
    }


def action_of(ir_before: dict, ir_after: dict) -> dict:
    """{field: [before, after]} for every planner-visible field that changed."""
    return {k: [ir_before.get(k), ir_after.get(k)] for k in ir_after
            if k != "coefs" and ir_before.get(k) != ir_after.get(k)}


def effect_of(m0: Metrics, m1: Metrics, v0: list[str], v1: list[str], c: Constraints) -> dict:
    fmax_d = m1.fmax_mhz - m0.fmax_mhz
    eff = {
        "fmax_delta": round(fmax_d, 1), "lut_delta": m1.lut - m0.lut, "dsp_delta": m1.dsp - m0.dsp,
        "became_feasible": bool(v0) and not v1, "lost_feasibility": not v0 and bool(v1),
    }
    # did the move help the thing that was wrong?
    kinds = violation_kinds(v0)
    helped = hurt = False
    if "timing" in kinds:
        helped |= fmax_d > 0.03 * max(m0.fmax_mhz, 1); hurt |= fmax_d < -0.03 * max(m0.fmax_mhz, 1)
    if "dsp" in kinds or "overflow" in kinds:
        helped |= m1.dsp < m0.dsp; hurt |= m1.dsp > m0.dsp
    if "lut" in kinds:
        helped |= m1.lut < m0.lut; hurt |= m1.lut > m0.lut
    if kinds == ["feasible"]:
        # optimizing: judge on the objective list
        helped |= eff["lost_feasibility"] is False and (m1.lut < m0.lut or fmax_d > 0.03 * m0.fmax_mhz)
        hurt |= eff["lost_feasibility"]
    eff["outcome"] = "helped" if helped and not hurt else "hurt" if hurt and not helped else "neutral"
    if eff["became_feasible"]:
        eff["outcome"] = "helped"
    return eff


# ---------------------------------------------------------------- storage
def _path(design: str, backend: str) -> Path:
    return MEM_DIR / f"{design}_{backend}.jsonl"


def load(design: str, backend: str) -> list[dict]:
    p = _path(design, backend)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _save(design: str, backend: str, lessons: list[dict]) -> None:
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(design, backend)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(l) + "\n" for l in lessons))
    tmp.replace(p)


def add(design: str, backend: str, lesson: dict) -> dict:
    lesson = {"id": secrets.token_hex(4), "created": time.time(), "confirmations": 0, "contradictions": 0,
              "retrieved": 0, "votes": 0, **lesson}
    with _lock:
        ls = load(design, backend)
        ls.append(lesson)
        _save(design, backend, ls)
    return lesson


def update(design: str, backend: str, lesson_id: str, **fields) -> dict | None:
    with _lock:
        ls = load(design, backend)
        for l in ls:
            if l["id"] == lesson_id:
                for k, v in fields.items():
                    l[k] = l.get(k, 0) + v if k in ("confirmations", "contradictions", "retrieved", "votes") else v
                _save(design, backend, ls)
                return l
    return None


def score(l: dict) -> float:
    return l.get("confirmations", 0) - 1.5 * l.get("contradictions", 0) + 0.5 * l.get("votes", 0)


# ---------------------------------------------------------------- retrieval
def similarity(sit: dict, l: dict) -> float:
    ls = l.get("situation", {})
    s = 0.0
    s += 2.0 * len(set(sit["violations"]) & set(ls.get("violations", [])))
    s += 1.5 * (sit["critical"] == ls.get("critical"))
    s += 0.5 * len(set(sit["structure"]) & set(ls.get("structure", [])))
    return s


def retrieve(design: str, backend: str, sit: dict, k: int = TOP_K, exclude_run: str | None = None) -> list[dict]:
    ls = [l for l in load(design, backend) if l.get("run_id") != exclude_run and score(l) > -2]
    ranked = sorted(ls, key=lambda l: (similarity(sit, l), score(l), l["created"]), reverse=True)
    picked = [l for l in ranked[:k] if similarity(sit, l) > 0]
    for l in picked:
        update(design, backend, l["id"], retrieved=1)
    return picked


def fmt_lessons(lessons: list[dict]) -> str:
    if not lessons:
        return ""
    lines = ["## Lessons from earlier runs on this device (measured; newest evidence first)"]
    for l in lessons:
        sc = score(l)
        tag = f"[{l['id']} score {sc:+.1f}, used {l.get('retrieved', 0)}x]"
        lines.append(f"- {l['text']} {tag}")
    lines.append("Cite a lesson id in your action when you follow one; contradict it explicitly if your diagnosis disagrees.")
    return "\n".join(lines)


# ---------------------------------------------------------------- closing the loop
def action_matches(lesson_action: dict, taken: dict) -> bool:
    """Same fields moved in the same direction (or to the same value for non-numeric fields)."""
    if not lesson_action or not taken:
        return False
    hits = 0
    for f, (b, a) in lesson_action.items():
        if f not in taken:
            continue
        tb, ta = taken[f]
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and isinstance(ta, (int, float)) and isinstance(tb, (int, float)):
            if (a - b) * (ta - tb) > 0:
                hits += 1
        elif ta == a:
            hits += 1
    return hits > 0 and hits >= len(lesson_action) / 2


def reconcile(design: str, backend: str, retrieved: list[dict], taken: dict, outcome: str) -> list[dict]:
    """After a move whose outcome is known: confirm/contradict the retrieved lessons it followed."""
    touched = []
    for l in retrieved:
        if action_matches(l.get("action", {}), taken):
            if outcome == "helped":
                touched.append(update(design, backend, l["id"], confirmations=1))
            elif outcome == "hurt":
                touched.append(update(design, backend, l["id"], contradictions=1))
    return [t for t in touched if t]
