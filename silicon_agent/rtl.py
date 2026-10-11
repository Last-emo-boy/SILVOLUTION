"""Bring-your-own RTL: the same ladder (behavioral sim -> synth/P&R -> netlist sims -> formal) for a design the user
wrote, with the planner tuning what such RTL exposes -- its top-level parameters.

The "generator" is the user's SystemVerilog: `emit` returns it with the parameter overrides the IR carries applied
as a `defparam`-free wrapper (a `module <top>` is not edited; a thin top `rtl_top` instantiates it with #(...)), so
the planner moves parameters (pipeline depths, widths, unroll factors the author exposed) and every measurement is
on the user's code. The testbench is the user's too: a self-checking SystemVerilog bench that instantiates
`<top>`, drives clk/rst, and prints `RESULT PASS` or `RESULT FAIL ...`; the same bench runs on the behavioral RTL
and on the post-synthesis / post-route netlists (the netlist's module keeps the top's name).

The user uploads a bundle (POST /api/rtl): {"name", "top", "files": {"x.sv": "...", ...}, "tb": "tb.sv",
"params": {"NAME": {"default":, "min":, "max":, "hint":}}, "clk": "clk", "rst": "rst"}; the server stores it under
SA_ROOT/rtl/<id>/ and registers a design kind `rtl:<id>` in the registry. Specs then use design "rtl:<id>" with
locked = {} and initial_ir = the parameter values.

Formal: the properties check applies as-is (ports are discovered from the RTL); equivalence compares two parameter
sets only when the user asks (different params are different designs, so the default equivalence of a run is
"measured RTL vs a fresh emit", which is trivially the same text).

Authored bundles (studio.py): the agent's designer session writes the RTL. Such a bundle carries
  authored    true
  stream      {"in", "out", "in_width", "out_width", "rate"}: the standard stream interface the RTL follows
              (clk/rst/in_valid/in_ready/<in>/out_valid/<out>), widths and rate as expressions of the parameters;
              null when the design is not a single stream
  reference   {"kind": "independent" | "generator", "code": <python with reference(params, xs)>, "origin": {...}}:
              where the expected outputs come from -- a model written by a session that never saw the RTL, or the
              generator's own vectors when the design was forked from a generator's iteration
  revisions   [{"rev", "files", "note", "created", "check"}, ...]: the RTL as the designer revised it; the IR's `rev`
              selects one, so a run can move between revisions like between knob values
For a stream bundle the testbench is the generic vector bench (vectors.TB_FIR with the top and widths substituted)
replaying stim/exp produced by the reference -- the check is independent of whoever wrote the RTL.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RtlArch:
    """Parameter overrides for a user top. `params` is name -> value (int); the bundle declares ranges. One subclass
    per bundle (see `arch_class`) so `from_dict` knows its bundle like the generators' IR classes know theirs."""
    params: dict = field(default_factory=dict)
    rev: int = 0   # which revision of an authored bundle's RTL (0 for an uploaded bundle)
    _bundle: dict = field(default_factory=dict, compare=False, repr=False)

    def validate(self) -> list[str]:
        errs = []
        decl = (self._bundle.get("params") or {})
        n_rev = len(self._bundle.get("revisions") or []) or 1
        if not isinstance(self.rev, int) or isinstance(self.rev, bool) or not 0 <= self.rev < n_rev:
            errs.append(f"rev must be in 0..{n_rev - 1}")
        for k, v in self.params.items():
            if k not in decl:
                errs.append(f"unknown parameter {k}; the bundle exposes {sorted(decl)}")
                continue
            d = decl[k]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                errs.append(f"{k} must be a number")
                continue
            if "choices" in d and v not in d["choices"]:
                errs.append(f"{k} must be one of {d['choices']}")
            if "min" in d and v < d["min"]:
                errs.append(f"{k} must be >= {d['min']}")
            if "max" in d and v > d["max"]:
                errs.append(f"{k} must be <= {d['max']}")
        return errs

    def to_dict(self) -> dict:
        d = dict(self.params)
        if self._bundle.get("revisions"):
            d["rev"] = self.rev
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RtlArch":
        b = getattr(cls, "_bundle_ref", {})
        decl = b.get("params") or {}
        params = {k: int(v.get("default", 0)) for k, v in decl.items()}
        params.update({k: v for k, v in d.items() if not k.startswith("_") and k in decl})
        rev = d.get("rev", 0)
        return cls(params=params, rev=int(rev) if isinstance(rev, (int, float)) and not isinstance(rev, bool) else rev, _bundle=b)

    def digest(self, backend: str = "ecp5") -> str:
        import hashlib
        return hashlib.sha256(json.dumps({"params": self.params, "rev": self.rev, "rtl": self._bundle.get("hash")}, sort_keys=True).encode()).hexdigest()[:12]


