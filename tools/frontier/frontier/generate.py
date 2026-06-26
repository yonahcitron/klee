"""generate.py — turn KLEE's terminated paths into deduped candidates.

take-all (run9): every path's ``prefix + win[:len]``, deduped by suffix — the
prolific validity generator. eq-pinned (run13+): the same, but each suffix byte
is tagged with whether KLEE positively pinned it (a matched keyword/token byte)
vs left it range-bounded (filler); two paths reaching the same suffix merge their
pins (pinned if either pinned it).
"""

from __future__ import annotations

from .config import Config, Gen
from .klee import PathResult
from .models import Candidate, WinByte


def candidates(cfg: Config, prefix: bytes, results: list[PathResult]) -> list[Candidate]:
    merged: dict[bytes, tuple[WinByte, ...]] = {}
    for r in results:
        suffix = r.win[:r.length]
        if cfg.gen is Gen.TAKE_ALL:
            merged.setdefault(suffix, ())
            continue
        info = tuple(WinByte(suffix[i], i in r.pins) for i in range(len(suffix)))
        prev = merged.get(suffix)
        merged[suffix] = info if prev is None else tuple(
            WinByte(b.value, b.pinned or q.pinned) for b, q in zip(prev, info)
        )
    return [
        Candidate(data=prefix + suffix, suffix=suffix, info=info)
        for suffix, info in sorted(merged.items())
    ]
