"""Candidate screening: cheap, closed-form resource estimates per design kind.

The planner proposes several architectures at once; before spending 1.5-4 min of
Vivado on each, we rank them with these estimates plus the design memory, and
evaluate only the most promising. Estimates are deliberately simple and biased
to the measured runs in prompts.py; they must be good enough to *order*
candidates, not to replace EDA.
"""

from __future__ import annotations

import math

from .ir import Constraints


def estimate_matmul(ir: dict, backend: str) -> dict:
    pe = ir["pe_rows"] * ir["pe_cols"]
    aw = ir["acc_width"]
    if backend == "xc7s50":
        dsp = pe * (2 if ir.get("pipe_mul", 0) > 0 else 1)
        lut = pe * (aw / 2 if not ir.get("acc_in_dsp", True) else 6) + 40 * ir["pe_rows"] + 20 * ir["pe_cols"]
        if ir.get("dsp_regs", "none") == "none":
            fmax = 190
        elif ir.get("acc_in_dsp", True):
            fmax = 275
        else:
            fmax = 300 - 0.6 * max(aw - 24, 0)
        fmax -= 2 * max(ir["pe_rows"] - 4, 0)
    else:
        dsp = pe
        lut = pe * aw * 1.0 + 60 * ir["pe_rows"]
        if ir.get("dsp_regs", "none") == "none":
            fmax = 75
        else:
            fmax = 190 - 1.5 * max(aw - 24, 0) - 1.2 * max(pe - 32, 0)
            if ir.get("pipe_mul", 0) == 0:
                fmax -= 40
    return {"fmax_mhz": fmax, "lut": int(lut), "dsp": dsp, "throughput": pe}


def estimate_fir(ir: dict, backend: str) -> dict:
    taps = ir["taps"]
    U = (taps + 1) // 2 if ir.get("symmetric") else taps
    fold = ir.get("fold", 1)
    mults = math.ceil(U / fold)
    aw = ir["acc_width"]
    if backend == "xc7s50":
        if fold == 1:
            dsp = int(mults * 1.4) if ir.get("acc_in_dsp", True) else mults
            lut = 1 if ir.get("acc_in_dsp", True) else U * aw / 2
            fmax = 450 if ir.get("dsp_regs", "none") != "none" else 220
        else:
            dsp = mults * (2 if ir.get("acc_in_dsp", True) else 1)
            lut = mults * (aw / 2 + 8 * fold) + taps * 2
            fmax = 240 - 10 * max(fold - 4, 0) - (40 if ir.get("symmetric") else 0)
            if ir.get("dsp_regs", "none") == "none":
                fmax -= 60
    else:
        if ir.get("dsp_regs", "none") == "none":
            dsp = 12 if taps >= 24 else mults
            fmax = 230
            lut = taps * 33
        else:
            dsp = mults
            fmax = (220 if fold == 1 else 125 - 5 * max(fold - 4, 0)) - 0.5 * max(aw - 38, 0)
            lut = mults * aw * 0.7 + taps * 12
    return {"fmax_mhz": fmax, "lut": int(lut), "dsp": dsp, "throughput": round(1 / fold, 4)}


ESTIMATORS = {"matmul": estimate_matmul, "fir": estimate_fir}


def screen(design_kind: str, backend: str, candidates: list[dict], c: Constraints) -> list[dict]:
    """Rank candidates: feasible-by-estimate first (by objective), then by how badly they miss.
    Returns the candidates annotated with `estimate`, `est_violations` and `rank`."""
    est_f = ESTIMATORS[design_kind]
    rows = []
    for cand in candidates:
        ir = cand["ir"]
        e = est_f(ir, backend)
        viol = []
        if e["fmax_mhz"] < c.fmax_mhz:
            viol.append(f"fmax~{e['fmax_mhz']:.0f}<{c.fmax_mhz}")
        if c.max_dsp is not None and e["dsp"] > c.max_dsp:
            viol.append(f"dsp~{e['dsp']}>{c.max_dsp}")
        if c.max_lut is not None and e["lut"] > c.max_lut:
            viol.append(f"lut~{e['lut']}>{c.max_lut}")
        miss = (max(0, (c.fmax_mhz - e["fmax_mhz"]) / c.fmax_mhz)
                + (max(0, (e["dsp"] - c.max_dsp) / c.max_dsp) if c.max_dsp else 0)
                + (max(0, (e["lut"] - c.max_lut) / c.max_lut) if c.max_lut else 0))
        key = []
        for obj in c.objective:
            key.append({"maximize_throughput": -e["throughput"], "maximize_fmax": -e["fmax_mhz"],
                        "minimize_lut": e["lut"], "minimize_dsp": e["dsp"]}.get(obj, 0))
        rows.append({**cand, "estimate": e, "est_violations": viol, "_sort": (len(viol) > 0, miss, tuple(key))})
    rows.sort(key=lambda r: r["_sort"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        del r["_sort"]
    return rows