def arch_class(bundle: dict) -> type:
    """The IR class of one bundle: RtlArch bound to the bundle's parameter declarations."""
    return type(f"RtlArch_{bundle.get('id', 'x')}", (RtlArch,), {"_bundle_ref": bundle})


def bundle_hash(bundle: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps({k: bundle.get(k) for k in ("top", "files", "tb", "revisions")}, sort_keys=True).encode()).hexdigest()[:12]


def files_of(bundle: dict, rev: int = 0) -> dict:
    """The RTL files of one revision (an uploaded bundle has a single, unnumbered set)."""
    revs = bundle.get("revisions") or []
    if revs and 0 <= rev < len(revs):
        return revs[rev]["files"]
    return bundle["files"]


def emit(bundle: dict, a: RtlArch, backend: str = "ecp5") -> str:
    """The user's files concatenated, parameters applied through a `defparam`-free wrapper named like the top
    (the original top is renamed <top>_user so the ladder's `<top>` keeps the user's port names)."""
    top = bundle["top"]
    files = files_of(bundle, a.rev)
    src = "\n\n".join(f"// ---- {name}\n{text}" for name, text in sorted(files.items()))
    user = f"{top}_user"
    src = re.sub(rf"\bmodule\s+{re.escape(top)}\b", f"module {user}", src, count=1)
    ports = _ports(bundle, a.rev)
    decl = (bundle.get("params") or {})
    pv = {k: int(decl[k].get("default", 0)) for k in decl}
    pv.update({k: int(v) for k, v in a.params.items()})
    pstr = ", ".join(f".{k}({v})" for k, v in pv.items())
    port_decl = ",\n".join(f"    {d} {t} {n}" for d, t, n in ports)
    conn = ", ".join(f".{n}({n})" for _, _, n in ports)
    header = f"// Generated by silicon-agent -- BYO-RTL wrapper. IR: {json.dumps(a.to_dict())}\n"
    return f"""{header}{src}

// ---- parameter wrapper (silicon-agent): the user's top with this iteration's parameters
module {top} (
{port_decl}
);
    {user} #({pstr}) u ({conn});
endmodule
"""


