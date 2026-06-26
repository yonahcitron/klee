"""report.py — provenance (run.yaml), the structured event log (log.jsonl), and
the final metrics + human summary.

The resolved Config written to ``run.yaml`` is the run's provenance: the knobs
that defined this version are all there, so a result is reproducible from its own
record (``frontier --profile … `` or the explicit flag set).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, fields
from enum import Enum
from pathlib import Path

from .config import Config
from .models import Stats


def _scalar(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return ",".join(map(str, value))
    if isinstance(value, bytes):
        return value.decode("latin1", "replace")
    return value


class Reporter:
    def __init__(self, cfg: Config, workdir: Path):
        self.cfg = cfg
        self.work = workdir
        self._log = open(self.work / "log.jsonl", "a", buffering=1)

    def start(self, t0: float) -> None:
        lines = ["# frontier run provenance", f"started_unix: {int(t0)}"]
        lines += [f"{f.name}: {_scalar(getattr(self.cfg, f.name))}"
                  for f in fields(self.cfg)]
        (self.work / "run.yaml").write_text("\n".join(lines) + "\n")

    def event(self, t0: float, **kw) -> None:
        kw["t"] = round(time.time() - t0, 1)
        self._log.write(json.dumps(kw) + "\n")

    def finish(self, t0: float, stats: Stats) -> None:
        metrics = {k: _scalar(v) for k, v in asdict(stats).items()}
        metrics["elapsed_s"] = round(time.time() - t0, 1)
        self.event(t0, event="done", **metrics)
        (self.work / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
        self._log.close()
        print(
            f"frontier done: {stats.iters} iters, {stats.candidates} candidates, "
            f"{stats.valid} valid ({stats.kw_valid} keyword-bearing), "
            f"max keyword-depth {stats.max_kwdepth} (distinct {stats.max_matched}), "
            f"deepest {stats.best_prefix.decode('latin1', 'replace')!r}"
        )
