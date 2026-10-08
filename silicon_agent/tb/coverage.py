"""Tiny functional-coverage helper for the cocotb benches: named bins that the test marks as it drives the DUT,
written as one JSON line (`COVERAGE {...}`) so eda.simulate() can lift it into Metrics.sim_coverage.

    cov = Coverage("fir")
    cov.hit("gap>0")            # stimulus had idle cycles
    cov.hit("back-to-back")     # ...
    cov.report()                # prints COVERAGE {"fir": {"gap>0": 12, ...}}

Bins are counts; a bin never hit stays 0 only if it was declared with `declare()`.
"""

import json


class Coverage:
    def __init__(self, name: str, bins: list[str] | None = None):
        self.name = name
        self.bins: dict[str, int] = {b: 0 for b in (bins or [])}

    def declare(self, *names: str) -> None:
        for n in names:
            self.bins.setdefault(n, 0)

    def hit(self, name: str, n: int = 1) -> None:
        self.bins[name] = self.bins.get(name, 0) + n

    def report(self) -> None:
        print("COVERAGE " + json.dumps({self.name: self.bins}), flush=True)