def _ports(bundle: dict, rev: int = 0) -> list[tuple[str, str, str]]:
    """(dir, type, name) of the user top's ports from its ANSI header; widths may reference parameters, which is
    fine because the wrapper declares the same parameters' defaults... except it does not: the wrapper is parameter
    free, so widths are re-expressed with the chosen values at emit time by the caller when they are parametric.
    Here: ports with a literal width or no width are kept; parametric widths are declared with the bundle's
    `port_widths` override (name -> expression) when provided, else as the max over the parameter range."""
    top = bundle["top"]
    text = "\n".join(files_of(bundle, rev).values())
    m = re.search(rf"\bmodule\s+{re.escape(top)}\b\s*(#\s*\((?:[^()]|\([^()]*\))*\))?\s*\((.*?)\)\s*;", text, re.S)
    if not m:
        raise ValueError(f"cannot find an ANSI port list for module {top}")
    body = re.sub(r"//.*", "", m.group(2))
    out = []
    cur_dir, cur_type = "input", "logic"
    for item in [x.strip() for x in body.split(",") if x.strip()]:
        mm = re.match(r"(input|output|inout)?\s*(wire|logic|reg)?\s*(signed)?\s*(\[[^\]]+\])?\s*(\w+)\s*$", item)
        if not mm:
            raise ValueError(f"port declaration not understood: {item!r}")
        d, t, sg, w, n = mm.groups()
        if d:
            cur_dir = d
        cur_type = "logic" + (" signed" if sg else "") + (f" {w}" if w else "")
        out.append((cur_dir, cur_type, n))
    widths = bundle.get("port_widths") or {}
    out = [(d, (f"logic {widths[n]}" if n in widths else t), n) for d, t, n in out]
    # a width written in terms of the top's own parameters is resolved with the parameter defaults declared in the
    # header (a generator's top declares every parameter with a literal), so the parameter-free wrapper elaborates
    defaults = header_params(text, top)
    if defaults:
        env = {**defaults, **{k: int(v.get("default", 0)) for k, v in (bundle.get("params") or {}).items()}}
        out = [(d, _resolve_width(t, env), n) for d, t, n in out]
    return out


def header_params(text: str, top: str) -> dict:
    """{name: value} of the `parameter int X = <literal>` list in the top's header."""
    m = re.search(rf"\bmodule\s+{re.escape(top)}\b\s*#\s*\(((?:[^()]|\([^()]*\))*)\)", text, re.S)
    if not m:
        return {}
    out: dict = {}
    for name, val in re.findall(r"parameter\s+(?:int|integer|logic|bit)?\s*(?:\[[^\]]*\]\s*)?(\w+)\s*=\s*([^,)\n]+)", m.group(1)):
        try:
            out[name] = int(eval(val.strip().rstrip(","), {"__builtins__": {}}, dict(out)))
        except Exception:
            pass
    return out


def _resolve_width(t: str, env: dict) -> str:
    m = re.search(r"\[([^\]]+)\]", t)
    if not m or re.fullmatch(r"\s*\d+\s*:\s*\d+\s*", m.group(1)):
        return t
    hi, _, lo = m.group(1).partition(":")
    try:
        h = int(eval(hi, {"__builtins__": {}}, dict(env)))
        l = int(eval(lo, {"__builtins__": {}}, dict(env))) if lo.strip() else 0
    except Exception:
        return t
    return t[:m.start()] + f"[{h}:{l}]" + t[m.end():]


def _env(bundle: dict, a: RtlArch) -> dict:
    """The parameter values an expression of the bundle may refer to (defaults, then this IR's values)."""
    return {**{k: int(v.get("default", 0)) for k, v in (bundle.get("params") or {}).items()}, **a.params}


def _clog2(x) -> int:
    """Verilog's $clog2 (its argument may be a true-division result such as 16/F)."""
    x = int(round(x))
    return 0 if x <= 1 else (x - 1).bit_length()


