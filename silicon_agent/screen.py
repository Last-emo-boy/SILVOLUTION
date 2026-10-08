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


def _style(backend: str) -> str:
    from .backends import BACKENDS
    return BACKENDS[backend].emit_style if backend in BACKENDS else "behavioral"


def _scale(backend: str) -> float:
    """Fmax scale of a device relative to ECP5, from its advertised band (used only for devices without measured data)."""
    from .backends import BACKENDS
    if backend not in BACKENDS or backend in ("ecp5", "xc7s50"):
        return 1.0
    if backend == "sky130":
        # one measured 16-tap FIR: 205 MHz where ECP5 gives ~220 with the same knobs; adders ripple, no DSP
        return 0.95
    hi = float(BACKENDS[backend].fmax_range.split("-")[-1].split()[0])
    return hi / 250.0


def estimate_matmul(ir: dict, backend: str) -> dict:
    pe = ir["pe_rows"] * ir["pe_cols"]
    aw = ir["acc_width"]
    if _style(backend) == "vivado":
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
        fmax *= _scale(backend)
    return {"fmax_mhz": fmax, "lut": int(lut), "dsp": dsp, "throughput": pe}


def estimate_fir(ir: dict, backend: str) -> dict:
    taps = ir["taps"]
    U = (taps + 1) // 2 if ir.get("symmetric") else taps
    fold = ir.get("fold", 1)
    mults = math.ceil(U / fold)
    aw = ir["acc_width"]
    if _style(backend) == "vivado":
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
        if fold > 1 and ir.get("sum_tree") and mults > 1:
            # the M-way output reduction becomes one adder per stage: the output side stops being the limiter
            fmax *= 1.15
            lut += mults * aw * 0.3
        if fold > 1 and ir.get("pipe_in"):
            # the pre-adder + F:1 mux no longer feeds the multiplier in the same cycle
            fmax *= 1.1 if ir.get("symmetric") else 1.05
        fmax *= _scale(backend)
    return {"fmax_mhz": fmax, "lut": int(lut), "dsp": dsp, "throughput": round(1 / fold, 4)}


def estimate_conv(ir: dict, backend: str) -> dict:
    k, pr, aw, dw, cw = ir["k"], ir.get("par_rows", ir["k"]), ir["acc_width"], ir["data_width"], ir["coef_width"]
    mults = k * pr
    levels = math.ceil(math.log2(mults)) if mults > 1 else 0
    small_const = dw <= 8 and cw <= 8          # yosys folds small constant products into LUTs instead of DSPs
    dsp = 0 if (small_const and _style(backend) != "vivado") else mults
    lut = (mults * 20 if small_const else mults * 6) + (mults - 1) * aw / 2 + k * k * 2 + (ir["width"] * (k - 1) * dw if ir.get("line_mem") == "lut" else 60)
    regs = ir.get("tree_regs", 0)
    depth = max(1, levels - regs) if pr == k else max(1, levels - regs) + 1
    fmax = 240 / (0.7 + 0.45 * depth) if _style(backend) == "vivado" else 180 / (0.7 + 0.45 * depth)
    if ir.get("dsp_regs", "none") == "none" and ir.get("pipe_mul", 0) == 0:
        fmax *= 0.7
    if _style(backend) == "vivado":
        fmax *= 1.6
    fmax *= _scale(backend)
    return {"fmax_mhz": fmax, "lut": int(lut), "dsp": dsp, "throughput": round(pr / k, 4)}


def estimate_chain(ir: dict, backend: str) -> dict:
    ests = [estimate_fir(st, backend) for st in ir["stages"]]
    return {"fmax_mhz": min(e["fmax_mhz"] for e in ests), "lut": sum(e["lut"] for e in ests) + 40 * (len(ests) - 1),
            "dsp": sum(e["dsp"] for e in ests), "throughput": min(e["throughput"] for e in ests)}


