"""Candidate screening: cheap, closed-form resource estimates per design kind.

The planner proposes several architectures at once; before spending 1.5-4 min of
Vivado on each, we rank them with these estimates plus the design memory, and
evaluate only the most promising. Estimates are deliberately simple and biased
to the measured runs in prompts.py; they must be good enough to *order*
candidates, not to replace EDA.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

from .ir import Constraints

ROOT = Path(os.environ.get("SA_ROOT", Path(__file__).resolve().parent.parent))
OBS_DIR = ROOT / "memory"
_lock = threading.Lock()


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


# ---------------------------------------------------------------- calibration from measured runs
# Every EDA result is recorded as (features, measured). The closed-form estimate is then corrected
# per (design, backend, metric) by a ridge-regularized linear fit of  measured / estimate  on a
# small feature vector -- i.e. we learn multiplicative corrections, not a new model. Few points,
# strong prior (identity): with < 6 observations the correction is the identity.

def _features(design: str, ir: dict, backend: str) -> list[float]:
    """Small, design-specific feature vector; the first entry is the bias."""
    f = [1.0]
    if design == "matmul":
        f += [math.log2(ir["pe_rows"] * ir["pe_cols"]), ir["acc_width"] / 32, 1.0 if ir.get("dsp_regs", "none") != "none" else 0.0,
              1.0 if ir.get("acc_in_dsp", True) else 0.0, ir.get("pipe_mul", 0), 1.0 if ir.get("reg_in") else 0.0, math.log2(max(ir["pe_rows"], 1))]
    else:
        f += [math.log2(ir["taps"]), math.log2(ir.get("fold", 1)), 1.0 if ir.get("symmetric") else 0.0, ir["acc_width"] / 40,
              1.0 if ir.get("dsp_regs", "none") != "none" else 0.0, 1.0 if ir.get("acc_in_dsp", True) else 0.0, ir.get("pipe_mul", 0)]
    return f


def record(design: str, backend: str, ir: dict, measured: dict) -> None:
    """Append one observation; called after every EDA evaluation."""
    e = ESTIMATORS[design](ir, backend)
    rec = {"t": time.time(), "features": _features(design, ir, backend),
           "estimate": {k: e[k] for k in ("fmax_mhz", "lut", "dsp")},
           "measured": {k: measured[k] for k in ("fmax_mhz", "lut", "dsp")}}
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        with (OBS_DIR / f"obs_{design}_{backend}.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")
    _fit_cache.pop((design, backend), None)


def _load_obs(design: str, backend: str) -> list[dict]:
    p = OBS_DIR / f"obs_{design}_{backend}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _solve(X: list[list[float]], y: list[float], lam: float) -> list[float]:
    """Ridge regression via normal equations with Gauss-Jordan; tiny systems only."""
    n = len(X[0])
    A = [[sum(X[k][i] * X[k][j] for k in range(len(X))) + (lam if i == j and i > 0 else 0.0) for j in range(n)] for i in range(n)]
    b = [sum(X[k][i] * y[k] for k in range(len(X))) for i in range(n)]
    M = [row + [b[i]] for i, row in enumerate(A)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(M[r][i]))
        if abs(M[piv][i]) < 1e-12:
            return [0.0] * n
        M[i], M[piv] = M[piv], M[i]
        for r in range(n):
            if r != i:
                fct = M[r][i] / M[i][i]
                M[r] = [a - fct * bb for a, bb in zip(M[r], M[i])]
    return [M[i][n] / M[i][i] for i in range(n)]


_fit_cache: dict = {}
MIN_OBS = 6


def _fit(design: str, backend: str) -> dict | None:
    key = (design, backend)
    if key in _fit_cache:
        return _fit_cache[key]
    obs = _load_obs(design, backend)
    obs = [o for o in obs if o["measured"]["fmax_mhz"] > 0 and o["estimate"]["fmax_mhz"] > 0]
    if len(obs) < MIN_OBS:
        _fit_cache[key] = None
        return None
    fit = {"n": len(obs)}
    X = [o["features"] for o in obs]
    nfeat = len(X[0])
    for metric in ("fmax_mhz", "lut", "dsp"):
        # learn log(measured/estimate): multiplicative correction, well behaved across scales
        y = [math.log(max(o["measured"][metric], 1) / max(o["estimate"][metric], 1)) for o in obs]
        raw_rmse = (sum(v * v for v in y) / len(y)) ** 0.5
        # pick the ridge strength by leave-one-out; keep identity unless the fit beats the raw estimate
        best = None
        for lam in (0.3, 1.0, 3.0, 10.0, 30.0):
            loo = []
            for i in range(len(X)):
                Xi = X[:i] + X[i + 1:]; yi = y[:i] + y[i + 1:]
                w = _solve(Xi, yi, lam)
                loo.append(y[i] - sum(wi * xi for wi, xi in zip(w, X[i])))
            rmse = (sum(r * r for r in loo) / len(loo)) ** 0.5
            if best is None or rmse < best[0]:
                best = (rmse, lam)
        if best[0] < 0.9 * raw_rmse:
            w = _solve(X, y, best[1])
            fit[metric] = {"w": w, "rmse_log": best[0], "lam": best[1], "raw_rmse_log": raw_rmse}
        else:
            fit[metric] = {"w": [0.0] * nfeat, "rmse_log": raw_rmse, "lam": None, "raw_rmse_log": raw_rmse}  # identity
    _fit_cache[key] = fit
    return fit


def calibrated(design: str, backend: str, ir: dict) -> dict:
    """Closed-form estimate with the learned multiplicative correction applied (if enough data)."""
    e = ESTIMATORS[design](ir, backend)
    fit = _fit(design, backend)
    if not fit:
        return {**e, "calibrated": False}
    f = _features(design, ir, backend)
    out = dict(e)
    for metric in ("fmax_mhz", "lut", "dsp"):
        corr = math.exp(max(-1.5, min(1.5, sum(wi * xi for wi, xi in zip(fit[metric]["w"], f)))))
        out[metric] = e[metric] * corr if metric == "fmax_mhz" else int(round(e[metric] * corr))
    out["calibrated"] = True
    out["n_obs"] = fit["n"]
    return out


def calibration_report(design: str, backend: str) -> dict:
    """How good the estimator is right now, raw vs calibrated, leave-one-out-free (in-sample)."""
    obs = [o for o in _load_obs(design, backend) if o["measured"]["fmax_mhz"] > 0]
    fit = _fit(design, backend)
    rep = {"n": len(obs), "calibrated": bool(fit)}
    for metric in ("fmax_mhz", "lut", "dsp"):
        raw = [abs(math.log(max(o["measured"][metric], 1) / max(o["estimate"][metric], 1))) for o in obs]
        rep[metric] = {"raw_median_err_pct": round((math.exp(sorted(raw)[len(raw) // 2]) - 1) * 100, 1) if raw else None,
                       "raw_rmse_pct": round((math.exp(fit[metric]["raw_rmse_log"]) - 1) * 100, 1) if fit else None,
                       "loo_rmse_pct": round((math.exp(fit[metric]["rmse_log"]) - 1) * 100, 1) if fit else None,
                       "applied": bool(fit and fit[metric]["lam"] is not None)}
    return rep


def screen(design_kind: str, backend: str, candidates: list[dict], c: Constraints) -> list[dict]:
    """Rank candidates: feasible-by-estimate first (by objective), then by how badly they miss.
    Returns the candidates annotated with `estimate`, `est_violations` and `rank`."""
    rows = []
    for cand in candidates:
        ir = cand["ir"]
        e = calibrated(design_kind, backend, ir)
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
