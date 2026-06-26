"""schedule.py — the prefix queue and the two steering strategies.

A Steering decides two things for a lineage: how a child's state evolves from its
parent plus the new window bytes (and whether that step made *progress* — which
refills the survival budget), and the heap key that ranks queued prefixes.

  CoverageSteering (run9): pursue afl-showmap edge novelty. The signal is the
    candidate's new-edge count; progress means it reached new code.
  KwdepthSteering (run14+): pursue keyword-prefix match depth — the trailing run
    of consecutive matched letters, retained max-ever — so the narrow
    ``w->wh->whi->whil->while`` ladder is climbed over diverse expressions
    (depth ~1). Secondary key: distinct matched values (kills repeated-token
    basins like ``(((``), or coverage novelty for non-keyword lineages when the
    unified secondary is on.

The Queue is one global best-first heap, or (root_fair) round-robin over
first-byte buckets so a large lineage family (e.g. ``(``-rooted expressions)
cannot starve a rare one (the ``w``-rooted keyword lineage).
"""

from __future__ import annotations

import heapq
from abc import ABC, abstractmethod

from .config import Config, Steer
from .models import Candidate, Lineage, LineageState


class Steering(ABC):
    @abstractmethod
    def child(self, parent: LineageState, cand: Candidate,
              novelty: int) -> tuple[LineageState, bool]:
        """(child_state, progress) for extending ``parent`` by ``cand``.
        ``novelty`` is the candidate's coverage novelty if the pipeline measured
        it for this strategy, else 0."""

    @abstractmethod
    def key(self, prefix_len: int, state: LineageState) -> tuple:
        """Heap sort key; lexicographically smaller is popped first."""


class CoverageSteering(Steering):
    """run9: new edges first, then shortest (pFuzzer's novelty + minimality)."""

    def child(self, parent, cand, novelty):
        state = LineageState(novelty=novelty, progress=novelty > 0)
        return state, state.progress

    def key(self, prefix_len, state):
        return (-state.novelty, prefix_len)


class KwdepthSteering(Steering):
    """run14+: keyword-prefix depth first; distinct matched (or, unified, coverage
    novelty for non-keyword lineages) second; then shortest."""

    def __init__(self, secondary_coverage: bool):
        self.secondary_coverage = secondary_coverage

    def child(self, parent, cand, novelty):
        matched, run, kwdepth = parent.matched, parent.run, parent.kwdepth
        progress = False
        for wb in cand.info:
            if wb.pinned and wb.value not in matched:
                progress = True          # a NEW matched value refills the budget
            if wb.pinned:
                matched = matched | {wb.value}
            is_letter = 97 <= wb.value <= 122
            run = run + 1 if (wb.pinned and is_letter) else 0
            kwdepth = max(kwdepth, run)
        state = LineageState(matched=matched, kwdepth=kwdepth, run=run,
                             novelty=novelty, progress=progress)
        return state, progress

    def key(self, prefix_len, state):
        if self.secondary_coverage and state.kwdepth == 0:
            secondary = -state.novelty
        else:
            secondary = -len(state.matched)
        return (-state.kwdepth, secondary, prefix_len)


def make_steering(cfg: Config) -> Steering:
    if cfg.steer is Steer.COVERAGE:
        return CoverageSteering()
    return KwdepthSteering(secondary_coverage=cfg.secondary_coverage)


class Queue:
    """Priority queue of Lineages, keyed by a Steering. Global best-first, or
    root-fair round-robin over first-byte buckets."""

    def __init__(self, steering: Steering, root_fair: bool):
        self.steering = steering
        self.root_fair = root_fair
        self._heap: list = []                  # global mode
        self._buckets: dict[bytes, list] = {}  # root_fair mode
        self._roots: list[bytes] = []
        self._rr = 0
        self._seq = 0
        self._n = 0

    def __len__(self) -> int:
        return self._n

    def push(self, lineage: Lineage) -> None:
        # (key, seq, lineage): key orders; the unique seq breaks ties so the
        # unorderable Lineage is never compared.
        entry = (self.steering.key(len(lineage.prefix), lineage.state),
                 self._seq, lineage)
        self._seq += 1
        if self.root_fair:
            bucket = self._buckets.get(lineage.prefix[:1])
            if bucket is None:
                bucket = self._buckets[lineage.prefix[:1]] = []
                self._roots.append(lineage.prefix[:1])
            heapq.heappush(bucket, entry)
        else:
            heapq.heappush(self._heap, entry)
        self._n += 1

    def pop(self) -> Lineage | None:
        if self.root_fair:
            for _ in range(len(self._roots)):
                bucket = self._buckets[self._roots[self._rr % len(self._roots)]]
                self._rr += 1
                if bucket:
                    self._n -= 1
                    return heapq.heappop(bucket)[2]
            return None
        if not self._heap:
            return None
        self._n -= 1
        return heapq.heappop(self._heap)[2]