def estimate_cordic(ir: dict, backend: str) -> dict:
    W = ir["width"] + ir.get("guard_bits", 2); N = ir["iterations"]; U = ir.get("unroll", 1); fold = ir.get("fold", 1)
    dsp = 1 if ir.get("scale_comp") else 0
    if fold == 1:
        lut = 3 * W * N * 0.9; ff = 3 * W * (N // U + 1)
        fmax = 200 / (0.55 + 0.5 * U) * (W / 18) ** -0.3
    else:
        lut = 3 * W * 1.2 + W * 6; fmax = 160 * (W / 18) ** -0.3
    if _style(backend) == "vivado":
        fmax *= 1.8
    fmax *= _scale(backend)
    return {"fmax_mhz": fmax, "lut": int(lut), "dsp": dsp, "throughput": round(1 / fold, 4)}


def estimate_fft(ir: dict, backend: str) -> dict:
    n, dw, tw = ir["n"], ir["data_width"], ir.get("tw_width", 16); S = int(math.log2(n))
    w_avg = dw + (S / 2 if ir.get("growth") == "grow" else 0)
    cmul = ir.get("cmul", "four")
    dsp = (S - 2) * {"four": 4, "three": 3, "lut": 0}[cmul]   # the last two stages have trivial twiddles
    regs = ir.get("mul_regs", 0) + {"none": 0, "out": 1, "in_out": 2, "in_pipe_out": 3}.get(ir.get("dsp_regs", "none"), 0)
    lut = S * (w_avg * 12 + 40) + (n * 2 * w_avg * 0.5 if ir.get("delay_mem") == "lut" else 0)
    if cmul == "three":
        lut += (S - 2) * 2 * (w_avg + tw)
    elif cmul == "lut":
        # measured: a 12x12 shift-add multiplier costs ~480 LUT4 on ECP5 (3.3 per bit-product); LUT6 fabrics about half
        lut += (S - 2) * 4 * w_avg * tw * (1.8 if _style(backend) == "vivado" else 3.3)
    fmax = 45 if regs <= 1 else (90 if regs == 2 else 130)
    if cmul == "three":
        fmax *= 0.85
    elif cmul == "lut":
        fmax *= 0.6
    if ir.get("bf_regs", 1) == 0:
        fmax *= 0.7
    if _style(backend) == "vivado":
        fmax *= 2.2
    fmax *= _scale(backend)
    return {"fmax_mhz": fmax, "lut": int(lut), "dsp": dsp, "throughput": 1.0}


def estimate_graph(ir: dict, backend: str) -> dict:
    ests = [ESTIMATORS[n["kind"]](n["ir"], backend) for n in ir["nodes"]]
    return {"fmax_mhz": min(e["fmax_mhz"] for e in ests), "lut": sum(e["lut"] for e in ests) + 40 * (len(ests) - 1),
            "dsp": sum(e["dsp"] for e in ests), "throughput": min(e["throughput"] for e in ests)}


ESTIMATORS = {"matmul": estimate_matmul, "fir": estimate_fir, "conv2d": estimate_conv, "fir_chain": estimate_chain, "cordic": estimate_cordic, "fft": estimate_fft}
ESTIMATORS["graph"] = estimate_graph


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
    elif design == "fir":
        f += [math.log2(ir["taps"]), math.log2(ir.get("fold", 1)), 1.0 if ir.get("symmetric") else 0.0, ir["acc_width"] / 40,
              1.0 if ir.get("dsp_regs", "none") != "none" else 0.0, 1.0 if ir.get("acc_in_dsp", True) else 0.0, ir.get("pipe_mul", 0)]
    elif design == "cordic":
        f += [ir["width"] / 16, ir["iterations"] / 16, math.log2(ir.get("unroll", 1) + 0.0) if ir.get("unroll", 1) > 0 else 0, 1.0 if ir.get("fold", 1) > 1 else 0.0,
              ir.get("guard_bits", 0) / 4, 1.0 if ir.get("scale_comp") else 0.0, 0.0]
    elif design == "fft":
        f += [math.log2(ir["n"]), ir["data_width"] / 16, 1.0 if ir.get("growth") == "grow" else 0.0, ir.get("mul_regs", 0) + {"none": 0, "out": 1, "in_out": 2, "in_pipe_out": 3}.get(ir.get("dsp_regs", "none"), 0),
              float(ir.get("bf_regs", 1)), 1.0 if ir.get("delay_mem") == "lut" else 0.0, 1.0 if ir.get("bit_reverse") else 0.0,
              {"four": 4.0, "three": 3.0, "lut": 0.0}[ir.get("cmul", "four")]]
    elif design == "graph":
        nodes = ir["nodes"]
        f += [float(len(nodes)), sum(1.0 for n in nodes if n["kind"] == "fir"), sum(1.0 for n in nodes if n["kind"] == "fft"), sum(1.0 for n in nodes if n["kind"] == "cordic"),
              sum(1.0 for n in nodes if n["kind"] == "conv2d"), math.log2(ir.get("fifo_depth", 16)), sum(1.0 for n in nodes if n["ir"].get("dsp_regs", "none") != "none") / len(nodes)]
    elif design == "fir_chain":
        st = ir["stages"]
        f += [math.log2(sum(s["taps"] for s in st)), math.log2(max(s.get("fold", 1) for s in st)), sum(1.0 for s in st if s.get("symmetric")) / len(st),
              max(s["acc_width"] for s in st) / 40, sum(1.0 for s in st if s.get("dsp_regs", "none") != "none") / len(st), float(len(st)), max(s.get("pipe_mul", 0) for s in st)]
    else:
        f += [math.log2(ir["k"] * ir.get("par_rows", ir["k"])), math.log2(ir["width"]), 1.0 if ir.get("line_mem") == "bram" else 0.0,
              ir["acc_width"] / 32, ir.get("tree_regs", 0), 1.0 if ir.get("dsp_regs", "none") != "none" else 0.0, ir.get("pipe_mul", 0)]
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


def _knn(design: str, backend: str, f: list[float], k: int = 4) -> list[tuple[float, dict]]:
    """Nearest measured observations in (standardized) feature space, as (distance, obs)."""
    obs = [o for o in _load_obs(design, backend) if o["measured"]["fmax_mhz"] > 0 and len(o["features"]) == len(f)]
    if len(obs) < 3:
        return []
    cols = list(zip(*[o["features"] for o in obs]))
    sd = [max((sum((x - sum(c) / len(c)) ** 2 for x in c) / len(c)) ** 0.5, 1e-6) for c in cols]
    def dist(a):
        return sum(((x - y) / s_) ** 2 for x, y, s_ in zip(a, f, sd)) ** 0.5
    return sorted(((dist(o["features"]), o) for o in obs), key=lambda t: t[0])[:k]


def calibrated(design: str, backend: str, ir: dict) -> dict:
    """Estimate = closed form corrected by (a) the ridge fit when it beats raw and (b) the nearest measured
    designs: each neighbour contributes its own measured/estimate ratio, weighted by 1/(d+0.3), and the
    neighbour term dominates when the nearest point is close (d < 1 in standardized units). An exact
    feature match (d ~ 0) therefore returns the measured value -- the same design under another spec."""
    e = ESTIMATORS[design](ir, backend)
    if backend == "sky130":
        # standard cells: every multiplier the FPGA estimate would put in a DSP becomes ~250 adder cells; the area
        # follows the cell count (sky130hd cells average ~9 um2 incl. the ~0.46 utilisation measured on a FIR)
        cells = e["lut"] + 250 * e["dsp"]
        e = {**e, "lut": cells, "dsp": 0, "area_um2": int(cells * 9)}
    fit = _fit(design, backend)
    f = _features(design, ir, backend)
    out = dict(e)
    nn = _knn(design, backend, f)
    for metric in ("fmax_mhz", "lut", "dsp"):
        corr_fit = math.exp(max(-1.5, min(1.5, sum(wi * xi for wi, xi in zip(fit[metric]["w"], f))))) if fit else 1.0
        corr = corr_fit
        if nn:
            w = [1.0 / (d + 0.3) for d, _ in nn]
            ratios = [math.log(max(o["measured"][metric], 1) / max(o["estimate"][metric], 1)) for _, o in nn]
            corr_nn = math.exp(max(-1.5, min(1.5, sum(wi * r for wi, r in zip(w, ratios)) / sum(w))))
            d0 = nn[0][0]
            alpha = 1.0 / (1.0 + d0)  # 1 at an exact match, 0.5 at distance 1, -> 0 far away
            corr = math.exp(alpha * math.log(corr_nn) + (1 - alpha) * math.log(corr_fit))
        out[metric] = e[metric] * corr if metric == "fmax_mhz" else int(round(e[metric] * corr))
    out["calibrated"] = bool(fit or nn)
    out["n_obs"] = fit["n"] if fit else len(_load_obs(design, backend))
    if nn:
        out["nearest"] = round(nn[0][0], 2)
    return out


def calibration_report(design: str, backend: str) -> dict:
    """How good the estimator is right now: raw, ridge-only (LOO), and ridge+nearest-neighbour (LOO)."""
    obs = [o for o in _load_obs(design, backend) if o["measured"]["fmax_mhz"] > 0]
    fit = _fit(design, backend)
    rep = {"n": len(obs), "calibrated": bool(fit)}
    for metric in ("fmax_mhz", "lut", "dsp"):
        raw = [abs(math.log(max(o["measured"][metric], 1) / max(o["estimate"][metric], 1))) for o in obs]
        rep[metric] = {"raw_median_err_pct": round((math.exp(sorted(raw)[len(raw) // 2]) - 1) * 100, 1) if raw else None,
                       "raw_rmse_pct": round((math.exp(fit[metric]["raw_rmse_log"]) - 1) * 100, 1) if fit else None,
                       "loo_rmse_pct": round((math.exp(fit[metric]["rmse_log"]) - 1) * 100, 1) if fit else None,
                       "applied": bool(fit and fit[metric]["lam"] is not None)}
    # leave-one-out of the knn blend: drop each observation, predict it from the rest
    if len(obs) >= 4:
        for metric in ("fmax_mhz", "lut", "dsp"):
            errs = []
            for i, o in enumerate(obs):
                rest = obs[:i] + obs[i + 1:]
                cols = list(zip(*[r["features"] for r in rest]))
                sd = [max((sum((x - sum(c) / len(c)) ** 2 for x in c) / len(c)) ** 0.5, 1e-6) for c in cols]
                nn = sorted(((sum(((x - y) / s_) ** 2 for x, y, s_ in zip(r["features"], o["features"], sd)) ** 0.5, r) for r in rest), key=lambda t: t[0])[:4]
                w = [1.0 / (d + 0.3) for d, _ in nn]
                ratios = [math.log(max(r["measured"][metric], 1) / max(r["estimate"][metric], 1)) for _, r in nn]
                corr_nn = sum(wi * r for wi, r in zip(w, ratios)) / sum(w)
                corr_fit = sum(wi * xi for wi, xi in zip(fit[metric]["w"], o["features"])) if fit else 0.0
                alpha = 1.0 / (1.0 + nn[0][0])
                pred = math.log(max(o["estimate"][metric], 1)) + alpha * corr_nn + (1 - alpha) * corr_fit
                errs.append(pred - math.log(max(o["measured"][metric], 1)))
            rep[metric]["loo_knn_rmse_pct"] = round((math.exp((sum(x * x for x in errs) / len(errs)) ** 0.5) - 1) * 100, 1)
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
        if c.max_area_um2 is not None and e.get("area_um2") and e["area_um2"] > c.max_area_um2:
            viol.append(f"area~{e['area_um2']}>{c.max_area_um2}")
        key = []
        for obj in c.objective:
            key.append({"maximize_throughput": -e["throughput"], "maximize_fmax": -e["fmax_mhz"],
                        "minimize_lut": e["lut"], "minimize_dsp": e["dsp"], "minimize_area": e.get("area_um2", e["lut"])}.get(obj, 0))  # minimize_power/latency: not estimated
        rows.append({**cand, "estimate": e, "est_violations": viol, "_sort": (len(viol) > 0, miss, tuple(key))})
    rows.sort(key=lambda r: r["_sort"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        del r["_sort"]
    return rows