# Verilog-flavoured helpers a bundle's expressions may use (the designer writes them like RTL parameters)
_EXPR_FNS = {"clog2": _clog2, "max": max, "min": min, "abs": abs, "int": int, "ceil": lambda v: int(-(-v // 1)), "log2": lambda v: _clog2(v)}


def expr_value(expr, env: dict, default):
    """A bundle field that is a number or an arithmetic expression of the parameters (`"DW + 8"`, `"F + $clog2(16/F)"`)."""
    if isinstance(expr, bool):
        return default
    if isinstance(expr, (int, float)):
        return expr
    if isinstance(expr, str) and expr.strip():
        try:
            return eval(expr.replace("$clog2", "clog2"), {"__builtins__": {}}, {**_EXPR_FNS, **env})
        except Exception:
            return default
    return default


def latency_cycles(bundle: dict, a: RtlArch) -> int:
    return int(expr_value(bundle.get("latency"), _env(bundle, a), 8))


def throughput(bundle: dict, a: RtlArch) -> float:
    return float(expr_value(bundle.get("throughput"), _env(bundle, a), 1.0))


# ---------------------------------------------------------------- authored bundles: the reference and the generic bench
def stream_of(bundle: dict, a: RtlArch) -> dict | None:
    """The stream interface with widths and rate resolved for this IR, or None for a design that is not one stream."""
    st = bundle.get("stream")
    if not st:
        return None
    env = _env(bundle, a)
    return {"in": st.get("in", "in_data"), "out": st.get("out", "out_data"),
            "in_width": int(expr_value(st.get("in_width"), env, 16)), "out_width": int(expr_value(st.get("out_width"), env, 16)),
            "rate": int(expr_value(st.get("rate"), env, 1))}


def stimulus(a: RtlArch, dw: int, seed: int = 1, n: int = 200) -> tuple[list[int], list[int]]:
    """Signed input samples and the idle gaps before each (the vectors.fir_vectors shape: extremes first, then random,
    gaps in the first half only so the back-to-back half catches full-rate bugs)."""
    import random
    rng = random.Random(seed)
    lo, hi = -(1 << (dw - 1)), (1 << (dw - 1)) - 1
    xs = [hi] * 8 + [lo] * 8 + [rng.randint(lo, hi) for _ in range(n)]
    gaps = [0 if i >= len(xs) - n // 2 else rng.choice([0, 0, 1, 3, 8]) for i in range(len(xs))]
    return xs, gaps


def run_model(code: str, params: dict, xs: list[int], cwd: Path, timeout: int = 60, name: str = "reference") -> list[int]:
    """Run a model (`def reference(params, xs)` or `def golden(params, xs)`) in a separate, isolated interpreter
    (`python -I`: no site, no env), with a time limit. The code was written by a model; it never runs in the server."""
    import subprocess
    import sys
    import tempfile
    cwd.mkdir(parents=True, exist_ok=True)
    src = Path(tempfile.mkdtemp(prefix=f"sa_{name}_", dir=cwd))
    (src / "model.py").write_text(code)
    (src / "in.json").write_text(json.dumps({"params": params, "xs": xs}))
    driver = (
        "import json, sys\n"
        "d = json.load(open('in.json'))\n"
        "ns = {}\n"
        "exec(compile(open('model.py').read(), 'model.py', 'exec'), ns)\n"
        f"fn = ns.get({name!r}) or ns.get('reference') or ns.get('golden')\n"
        "if fn is None: raise SystemExit('no reference()/golden() defined')\n"
        "out = fn(d['params'], d['xs'])\n"
        "json.dump([int(v) for v in out], open('out.json', 'w'))\n"
    )
    p = subprocess.run([sys.executable, "-I", "-c", driver], cwd=src, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{name} model failed: {(p.stderr or p.stdout)[-800:]}")
    return json.loads((src / "out.json").read_text())


def compare_models(golden: str, reference: str, params: dict, dw: int, cwd: Path, seeds: tuple[int, ...] = (1, 2, 3)) -> dict:
    """The designer's golden against the independent reference, Python against Python, before any EDA time is
    spent: {"agree", "samples", "mismatch": {"seed", "index", "golden", "reference"} | None, "error"}."""
    total = 0
    for seed in seeds:
        xs, _ = stimulus(RtlArch(), dw, seed)
        try:
            g = run_model(golden, params, xs, cwd, name="golden")
            r = run_model(reference, params, xs, cwd, name="reference")
        except (RuntimeError, ValueError) as e:
            return {"agree": False, "samples": total, "mismatch": None, "error": str(e)[-800:]}
        n = min(len(g), len(r))
        for i in range(n):
            if g[i] != r[i]:
                return {"agree": False, "samples": total + i, "mismatch": {"seed": seed, "index": i, "golden": g[i], "reference": r[i]}, "error": None}
        if len(g) != len(r):
            return {"agree": False, "samples": total + n, "mismatch": {"seed": seed, "index": n, "golden": len(g), "reference": len(r), "note": "output counts differ"}, "error": None}
        total += n
    return {"agree": True, "samples": total, "mismatch": None, "error": None}


def vectors(bundle: dict, a: RtlArch, seed: int = 1, cwd: Path | None = None) -> tuple[list[str], list[str]]:
    """stim/exp lines for the generic vector bench of a stream bundle; the expected outputs come from the bundle's
    reference (an independent model, or the generator the design was forked from), never from the RTL's author."""
    st = stream_of(bundle, a)
    if not st:
        raise ValueError("not a stream bundle")
    ref = bundle.get("reference") or {}
    if ref.get("kind") == "generator":
        from .designs import DESIGNS
        o = ref["origin"]
        d = DESIGNS[o["design"]]
        return d.vectors(d.ir_cls.from_dict(o["ir"]), seed=seed)
    if ref.get("kind") != "independent" or not ref.get("code"):
        raise ValueError("the bundle has no reference model")
    dw, ow = st["in_width"], st["out_width"]
    xs, gaps = stimulus(a, dw, seed)
    work = cwd or Path(bundle.get("dir") or ".") / "ref-cache"
    ys = run_model(ref["code"], _env(bundle, a), xs, work)
    stim = [f"{g:x} {x & ((1 << dw) - 1):0{max(1, (dw + 3) // 4)}x}" for g, x in zip(gaps, xs)]
    exp = [f"{y & ((1 << ow) - 1):0{max(1, (ow + 3) // 4)}x}" for y in ys]
    return stim, exp


def stream_tb(bundle: dict, a: RtlArch, period: float) -> str:
    """The generic replay bench (vectors.TB_FIR) aimed at this bundle's top and stream ports: stimulus on negedge,
    outputs sampled 3/4 period after posedge, so it also runs on SDF-annotated netlists."""
    from .vectors import TB_FIR
    st = stream_of(bundle, a)
    drain = 300 + 4 * max(latency_cycles(bundle, a), 1) * max(st["rate"], 1)
    tb = (TB_FIR.replace("fir_top dut(.clk, .rst, .in_valid, .in_data, .in_ready, .out_valid, .out_data{COEF_TIE});",
                         f"{bundle['top']} dut(.clk(clk), .rst(rst), .in_valid(in_valid), .{st['in']}(in_data), .in_ready(in_ready), .out_valid(out_valid), .{st['out']}(out_data));")
          .replace("repeat (300) @(posedge clk);", f"repeat ({drain}) @(posedge clk);"))
    return tb.format(DW=st["in_width"], AW=st["out_width"], period=period, COEF_TIE="")


def ir_schema(bundle: dict) -> str:
    lines = ["IR fields and legal ranges (the parameters this RTL exposes; the task fixes everything else):"]
    for k, d in (bundle.get("params") or {}).items():
        rng = f"choices {d['choices']}" if "choices" in d else f"int {d.get('min', '?')}..{d.get('max', '?')}"
        lines.append(f"  {k:12s} {rng:18s} {d.get('hint', '')} (default {d.get('default')})")
    revs = bundle.get("revisions") or []
    if len(revs) > 1:
        lines.append(f"  {'rev':12s} {'int 0..' + str(len(revs) - 1):18s} which revision of the RTL (the designer's rewrites): " +
                     "; ".join(f"{r['rev']}: {str(r.get('note', ''))[:80]}" for r in revs))
    if bundle.get("authored"):
        lines.append("This RTL was written by the designer session for this task. Besides the knobs you may ask for a REWRITE of the RTL when no knob reaches the "
                     "critical path: add \"revise\": \"<what to change and why, citing the critical path>\" to your reply; the designer rewrites, the result is re-checked "
                     "against the independent reference and comes back as a new `rev` for the next iteration. Use it only when the knobs clearly cannot help.")
    if bundle.get("notes"):
        lines.append("Author's notes: " + str(bundle["notes"])[:1500])
    return "\n".join(lines)
