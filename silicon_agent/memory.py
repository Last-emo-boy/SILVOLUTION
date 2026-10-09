"""Design memory: lessons distilled from past runs, retrieved by situation, scored by outcome.

A lesson records one planner move and what actually happened:

  situation  -> which constraints were violated, what dominated the critical path,
                which structural class the design was in (pipelined / folded / ...)
  action     -> the IR fields the planner changed and in which direction
  effect     -> measured deltas (fmax, lut, dsp), whether the design became feasible
  text       -> one sentence the planner can use next time, written by the LLM from the numbers
  score      -> confirmations - contradictions, updated automatically whenever a later run
                takes a matching action in a matching situation (and by human votes)

Retrieval is hybrid: a BM25 text index over every lesson (its sentence, situation, action, design and device
tokens -- so a carry-chain lesson learned on an iCE40 FIR can surface for an ECP5 decimator) combined with the
structured situation overlap and the lesson's score; same design / same device get a boost, other pairs are
labelled as transfer when shown. Pure Python, rebuilt on demand when a memory file changes; no embedding model
(the provider has none) -- a few hundred one-sentence lessons do not need one.
Storage is one JSONL file per (design, backend) under SA_ROOT/memory/.
"""

from __future__ import annotations

import json
import math
import os
import re
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


# ---------------------------------------------------------------- text index (BM25 over every lesson)
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "is", "it", "its", "for", "with", "by", "from", "that", "this", "as",
         "be", "was", "are", "not", "do", "does", "no", "so", "but", "than", "then", "when", "while", "into", "because", "which", "while"}


def _tokens(text: str) -> list[str]:
    out = []
    for w in re.split(r"[^0-9A-Za-z_\u4e00-\u9fff]+", text or ""):
        if not w:
            continue
        for part in w.lower().split("_"):          # snake_case knobs: pipe_mul -> pipe, mul (and the whole knob)
            if len(part) >= 2 and part not in _STOP:
                out.append(part)
        if "_" in w:
            out.append(w.lower())
    return out


def _doc(design: str, backend: str, l: dict) -> list[str]:
    sit = l.get("situation") or {}
    parts = [design, backend, l.get("text", ""), " ".join(sit.get("violations") or []), str(sit.get("critical") or ""),
             " ".join(sit.get("structure") or []), " ".join(l.get("action") or {})]
    return _tokens(" ".join(parts))


class _Index:
    """BM25 over all lessons; rebuilt when any memory file changed."""
    def __init__(self):
        self.stamp = None
        self.docs: list[tuple[str, str, dict, list[str]]] = []
        self.df: dict[str, int] = {}
        self.avg = 1.0

    def _files(self):
        return sorted(p for p in MEM_DIR.glob("*.jsonl") if not p.name.startswith("obs_")) if MEM_DIR.exists() else []

    def refresh(self) -> None:
        files = self._files()
        stamp = tuple((p.name, p.stat().st_mtime_ns) for p in files)
        if stamp == self.stamp:
            return
        docs, df = [], {}
        for p in files:
            design, backend = p.stem.split("_", 1)
            for l in load(design, backend):
                toks = _doc(design, backend, l)
                docs.append((design, backend, l, toks))
                for t in set(toks):
                    df[t] = df.get(t, 0) + 1
        self.docs, self.df, self.stamp = docs, df, stamp
        self.avg = (sum(len(d[3]) for d in docs) / len(docs)) if docs else 1.0

    def bm25(self, q: list[str], toks: list[str], k1: float = 1.2, b: float = 0.75) -> float:
        n = len(self.docs)
        if not toks or not n:
            return 0.0
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in set(q):
            f = tf.get(t)
            if not f:
                continue
            idf = math.log(1 + (n - self.df.get(t, 0) + 0.5) / (self.df.get(t, 0) + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(toks) / self.avg))
        return s


_index = _Index()


