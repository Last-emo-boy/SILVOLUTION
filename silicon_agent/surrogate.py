"""Surrogate + acquisition over the observation log: Bayesian-optimization style ranking of candidate IRs.

The planner (LLM) proposes candidates; `screen.calibrated` gives each a point estimate. This module adds what
the closed form lacks: an uncertainty. A Gaussian process (RBF kernel on the standardized feature vector) is fitted
to log(measured / estimate) per metric -- the residual of the closed form, which is smooth across the design space
(the closed form already carries the big structural trends). Prediction = estimate * exp(mu +- sigma).

The acquisition is expected improvement of the first objective subject to the constraints being met with some
probability (constrained EI): a candidate close to measured points gets a tight band; a candidate in unexplored
space gets a wide band and so a real chance of being better than the incumbent. The loop uses this in two ways:
  - `rank(...)`: order the planner's candidates by acquisition instead of the point estimate alone;
  - `explore(...)`: propose extra candidates by local moves of the incumbent's knobs (the kinds the IR schema
    declares) and keep the ones with the highest acquisition -- the planner is the global proposer, this is the
    local one that never stops looking in the neighbourhood of the best point.

No numpy on the server: the GP is a plain-Python Cholesky on at most a few hundred points, which is what one
(design, device) pair accumulates.
"""

from __future__ import annotations

import math
import random

from . import screen
from .ir import Constraints

MIN_OBS = 4


# ---------------------------------------------------------------- tiny linear algebra
def _chol(A: list[list[float]]) -> list[list[float]]:
    n = len(A)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = A[i][j] - sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                L[i][j] = math.sqrt(max(s, 1e-10))
            else:
                L[i][j] = s / L[j][j]
    return L


def _solve_chol(L: list[list[float]], b: list[float]) -> list[float]:
    n = len(L)
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(L[i][k] * y[k] for k in range(i))) / L[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(L[k][i] * x[k] for k in range(i + 1, n))) / L[i][i]
    return x


# ---------------------------------------------------------------- the GP on the residual
class _GP:
    def __init__(self, X: list[list[float]], y: list[float], ls: float = 1.0, noise: float = 0.05):
        self.X = X
        self.mean = sum(y) / len(y)
        self.y = [v - self.mean for v in y]
        self.ls = ls
        var = max(sum(v * v for v in self.y) / len(y), 1e-4)
        self.sf2 = var
        K = [[self.sf2 * self._k(a, b) + (noise if i == j else 0.0) for j, b in enumerate(X)] for i, a in enumerate(X)]
        self.L = _chol(K)
        self.alpha = _solve_chol(self.L, self.y)

    def _k(self, a: list[float], b: list[float]) -> float:
        d2 = sum((x - y) ** 2 for x, y in zip(a, b))
        return math.exp(-0.5 * d2 / (self.ls * self.ls))

    def predict(self, x: list[float]) -> tuple[float, float]:
        k = [self.sf2 * self._k(x, xi) for xi in self.X]
        mu = self.mean + sum(ki * ai for ki, ai in zip(k, self.alpha))
        v = _solve_chol(self.L, k)
        var = self.sf2 - sum(ki * vi for ki, vi in zip(k, v))
        return mu, math.sqrt(max(var, 1e-8))


_cache: dict = {}


def _model(design: str, backend: str):
    """One GP per metric on the standardized features; None without enough observations."""
    key = (design, backend)
    obs = screen._load_obs(design, backend)
    obs = [o for o in obs if o["measured"]["fmax_mhz"] > 0 and o["estimate"]["fmax_mhz"] > 0]
    if key in _cache and _cache[key][0] == len(obs):
        return _cache[key][1]
    if len(obs) < MIN_OBS:
        _cache[key] = (len(obs), None)
        return None
    X = [o["features"][1:] for o in obs]  # drop the bias
    n = len(X[0])
    mean = [sum(x[j] for x in X) / len(X) for j in range(n)]
    sd = [max((sum((x[j] - mean[j]) ** 2 for x in X) / len(X)) ** 0.5, 1e-6) for j in range(n)]
    Z = [[(x[j] - mean[j]) / sd[j] for j in range(n)] for x in X]
    gps = {}
    for metric in ("fmax_mhz", "lut"):
        y = [math.log(max(o["measured"][metric], 1) / max(o["estimate"][metric], 1)) for o in obs]
        gps[metric] = _GP(Z, y, ls=1.2)
    m = {"gps": gps, "mean": mean, "sd": sd, "n": len(obs)}
    _cache[key] = (len(obs), m)
    return m


def predict(design: str, backend: str, ir: dict) -> dict | None:
    """{metric: (median, lo, hi)} from estimate * exp(mu +- sigma); None without a model."""
    m = _model(design, backend)
    if not m:
        return None
    e = screen.estimator(design)(ir, backend)
    f = screen._features(design, ir, backend)[1:]
    z = [(f[j] - m["mean"][j]) / m["sd"][j] for j in range(len(f))]
    out = {}
    for metric, gp in m["gps"].items():
        mu, sd = gp.predict(z)
        base = max(e[metric], 1)
        out[metric] = (base * math.exp(mu), base * math.exp(mu - sd), base * math.exp(mu + sd), sd)
    out["n_obs"] = m["n"]
    return out


