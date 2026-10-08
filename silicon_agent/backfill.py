"""Backfill design memory from completed runs under results/ (one-off; idempotent per run)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import memory
from .designs import design_for
from .eda import ROOT
from .ir import Constraints
from .loop import _learn


def main(argv: list[str]) -> int:
    results = ROOT / "results"
    done = {l["run_id"] for d in memory.MEM_DIR.glob("*.jsonl") for l in memory.load(*d.stem.split("_", 1))}
    n = 0
    for d in sorted(results.iterdir()):
        s = d / "summary.json"
        if not s.exists() or d.name in done:
            continue
        summ = json.loads(s.read_text())
        spec, hist = summ["spec"], summ.get("history", [])
        if len(hist) < 2:
            continue
        design, backend = design_for(spec), spec.get("backend", "ecp5")
        c = Constraints.from_dict(spec["constraints"])
        print(f"== {d.name}  {design.kind}/{backend}  {len(hist)} iterations")
        for prev, cur in zip(hist, hist[1:]):
            _learn(design, backend, c, d.name, prev, cur, [], lambda ev: None)
            n += 1
    print(f"distilled {n} moves")
    for f in sorted(memory.MEM_DIR.glob("*.jsonl")):
        ls = memory.load(*f.stem.split("_", 1))
        print(f"{f.name}: {len(ls)} lessons")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
