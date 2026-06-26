"""Core data types shared across the frontier pipeline.

These are deliberately small, immutable value objects. The behaviour lives in the
stage modules (``klee``, ``generate``, ``oracle``, ``schedule``, ``pipeline``);
this module is just the vocabulary they share.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Verdict(IntEnum):
    """How the survival oracle judged a candidate, from the native-replay exit
    code. The three live values are the contract every survival harness honours
    (``frontier/harnesses/*_survival*.c``); ``ERROR`` is a replay failure."""

    ACCEPT = 0   # parser accepted the whole input
    ALIVE = 10   # viable prefix: errored only after reading past end-of-input
    DEAD = 11    # committed error on a byte that was present
    ERROR = -1   # replay timed out or could not run

    @property
    def accepted(self) -> bool:
        return self is Verdict.ACCEPT


@dataclass(frozen=True)
class WinByte:
    """One window byte of a candidate's suffix: its value, and whether KLEE's
    own constraints positively *pinned* it. A pinned byte is one the program
    compared and matched (a keyword char via strcmp, a token char via the lexer
    switch); an unpinned byte only satisfied a range bound (the identifier /
    number filler slot). Always unpinned for take-all generation, which never
    looks at the constraints."""

    value: int
    pinned: bool


@dataclass(frozen=True)
class Candidate:
    """A full input produced by extending a concrete prefix with one KLEE
    window. ``data`` is what the oracle replays; ``info`` carries the per-byte
    pin status of the appended ``suffix`` (empty for take-all)."""

    data: bytes
    suffix: bytes
    info: tuple[WinByte, ...] = ()


@dataclass(frozen=True)
class LineageState:
    """The steering state carried from a prefix to its extensions.

    A lineage is one root-to-prefix path through the search. ``matched`` and
    ``kwdepth`` accumulate the program's comparison signal along it; ``novelty``
    is the coverage it last earned (the secondary key in unified steering);
    ``progress`` records whether the most recent extension improved the steering
    signal (it refills the survival budget)."""

    matched: frozenset = frozenset()
    kwdepth: int = 0           # max keyword-prefix depth reached (max-ever)
    run: int = 0              # current trailing run of matched letters
    novelty: int = 0
    progress: bool = False


@dataclass(frozen=True)
class Lineage:
    """A queued concrete prefix, its steering state, and its survival budget
    (consecutive no-progress extensions it may still take before being pruned)."""

    prefix: bytes
    state: LineageState
    budget: int


@dataclass
class Stats:
    """Running totals, written to the run summary and the final stdout line."""

    iters: int = 0
    candidates: int = 0
    valid: int = 0
    kw_valid: int = 0
    max_matched: int = 0
    max_kwdepth: int = 0
    edges: int = 0
    best_prefix: bytes = b""
