"""EDA environment: simulate, synthesize, place & route, and parse reports.

Backends are plugins under silicon_agent/backends/ (ecp5, xc7s50, ice40, gowin, ...); this module owns the
behavioral simulation (cocotb + iverilog, the same for every device) and the cached `evaluate()` entry point.
Results are cached by (CACHE_VERSION, backend, RTL hash) so a repeated design is free.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

from .backends import BACKENDS as _REG, Backend
from .backends._common import CACHE, ROOT, TB_DIR, TOOLS, tool_env, run  # noqa: F401  (re-exported for older callers)
from .designs import DESIGNS, Design
from .ir import Metrics

CACHE_VERSION = "13"  # bump when report parsing, the vector benches or the sim libraries change (10: time-0 eval of constant cells after flop init)


class _BackendView(dict):
    """`BACKENDS[key]` keeps answering the old dict questions (name, lut, dsp, ...) while the plugin
    object itself is reachable through `.plugin`."""


BACKENDS: dict[str, dict] = {}


def _refresh() -> None:
    for k, b in _REG.items():
        v = _BackendView(b.as_facts())
        v.plugin = b  # type: ignore[attr-defined]
        BACKENDS[k] = v


_refresh()


def backend(key: str) -> Backend:
    return _REG[key]


# ---------------------------------------------------------------- behavioral simulation
def simulate(d: Design, arch, work: Path, seed: int = 1) -> tuple[bool, str, dict]:
    rtl = work / "rtl" / f"{d.top}.sv"
    sim_dir = work / "sim"
    sim_dir.mkdir(parents=True, exist_ok=True)
    env = tool_env()
    env.update({"SA_ARCH": json.dumps(arch.to_dict()), "SA_SEED": str(seed), "PYTHONPATH": str(TB_DIR), **d.sim_env(arch)})
    sources = [rtl] + [TB_DIR / f for f in d.extra_sim_sources]
    driver = f"""
from pathlib import Path
from cocotb_tools.runner import get_runner
from cocotb_tools.check_results import get_results
r = get_runner("icarus")
r.build(sources=[Path(p) for p in {[str(x) for x in sources]!r}], hdl_toplevel={d.top!r}, build_dir={str(sim_dir / 'build')!r},
        build_args=["-g2012"], timescale=("1ns", "1ps"), always=True)
xml = r.test(test_module={d.test_module!r}, hdl_toplevel={d.top!r}, build_dir={str(sim_dir / 'build')!r},
             test_dir={str(sim_dir)!r}, timescale=("1ns", "1ps"))
total, failed = get_results(xml)
print(f"COCOTB_RESULT total={{total}} failed={{failed}}")
raise SystemExit(1 if failed else 0)
"""
    log = sim_dir / "sim.log"
    with log.open("w") as f:
        p = subprocess.run([str(TOOLS / "py3bin" / "python3"), "-c", driver], cwd=sim_dir, env=env,
                           stdout=f, stderr=subprocess.STDOUT, text=True, timeout=1800)
    text = log.read_text()
    passed = p.returncode == 0 and "failed=0" in text
    tail = "\n".join(line for line in text.splitlines() if re.search(r"(FAIL|ERROR|Error|error:|assert|COCOTB_RESULT|PASS)", line))[-4000:]
    cov = {}
    for line in text.splitlines():
        if "COVERAGE " in line:
            try:
                cov.update(json.loads(line.split("COVERAGE ", 1)[1]))
            except json.JSONDecodeError:
                pass
    return passed, tail, cov


# ---------------------------------------------------------------- top-level evaluate
def evaluate(d: Design, arch, fmax_target: float, runs_dir: Path, backend: str = "ecp5", on_stage=None) -> Metrics:
    b = _REG[backend]
    rtl_text = d.emit(arch, backend=backend)
    h = hashlib.sha256(f"{backend}\n{rtl_text}".encode()).hexdigest()[:16]
    cache_key = hashlib.sha256(f"{CACHE_VERSION}\n{backend}\n{rtl_text}".encode()).hexdigest()[:16]
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / f"{cache_key}.json"
    if cached.exists():
        return Metrics(**json.loads(cached.read_text()))

    work = runs_dir / h
    (work / "rtl").mkdir(parents=True, exist_ok=True)
    (work / "rtl" / f"{d.top}.sv").write_text(rtl_text)
    (work / "arch.json").write_text(json.dumps({"design": d.kind, "backend": backend, **arch.to_dict()}, indent=2))

    sim_ok, sim_log, cov = simulate(d, arch, work)
    if not sim_ok:
        m = Metrics(fmax_mhz=0, lut=0, ff=0, dsp=0, bram=0, critical_path=[], sim_passed=False, sim_log=sim_log, sim_coverage=cov)
    else:
        r = b.synth_pnr(work, fmax_target, d.top)
        stages = {}
        if r.get("fits") and d.vectors is not None and b.netlist_sims is not None:
            stages = b.netlist_sims(d, arch, work, fmax_target, on_stage=on_stage)
        m = Metrics(**r, sim_passed=True, sim_log=sim_log, sim_stages=stages, sim_coverage=cov)
    cached.write_text(json.dumps(asdict(m)))
    (work / "metrics.json").write_text(json.dumps(asdict(m), indent=2))
    return m