def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _Phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def acquisition(design: str, backend: str, ir: dict, c: Constraints, incumbent: dict | None, throughput: float | None = None) -> dict | None:
    """Constrained expected improvement on the first objective. Returns {"ei", "p_feasible", "pred"} or None."""
    p = predict(design, backend, ir)
    if not p:
        return None
    # resources the closed form counts exactly (multipliers -> DSPs) are hard: a candidate over the DSP/BRAM budget
    # has no chance regardless of what the GP thinks about timing
    e = screen.estimator(design)(ir, backend)
    if (c.max_dsp is not None and e.get("dsp", 0) > c.max_dsp) or (c.max_bram is not None and e.get("bram", 0) > c.max_bram):
        return {"ei": 0.0, "p_feasible": 0.0, "score": 0.0, "pred": {k: [round(v, 1) for v in p[k][:3]] for k in ("fmax_mhz", "lut")}, "n_obs": p["n_obs"]}
    # feasibility: fmax >= target (lognormal), lut <= max_lut
    mu_f, sd_f = math.log(p["fmax_mhz"][0]), p["fmax_mhz"][3]
    pf = 1 - _Phi((math.log(max(c.fmax_mhz, 1)) - mu_f) / max(sd_f, 1e-3))
    if c.max_lut is not None:
        mu_l, sd_l = math.log(max(p["lut"][0], 1)), p["lut"][3]
        pf *= _Phi((math.log(c.max_lut) - mu_l) / max(sd_l, 1e-3))
    obj = (c.objective or ["maximize_throughput"])[0]
    # the improvement target: maximize_fmax -> fmax; minimize_lut/area -> lut; throughput is deterministic
    # (the IR fixes it) so EI reduces to feasibility weighting of the throughput gain
    if obj == "maximize_fmax":
        best = incumbent["metrics"]["fmax_mhz"] if incumbent else c.fmax_mhz
        mu, sd = mu_f, sd_f
        z = (mu - math.log(max(best, 1))) / max(sd, 1e-3)
        ei = sd * (z * _Phi(z) + _phi(z))
    elif obj in ("minimize_lut", "minimize_area"):
        best = incumbent["metrics"]["lut"] if incumbent else p["lut"][0] * 2
        mu, sd = math.log(max(p["lut"][0], 1)), p["lut"][3]
        z = (math.log(max(best, 1)) - mu) / max(sd, 1e-3)
        ei = sd * (z * _Phi(z) + _phi(z))
    else:
        best = incumbent["throughput"] if incumbent else 0.0
        ei = max(0.0, (throughput or 0.0) - best) / max(best, 1e-3) + 0.05 * sd_f
    return {"ei": ei, "p_feasible": pf, "score": ei * pf, "pred": {k: [round(v, 1) for v in p[k][:3]] for k in ("fmax_mhz", "lut")}, "n_obs": p["n_obs"]}


def explore(design: str, backend: str, incumbent_ir: dict, locked: dict, c: Constraints, incumbent: dict | None, ir_cls, seen: set, schema: dict, n: int = 6, rng=None) -> list[dict]:
    """Local proposals around the incumbent: one-knob moves over the schema's declared ranges/choices, scored by the
    acquisition; the best `n` legal, unseen ones. Empty without a model (then the planner is on its own)."""
    if not _model(design, backend):
        return []
    rng = rng or random.Random(0)
    fields = [f for f in (schema.get("fields") or []) if f["name"] not in locked and f["name"] in incumbent_ir]
    cands = []
    for f in fields:
        name, cur = f["name"], incumbent_ir[f["name"]]
        moves = []
        if f["type"] == "bool":
            moves = [not cur]
        elif f["type"] == "choice":
            moves = [v for v in f["choices"] if v != cur]
        elif f["type"] == "int":
            lo, hi = f.get("min", cur), f.get("max", cur)
            moves = sorted({max(lo, min(hi, v)) for v in (cur - 1, cur + 1, cur // 2, cur * 2)} - {cur})
        for v in moves[:4]:
            ir = {**incumbent_ir, name: v}
            try:
                a = ir_cls.from_dict(ir)
            except Exception:
                continue
            if a.validate() or a.digest(backend) in seen:
                continue
            acq = acquisition(design, backend, a.to_dict(), c, incumbent)
            if acq:
                cands.append({"ir": a.to_dict(), "why": f"local move {name}: {cur} -> {v} (surrogate: fmax~{acq['pred']['fmax_mhz'][0]:.0f} [{acq['pred']['fmax_mhz'][1]:.0f}-{acq['pred']['fmax_mhz'][2]:.0f}], p_feasible {acq['p_feasible']:.2f})", "acq": acq})
    cands.sort(key=lambda x: -x["acq"]["score"])
    return cands[:n]