def search(query: str, design: str | None = None, backend: str | None = None, sit: dict | None = None, k: int = TOP_K,
           exclude_run: str | None = None, min_score: float = -2.0) -> list[dict]:
    """Lessons ranked by text relevance to `query` (BM25), the structured situation overlap when `sit` is given, the
    lesson's own score, and a preference for the same design / device. Each hit carries its design, backend and
    the rank score; lessons from another (design, device) are transfer and are labelled so by the caller."""
    with _lock:
        _index.refresh()
        docs = list(_index.docs)
    q = _tokens(query)
    cands = []
    for d, b, l, toks in docs:
        if l.get("run_id") == exclude_run or score(l) <= min_score:
            continue
        text_rel = _index.bm25(q, toks) if q else 0.0
        sit_rel = similarity(sit, l) if sit else 0.0
        if text_rel <= 0 and sit_rel <= 0:
            continue
        cands.append((d, b, l, text_rel, sit_rel))
    if not cands:
        return []
    # text relevance normalized to 0..1 within the candidate set, then the structural signals: same design and
    # device (a lesson from the same pair outranks an equally worded one from elsewhere), situation overlap, score
    tmax = max(c[3] for c in cands) or 1.0
    out = []
    for d, b, l, text_rel, sit_rel in cands:
        rel = text_rel / tmax + 0.35 * (d == design) + 0.2 * (b == backend) + 0.12 * min(sit_rel, 4) + 0.05 * max(min(score(l), 6), -2) + 0.1 * float(l.get("confidence") or 0)
        out.append({**l, "design": d, "backend": b, "rank": round(rel, 3), "transfer": not (d == design and b == backend)})
    out.sort(key=lambda l: (-l["rank"], -l["created"]))
    return out[:k]


# ---------------------------------------------------------------- retrieval
def similarity(sit: dict, l: dict) -> float:
    ls = l.get("situation", {})
    s = 0.0
    s += 2.0 * len(set(sit["violations"]) & set(ls.get("violations", [])))
    s += 1.5 * (sit["critical"] == ls.get("critical"))
    s += 0.5 * len(set(sit["structure"]) & set(ls.get("structure", [])))
    return s


def retrieve(design: str, backend: str, sit: dict, k: int = TOP_K, exclude_run: str | None = None, query: str = "") -> list[dict]:
    """What the planner sees before its next move: the same-pair lessons that match the situation (as before), and,
    when a `query` (the last diagnosis, violations, critical-path cells) is given, text matches from every pair --
    at most a third of the slots go to transfer lessons from other designs / devices."""
    ls = [l for l in load(design, backend) if l.get("run_id") != exclude_run and score(l) > -2]
    ranked = sorted(ls, key=lambda l: (similarity(sit, l), score(l), l["created"]), reverse=True)
    picked = [{**l, "design": design, "backend": backend, "transfer": False} for l in ranked[:k] if similarity(sit, l) > 0]
    if query:
        seen = {l["id"] for l in picked}
        extra = [l for l in search(query, design, backend, sit, k=k * 2, exclude_run=exclude_run) if l["id"] not in seen]
        room = max(k - len(picked), 0)
        same = [l for l in extra if not l["transfer"]][:room]
        picked += same
        picked += [l for l in extra if l["transfer"]][:max(min(k // 3, k - len(picked)), 0)]
    for l in picked:
        update(l["design"], l["backend"], l["id"], retrieved=1)
    return picked


def fmt_lessons(lessons: list[dict]) -> str:
    if not lessons:
        return ""
    lines = ["## Lessons from earlier runs (measured; newest evidence first)"]
    for l in lessons:
        sc = score(l)
        tag = f"[{l['id']} score {sc:+.1f}, used {l.get('retrieved', 0)}x" + (f", from {l['design']}/{l['backend']} -- transfer, check it applies" if l.get("transfer") else "") + "]"
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
            # a transfer lesson is confirmed in its own file: evidence from another device still counts
            d, b = l.get("design") or design, l.get("backend") or backend
            if outcome == "helped":
                touched.append(update(d, b, l["id"], confirmations=1))
            elif outcome == "hurt":
                touched.append(update(d, b, l["id"], contradictions=1))
    return [t for t in touched if t]
