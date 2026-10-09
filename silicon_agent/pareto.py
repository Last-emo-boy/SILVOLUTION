"""Cross-run Pareto front: every design the agent has ever measured on a (design, backend), reduced to the
non-dominated set over (throughput up, fmax up, lut down, dsp down). Lets the product answer "what is the best
you can get on this device" without a new run, and seeds new runs with known-good points.
"""

from __future__ import annotations

import json

from .ir import Metrics


def points(jobs: dict, design: str, backend: str) -> list[dict]:
    """All sim-passing, fitting evaluations across jobs for one design/backend (deduplicated by IR)."""
    seen, out = set(), []
    for jid, j in jobs.items():
        sp = j["spec"]
        if sp.get("design", "matmul") != design or sp.get("backend", "ecp5") != backend:
            continue
        for e in j["events"]:
            if e["kind"] != "result":
                continue
            mm = e["metrics"]
            if not mm.get("sim_passed", True) or not mm.get("fits", True) or mm.get("fmax_mhz", 0) <= 0 or any(v.get("passed") is False for v in (mm.get("sim_stages") or {}).values()):
                continue
            m = Metrics(**{k: mm.get(k, d) for k, d in (("fmax_mhz", 0), ("lut", 0), ("ff", 0), ("dsp", 0), ("bram", 0), ("critical_path", []), ("sim_passed", True))},
                        fits=mm.get("fits", True), sim_stages=mm.get("sim_stages") or {}, power_mw=mm.get("power_mw"))
            ir = {k: v for k, v in e["ir"].items() if k != "coefs"}
            # IRs may hold lists (graph nodes, coefficient tables): the dedup key is their canonical JSON
            key = json.dumps([ir, sp.get("locked") or {}], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            out.append({"job": jid, "iteration": e["iteration"], "ir": ir, "locked": sp.get("locked", {}), "task": sp.get("task", ""),
                        "fmax_mhz": m.fmax_mhz, "lut": m.lut, "ff": m.ff, "dsp": m.dsp, "bram": m.bram, "power_mw": m.power_mw,
                        "throughput": e["throughput"], "latency_cycles": e.get("latency_cycles")})
    return out


def _dominates(a: dict, b: dict) -> bool:
    ge = a["throughput"] >= b["throughput"] and a["fmax_mhz"] >= b["fmax_mhz"] and a["lut"] <= b["lut"] and a["dsp"] <= b["dsp"]
    gt = a["throughput"] > b["throughput"] or a["fmax_mhz"] > b["fmax_mhz"] or a["lut"] < b["lut"] or a["dsp"] < b["dsp"]
    return ge and gt


def front(pts: list[dict]) -> list[dict]:
    return sorted([p for p in pts if not any(_dominates(q, p) for q in pts if q is not p)], key=lambda p: (-p["throughput"], -p["fmax_mhz"]))


def best_under(pts: list[dict], fmax_mhz: float | None = None, max_dsp: int | None = None, max_lut: int | None = None, objective: list[str] | None = None) -> dict | None:
    """The measured point that satisfies the constraints and is best by the lexicographic objective."""
    ok = [p for p in pts if (fmax_mhz is None or p["fmax_mhz"] >= fmax_mhz) and (max_dsp is None or p["dsp"] <= max_dsp) and (max_lut is None or p["lut"] <= max_lut)]
    if not ok:
        return None
    obj = objective or ["maximize_throughput", "minimize_lut"]
    key = lambda p: tuple({"maximize_throughput": -p["throughput"], "maximize_fmax": -p["fmax_mhz"], "minimize_lut": p["lut"], "minimize_dsp": p["dsp"], "minimize_latency": p.get("latency_cycles") or 0}[o] for o in obj)
    return min(ok, key=key)
