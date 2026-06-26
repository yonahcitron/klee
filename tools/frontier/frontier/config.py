"""Configuration: the knobs that define a *version*, and the named profiles.

The whole tool is one pipeline; a "version" (run9, run14, run18, …) is just a
point in this knob space. ``PROFILES`` pins each published run to its knobs, so a
reproduction is ``--profile run9`` and an ablation is ``--profile run9 --window
2``. The resolved ``Config`` is written verbatim into every run's ``run.yaml`` —
the config *is* the provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum


class Gen(str, Enum):
    """How a KLEE iteration's terminated paths become candidates."""

    TAKE_ALL = "take-all"    # every path's prefix+win[:len], deduped (run9)
    EQ_PINNED = "eq-pinned"  # + per-byte Eq-pin info from the .kquery (run13+)


class Steer(str, Enum):
    """What the queue priority pursues."""

    COVERAGE = "coverage"    # afl-showmap edge novelty — pFuzzer/run9 breadth
    KWDEPTH = "kwdepth"      # keyword-prefix match depth, distinct 2ndary (run14+)


class Survival(str, Enum):
    """What the oracle can tell apart."""

    BASIC = "basic"          # exit 0 = accept, nonzero = alive (driver.py)
    ACCURATE = "accurate"    # exit 0/10/11 = accept/alive/dead (survival harness)


class Requeue(str, Enum):
    """What happens to an accepted valid input."""

    NONE = "none"            # never re-extend it (single-statement grammars, run14)
    PLAIN = "plain"          # re-extend like an alive prefix (run9)
    GATED = "gated"          # re-extend only if it earns new coverage (run18)


@dataclass(frozen=True)
class Config:
    """A fully-resolved run configuration. Knobs first, then runtime budgets,
    then the resolved artifact paths the CLI fills from the subject registry."""

    subject: str
    # --- the knobs that define the version ---
    window: int = 4
    gen: Gen = Gen.TAKE_ALL
    steer: Steer = Steer.COVERAGE
    survival: Survival = Survival.BASIC
    requeue: Requeue = Requeue.PLAIN
    survival_budget: int = 0
    requeue_budget: int = 3
    root_fair: bool = False
    secondary_coverage: bool = False  # unified: coverage as kwdepth's 2ndary key
    keywords: tuple[str, ...] = ()    # report-only; flags keyword-bearing valids
    # --- runtime budgets ---
    max_seconds: int = 3600
    iter_time: int = 30
    max_len: int = 512
    klee_memory: int = 2000
    keep_iters: bool = False
    # --- resolved paths / tools ---
    subject_bc: str = ""
    survival_bin: str = ""
    showmap: str = ""
    klee: str = "klee"
    workdir: str = ""

    @property
    def needs_kqueries(self) -> bool:
        """Eq-pinned generation reads KLEE's --write-kqueries dumps."""
        return self.gen is Gen.EQ_PINNED

    @property
    def needs_coverage(self) -> bool:
        """Whether an afl-showmap harness is required: as the primary steering
        signal, as the accept gate, or as the unified secondary key."""
        return (
            self.steer is Steer.COVERAGE
            or self.requeue is Requeue.GATED
            or self.secondary_coverage
        )


# The published runs, as knob sets. Anything omitted takes the Config default.
PROFILES: dict[str, dict] = {
    # criterion 1 — length-exact validity (the headline). Take-all generation,
    # coverage-novelty steering, basic exit oracle, window 4.
    "run9": dict(
        window=4, gen=Gen.TAKE_ALL, steer=Steer.COVERAGE, survival=Survival.BASIC,
        requeue=Requeue.PLAIN, survival_budget=0, root_fair=False,
    ),
    # criterion 2 — keyword climbing on the char-comparison class. Eq-pinned
    # generation, keyword-depth steering, accurate survival, window 1, no requeue.
    "run14": dict(
        window=1, gen=Gen.EQ_PINNED, steer=Steer.KWDEPTH, survival=Survival.ACCURATE,
        requeue=Requeue.NONE, survival_budget=8, root_fair=True, max_len=64,
    ),
    # the full modern sweep — run14 mechanism + coverage-gated requeue, window 2.
    "run18": dict(
        window=2, gen=Gen.EQ_PINNED, steer=Steer.KWDEPTH, survival=Survival.ACCURATE,
        requeue=Requeue.GATED, survival_budget=8, requeue_budget=3, root_fair=True,
        max_len=64,
    ),
    # the unified driver (run15, previously designed-not-built): run9's economics
    # under run14's keyword steering — kwdepth primary, coverage as the secondary
    # key for non-keyword lineages. Window 4, accepts re-extended (multi-statement).
    "run15": dict(
        window=4, gen=Gen.EQ_PINNED, steer=Steer.KWDEPTH, survival=Survival.ACCURATE,
        requeue=Requeue.PLAIN, survival_budget=8, root_fair=True,
        secondary_coverage=True,
    ),
}

_FIELDS = {f.name for f in fields(Config)}


def resolve(profile: str | None, **overrides) -> Config:
    """Build a Config from an optional profile plus explicit overrides.

    Overrides win over the profile; ``None`` overrides are ignored (so a CLI flag
    that was not passed leaves the profile / default value untouched)."""
    if profile is not None and profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choices: {sorted(PROFILES)}")
    merged: dict = dict(PROFILES[profile]) if profile else {}
    for key, value in overrides.items():
        if value is None:
            continue
        if key not in _FIELDS:
            raise ValueError(f"unknown config field {key!r}")
        merged[key] = value
    return Config(**merged)


def validate(cfg: Config) -> None:
    """Reject incoherent knob combinations early, with an actionable message."""
    if cfg.window < 1:
        raise ValueError("--window must be >= 1")
    if cfg.gen is Gen.TAKE_ALL and cfg.steer is Steer.KWDEPTH:
        raise ValueError(
            "kwdepth steering needs the per-byte Eq pins from eq-pinned "
            "generation; take-all generation produces none (use --steer coverage "
            "or --gen eq-pinned)"
        )
    if cfg.needs_coverage and not cfg.showmap:
        raise ValueError(
            "an afl-showmap path is required for coverage steering, gated "
            "requeue, or the unified secondary key (pass --showmap, and build an "
            "afl-instrumented survival harness)"
        )
    if cfg.requeue is Requeue.GATED and cfg.secondary_coverage:
        raise ValueError("gated requeue and unified secondary coverage are exclusive")
    if not cfg.subject_bc or not cfg.survival_bin:
        raise ValueError("subject_bc and survival_bin must be resolved before running")
