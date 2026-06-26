"""pipeline.py — the frontier loop: schedule -> explore -> generate -> classify
-> bank/requeue. One loop; the Config selects the strategy at each stage, so
every version (run9/run14/run18/run15) is this same code on different knobs.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace
from pathlib import Path

from .config import Config, Requeue, Steer
from .generate import candidates as generate_candidates
from .klee import KleeRunner
from .models import Candidate, Lineage, LineageState, Stats, Verdict
from .oracle import Coverage, SurvivalOracle
from .report import Reporter
from .schedule import Queue, make_steering

_GIVE_UP_AFTER = 30   # consecutive empty-queue reseeds before declaring it dry


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.work = Path(cfg.workdir)
        self.valid_dir = self.work / "valid"
        self.klee_dir = self.work / "klee-iters"
        for d in (self.valid_dir, self.klee_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.klee = KleeRunner(cfg, self.klee_dir)
        self.oracle = SurvivalOracle(cfg)
        self.coverage = Coverage(cfg)
        self.queue = Queue(make_steering(cfg), cfg.root_fair)
        self.reporter = Reporter(cfg, self.work)

        self.stats = Stats()
        self.seen: set[bytes] = set()
        self._keywords = [k.encode() for k in cfg.keywords]
        self.queue.push(Lineage(b"", LineageState(), cfg.survival_budget))

    # ---- main loop ------------------------------------------------------
    def run(self) -> Stats:
        t0 = time.time()
        self.reporter.start(t0)
        deadline = t0 + self.cfg.max_seconds
        idle = 0

        while time.time() < deadline:
            if len(self.queue) == 0:
                idle = 0 if self._reseed() else idle + 1
                if idle > _GIVE_UP_AFTER:
                    self.reporter.event(t0, event="giving-up", reason="queue dry")
                    break

            remaining = int(deadline - time.time())
            if remaining < 5:
                break
            iter_time = min(self.cfg.iter_time, max(remaining - 2, 1))

            lineage = self.queue.pop()
            if lineage is None or len(lineage.prefix) >= self.cfg.max_len:
                continue

            self.stats.iters += 1
            results = self.klee.run(lineage.prefix, iter_time)
            cands = generate_candidates(self.cfg, lineage.prefix, results)
            fresh = self._process(lineage, cands, t0)
            if fresh:
                idle = 0
            self.reporter.event(
                t0, event="iter", i=self.stats.iters, plen=len(lineage.prefix),
                paths=len(results), fresh=fresh, queue=len(self.queue),
                valid=self.stats.valid, max_kwdepth=self.stats.max_kwdepth)

        self.stats.edges = len(self.coverage.edges)
        self.reporter.finish(t0, self.stats)
        return self.stats

    # ---- per-candidate processing --------------------------------------
    def _process(self, parent: Lineage, cands: list[Candidate], t0: float) -> int:
        fresh = 0
        for cand in cands:
            if cand.data in self.seen:
                continue
            self.seen.add(cand.data)
            fresh += 1
            self.stats.candidates += 1

            verdict = self.oracle.classify(cand.data)
            state, progress, gate_novelty = self._advance(parent, cand, verdict)

            if state.kwdepth > self.stats.max_kwdepth:
                self.stats.max_kwdepth, self.stats.best_prefix = state.kwdepth, cand.data
            self.stats.max_matched = max(self.stats.max_matched, len(state.matched))

            banked_kw = self._bank(cand) if verdict is Verdict.ACCEPT else False
            budget = self._requeue_budget(verdict, progress, parent.budget, gate_novelty)
            if budget is not None:
                self.queue.push(Lineage(cand.data, state, budget))

            self.reporter.event(
                t0, event="cand", n=self.stats.candidates,
                ascii=cand.data.decode("latin1", "replace"), rc=int(verdict),
                kwdepth=state.kwdepth, distinct=len(state.matched),
                novelty=state.novelty, valid=self.stats.valid, kw=banked_kw)
        return fresh

    def _advance(self, parent: Lineage, cand: Candidate,
                 verdict: Verdict) -> tuple[LineageState, bool, int]:
        """Compute the child lineage state, its progress flag, and the coverage
        novelty for the gated-requeue gate. Coverage is measured at most once per
        candidate, and only where this config actually uses it."""
        steering = self.queue.steering
        if self.cfg.steer is Steer.COVERAGE:
            # primary signal: measured for every candidate, drives state+progress;
            # also serves as the gate value if requeue is gated.
            novelty = self.coverage.novelty(cand.data)
            state, progress = steering.child(parent.state, cand, novelty)
            return state, progress, novelty
        # kwdepth: fold the pins first (no coverage needed for the fold)
        state, progress = steering.child(parent.state, cand, 0)
        if self.cfg.secondary_coverage and state.kwdepth == 0:
            state = replace(state, novelty=self.coverage.novelty(cand.data))
            return state, progress, 0
        if self.cfg.requeue is Requeue.GATED and verdict is Verdict.ACCEPT:
            return state, progress, self.coverage.novelty(cand.data)
        return state, progress, 0

    def _requeue_budget(self, verdict: Verdict, progress: bool,
                        parent_budget: int, gate_novelty: int) -> int | None:
        """The survival budget to requeue a candidate with, or None to drop it.

        ACCEPT: none -> drop; gated -> requeue (small budget) only on new
        coverage; plain -> treat like an alive prefix. ALIVE: full budget on
        progress, else spend one budget unit. DEAD / ERROR: drop."""
        if verdict is Verdict.ACCEPT:
            if self.cfg.requeue is Requeue.NONE:
                return None
            if self.cfg.requeue is Requeue.GATED:
                return self.cfg.requeue_budget if gate_novelty > 0 else None
            # PLAIN: fall through to the alive rule
        if verdict in (Verdict.ACCEPT, Verdict.ALIVE):
            if progress:
                return self.cfg.survival_budget
            if parent_budget > 0:
                return parent_budget - 1
        return None

    def _bank(self, cand: Candidate) -> bool:
        self.stats.valid += 1
        kw = self._is_keyword_bearing(cand.data)
        if kw:
            self.stats.kw_valid += 1
        name = f"{self.stats.valid:06d}{'-kw' if kw else ''}.bin"
        (self.valid_dir / name).write_bytes(cand.data)
        return kw

    def _is_keyword_bearing(self, data: bytes) -> bool:
        # Word-boundary token match, report-only. Caveat: it does not exclude a
        # keyword inside a string/comment, so on subjects with strings a flagged
        # `-kw` valid must be confirmed by hand (tinyc has no strings and rejects
        # multi-char non-keyword identifiers, so the flag is exact there).
        return any(
            re.search(br"(?<![A-Za-z0-9_])" + re.escape(k) + br"(?![A-Za-z0-9_])", data)
            for k in self._keywords
        )

    def _reseed(self) -> bool:
        """Refill an empty queue. With no-requeue (single-statement grammars)
        re-seed only from the empty prefix; otherwise re-extend recent banked
        valids — a re-run can yield new continuations. Returns True if real seeds
        were added (so the loop does not count it as idle)."""
        seeds = ([] if self.cfg.requeue is Requeue.NONE
                 else sorted(self.valid_dir.glob("*.bin"))[-10:])
        for p in seeds:
            self.queue.push(Lineage(p.read_bytes(), LineageState(), self.cfg.survival_budget))
        if not seeds:
            self.queue.push(Lineage(b"", LineageState(), self.cfg.survival_budget))
        return bool(seeds)
