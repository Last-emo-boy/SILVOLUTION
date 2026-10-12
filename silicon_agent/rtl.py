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


def _clog2(x) -> int:
    """Verilog's $clog2 (its argument may be a true-division result such as 16/F)."""
    x = int(round(x))
    return 0 if x <= 1 else (x - 1).bit_length()


# Verilog-flavoured helpers a bundle's expressions may use (the designer writes them like RTL parameters)
_EXPR_FNS = {"clog2": _clog2, "max": max, "min": min, "abs": abs, "int": int, "ceil": lambda v: int(-(-v // 1)), "log2": lambda v: _clog2(v)}


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
        h = int(eval(hi.replace("$clog2", "clog2"), {"__builtins__": {}}, {**_EXPR_FNS, **env}))
        l = int(eval(lo.replace("$clog2", "clog2"), {"__builtins__": {}}, {**_EXPR_FNS, **env})) if lo.strip() else 0
    except Exception:
        return t
    return t[:m.start()] + f"[{h}:{l}]" + t[m.end():]


def _env(bundle: dict, a: RtlArch) -> dict:
    """The parameter values an expression of the bundle may refer to (defaults, then this IR's values)."""
    return {**{k: int(v.get("default", 0)) for k, v in (bundle.get("params") or {}).items()}, **a.params}


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
    """The stream interface with widths and rate resolved for this IR, or None for a design that is not a stream.

    The general form is a list of input fields and a list of output fields, each {"name", "width", "signed"}, all
    presented on one accepted beat (inputs) / one valid beat (outputs): a complex sample is two fields, a
    frame_start bit is an unsigned 1-bit field, a side-band coefficient is another field. The shorthand
    {"in": "in_data", "out": "out_data", "in_width", "out_width"} is the one-field case.
    """
    st = bundle.get("stream")
    if not st:
        return None
    env = _env(bundle, a)
    def fields(key_list, key_one, key_w, dflt):
        if st.get(key_list):
            return [{"name": f.get("name", dflt), "width": int(expr_value(f.get("width"), env, 16)), "signed": bool(f.get("signed", True))} for f in st[key_list]]
        # the one-field shorthand may say "signed": false (an unsigned block); samples default to signed
        return [{"name": st.get(key_one, dflt), "width": int(expr_value(st.get(key_w), env, 16)), "signed": bool(st.get("signed", True))}]
    ins = fields("inputs", "in", "in_width", "in_data")
    outs = fields("outputs", "out", "out_width", "out_data")
    return {"in": ins[0]["name"], "out": outs[0]["name"], "in_width": ins[0]["width"], "out_width": outs[0]["width"],
            "inputs": ins, "outputs": outs, "in_bits": sum(f["width"] for f in ins), "out_bits": sum(f["width"] for f in outs),
            "rate": int(expr_value(st.get("rate"), env, 1)), "valid_in": st.get("valid_in", "in_valid"), "ready": st.get("ready", "in_ready"),
            "valid_out": st.get("valid_out", "out_valid")}


def _rand_field(rng, f: dict, extreme: int | None = None) -> int:
    w = f["width"]
    if f["signed"]:
        lo, hi = -(1 << (w - 1)), (1 << (w - 1)) - 1
    else:
        lo, hi = 0, (1 << w) - 1
    if extreme is not None:
        return hi if extreme > 0 else lo
    return rng.randint(lo, hi)


def stimulus(st: dict | int, seed: int = 1, n: int = 200) -> tuple[list, list[int]]:
    """Input beats and the idle gaps before each (the vectors.fir_vectors shape: extremes first, then random, gaps
    in the first half only so the back-to-back half catches full-rate bugs). One field: a list of ints; several
    fields: a list of dicts {name: value} (what the models receive)."""
    import random
    rng = random.Random(seed)
    if isinstance(st, int):
        st = {"inputs": [{"name": "x", "width": st, "signed": True}]}
    ins = st["inputs"]
    one = len(ins) == 1
    def beat(extreme=None):
        vals = {f["name"]: _rand_field(rng, f, extreme if f["width"] > 1 else None) for f in ins}
        return vals[ins[0]["name"]] if one else vals
    xs = [beat(+1) for _ in range(8)] + [beat(-1) for _ in range(8)] + [beat() for _ in range(n)]
    gaps = [0 if i >= len(xs) - n // 2 else rng.choice([0, 0, 1, 3, 8]) for i in range(len(xs))]
    return xs, gaps


def run_model(code: str, params: dict, xs: list, cwd: Path, timeout: int = 60, name: str = "reference") -> list:
    """Run a model (`def reference(params, xs)` or `def golden(params, xs)`) in a separate, isolated interpreter
    (`python -I`: no site, no env), with a time limit. The code was written by a model; it never runs in the server.
    Outputs are ints (one field) or dicts {name: int} (several fields)."""
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
        "json.dump([({k: int(v) for k, v in o.items()} if isinstance(o, dict) else int(o)) for o in out], open('out.json', 'w'))\n"
    )
    p = subprocess.run([sys.executable, "-I", "-c", driver], cwd=src, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{name} model failed: {(p.stderr or p.stdout)[-800:]}")
    return json.loads((src / "out.json").read_text())


def _pack(vals, fields: list[dict]) -> int:
    """Fields packed MSB-first in declaration order into one word (the first field is the most significant)."""
    word = 0
    for f in fields:
        v = vals[f["name"]] if isinstance(vals, dict) else vals
        word = (word << f["width"]) | (int(v) & ((1 << f["width"]) - 1))
    return word


def _norm_out(o, fields: list[dict]):
    """A model output normalised to the output fields' widths (masked), for comparison."""
    if isinstance(o, dict):
        return {f["name"]: int(o.get(f["name"], 0)) & ((1 << f["width"]) - 1) for f in fields}
    return int(o) & ((1 << fields[0]["width"]) - 1)


def compare_models(golden: str, reference: str, params: dict, st: dict | int, cwd: Path, seeds: tuple[int, ...] = (1, 2, 3)) -> dict:
    """The designer's golden against the independent reference, Python against Python, before any EDA time is
    spent: {"agree", "samples", "mismatch": {"seed", "index", "golden", "reference"} | None, "error"}."""
    if isinstance(st, int):
        st = {"inputs": [{"name": "x", "width": st, "signed": True}], "outputs": [{"name": "y", "width": 64, "signed": True}]}
    total = 0
    for seed in seeds:
        xs, _ = stimulus(st, seed)
        try:
            g = run_model(golden, params, xs, cwd, name="golden")
            r = run_model(reference, params, xs, cwd, name="reference")
        except (RuntimeError, ValueError) as e:
            return {"agree": False, "samples": total, "mismatch": None, "error": str(e)[-800:]}
        n = min(len(g), len(r))
        for i in range(n):
            if _norm_out(g[i], st["outputs"]) != _norm_out(r[i], st["outputs"]):
                return {"agree": False, "samples": total + i, "mismatch": {"seed": seed, "index": i, "golden": g[i], "reference": r[i]}, "error": None}
        if len(g) != len(r):
            return {"agree": False, "samples": total + n, "mismatch": {"seed": seed, "index": n, "golden": len(g), "reference": len(r), "note": "output counts differ"}, "error": None}
        total += n
    return {"agree": True, "samples": total, "mismatch": None, "error": None}


def vectors(bundle: dict, a: RtlArch, seed: int = 1, cwd: Path | None = None) -> tuple[list[str], list[str]]:
    """stim/exp lines for the generic vector bench of a stream bundle; the expected outputs come from the bundle's
    reference (an independent model, or the generator the design was forked from), never from the RTL's author.
    A stim line is `<gap> <packed inputs>`, an exp line the packed outputs (fields MSB-first in declaration order)."""
    ref = bundle.get("reference") or {}
    if ref.get("kind") == "generator":
        from .designs import DESIGNS
        o = ref["origin"]
        d = DESIGNS[o["design"]]
        return d.vectors(d.ir_cls.from_dict(o["ir"]), seed=seed)
    st = stream_of(bundle, a)
    if not st:
        raise ValueError("not a stream bundle")
    if ref.get("kind") != "independent" or not ref.get("code"):
        raise ValueError("the bundle has no reference model")
    xs, gaps = stimulus(st, seed)
    work = cwd or Path(bundle.get("dir") or ".") / "ref-cache"
    ys = run_model(ref["code"], _env(bundle, a), xs, work)
    iw, ow = max(1, (st["in_bits"] + 3) // 4), max(1, (st["out_bits"] + 3) // 4)
    stim = [f"{g:x} {_pack(x, st['inputs']):0{iw}x}" for g, x in zip(gaps, xs)]
    exp = [f"{_pack(y, st['outputs']):0{ow}x}" for y in ys]
    return stim, exp


def stream_tb(bundle: dict, a: RtlArch, period: float) -> str:
    """The generic replay bench (vectors.TB_FIR) aimed at this bundle's top and stream ports: stimulus on negedge,
    outputs sampled 3/4 period after posedge, so it also runs on SDF-annotated netlists. Several fields are packed
    into one stim/exp word and unpacked onto the ports here."""
    from .vectors import TB_FIR
    st = stream_of(bundle, a)
    drain = 300 + 4 * max(latency_cycles(bundle, a), 1) * max(st["rate"], 1)
    ins, outs = st["inputs"], st["outputs"]
    # port connections: in_data / out_data are the packed words; each field is a slice of them
    conn_in = []
    hi = st["in_bits"] - 1
    for f in ins:
        conn_in.append(f".{f['name']}(in_data[{hi}:{hi - f['width'] + 1}])" if st["in_bits"] > 1 or len(ins) > 1 else f".{f['name']}(in_data)")
        hi -= f["width"]
    conn_out = []
    hi = st["out_bits"] - 1
    for f in outs:
        conn_out.append(f".{f['name']}(out_data[{hi}:{hi - f['width'] + 1}])" if st["out_bits"] > 1 or len(outs) > 1 else f".{f['name']}(out_data)")
        hi -= f["width"]
    ready = f".{st['ready']}(in_ready)" if st.get("ready") else ""
    inst = f"{bundle['top']} dut(.clk(clk), .rst(rst), .{st['valid_in']}(in_valid), {', '.join(conn_in)}, {ready + ', ' if ready else ''}.{st['valid_out']}(out_valid), {', '.join(conn_out)});"
    tb = (TB_FIR.replace("fir_top dut(.clk, .rst, .in_valid, .in_data, .in_ready, .out_valid, .out_data{COEF_TIE});", inst)
          .replace("repeat (300) @(posedge clk);", f"repeat ({drain}) @(posedge clk);"))
    if not st.get("ready"):
        # a design without back-pressure: the bench's in_ready is a constant 1
        tb = tb.replace("wire in_ready, out_valid;", "wire out_valid; wire in_ready = 1'b1;")
    return tb.format(DW=st["in_bits"], AW=st["out_bits"], period=period, COEF_TIE="")


def bench_of(bundle: dict, a: RtlArch, period: float) -> str:
    """The testbench of a bundle: the generator's own bench for a fork (its vectors carry its own fields and
    protocol), the generic replay bench for an independently referenced stream, the user's bench otherwise."""
    ref = bundle.get("reference") or {}
    if ref.get("kind") == "generator":
        from .designs import DESIGNS
        o = ref["origin"]
        d = DESIGNS[o["design"]]
        return d.tb(d.ir_cls.from_dict(o["ir"]), period)
    if bundle.get("stream"):
        return stream_tb(bundle, a, period)
    t = bundle.get("tb") or ""
    return re.sub(r"parameter\s+real\s+PERIOD\s*=\s*[\d.]+", f"parameter real PERIOD = {period}", t)


def design_of(bundle: dict, cwd: Path | None = None):
    """The registry Design for a bundle (uploaded or authored)."""
    from .designs import Design, DESIGNS
    A = arch_class(bundle)
    ref = bundle.get("reference") or {}
    extra = ("mult18x18d_sim.v",)
    if ref.get("kind") == "generator" and ref["origin"]["design"] in DESIGNS:
        extra = tuple(sorted(set(extra) | set(DESIGNS[ref["origin"]["design"]].extra_sim_sources)))
    has_vectors = bool(bundle.get("stream")) or ref.get("kind") == "generator"
    return Design(kind=f"rtl:{bundle['id']}", ir_cls=A, top=bundle["top"], emit=lambda a, backend="ecp5": emit(bundle, a, backend), test_module=None,
                  sim_env=lambda a: {}, throughput=lambda a: throughput(bundle, a), throughput_unit=bundle.get("throughput_unit", "items/cycle"),
                  latency=lambda a: latency_cycles(bundle, a), extra_sim_sources=extra,
                  vectors=(lambda a, seed=1: vectors(bundle, a, seed=seed, cwd=cwd)) if has_vectors else None,
                  tb=lambda a, period: bench_of(bundle, a, period))


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
