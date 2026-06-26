"""oracle.py — observe a candidate by native replay.

Two observations, both run the native harness over the candidate's bytes on
stdin:

* ``SurvivalOracle`` classifies it (accept / alive / dead) — the survival
  signal that tells directed search "keep extending this prefix" vs "abandon it".
* ``Coverage`` measures afl-showmap edge novelty — the breadth signal (run9's
  coverage steering, the unified secondary key, and the gated-requeue gate).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Config, Survival
from .models import Verdict

_REPLAY_TIMEOUT = 5
_SHOWMAP_TIMEOUT = 10


class SurvivalOracle:
    """Native replay of a candidate through the survival harness.

    accurate: the harness exits 0/10/11 = accept/alive/dead (the run13+ oracle).
    basic:    exit 0 = accept, any nonzero = alive (driver.py had no committed-
              error signal; extension is then governed by steering progress and
              the survival budget rather than by a dead verdict)."""

    def __init__(self, cfg: Config):
        self.bin = cfg.survival_bin
        self.accurate = cfg.survival is Survival.ACCURATE

    def classify(self, data: bytes) -> Verdict:
        try:
            rc = subprocess.run(
                [self.bin], input=data, capture_output=True,
                timeout=_REPLAY_TIMEOUT,
            ).returncode
        except (subprocess.TimeoutExpired, OSError):
            return Verdict.ERROR
        if rc == 0:
            return Verdict.ACCEPT
        if not self.accurate:
            return Verdict.ALIVE
        if rc == Verdict.ALIVE.value:
            return Verdict.ALIVE
        if rc == Verdict.DEAD.value:
            return Verdict.DEAD
        return Verdict.ERROR


class Coverage:
    """afl-showmap edge novelty against a global edge set. Returns the count of
    new edges a candidate covers and absorbs them, so a value > 0 means "reached
    new code". Needs an afl-instrumented survival harness."""

    def __init__(self, cfg: Config):
        self.showmap = cfg.showmap
        self.bin = cfg.survival_bin
        self.mapfile = Path(cfg.workdir) / "showmap.tmp"
        self.edges: set[str] = set()

    def novelty(self, data: bytes) -> int:
        if not self.showmap:
            return 0
        try:
            subprocess.run(
                [self.showmap, "-m", "none", "-t", "1000", "-q",
                 "-o", str(self.mapfile), "--", self.bin],
                input=data, capture_output=True, timeout=_SHOWMAP_TIMEOUT,
                env={"AFL_QUIET": "1", "PATH": "/usr/bin:/bin"},
            )
        except (subprocess.TimeoutExpired, OSError):
            return 0
        try:
            ids = {ln.split(":", 1)[0]
                   for ln in self.mapfile.read_text().splitlines() if ":" in ln}
        except OSError:
            return 0
        new = ids - self.edges
        self.edges |= new
        return len(new)
