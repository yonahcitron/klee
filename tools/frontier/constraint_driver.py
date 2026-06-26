#!/usr/bin/env python3
"""constraint_driver.py — pFuzzer-via-KLEE (run13 / constraint-steering).

The one untested lever from the frontier line. The earlier driver (driver.py,
v1-v6) read pFuzzer's two signals from *outside* the program — concrete ktest
bytes for generation, a wrapped strcmp for survival — and both leaked
(run13 README). This driver reads them from KLEE's *own execution state*:

  1. DIRECTED GENERATION (closes leak #1). The frontier is ONE symbolic byte
     (window 1, pFuzzer's grain). KLEE explores it and, for every terminated
     path, the .kquery dump records what the program compared that byte
     against. A byte positively pinned by `(Eq C Read(0,win))` is a *matched*
     continuation — the value the program's strcmp/switch wanted (a keyword
     char, a token char). We extend by these. A byte that only satisfies a
     range (`Sle 97 .. Sle .. 122`, the identifier/number slot) is *unmatched*
     — kept, but never preferred. The junk pFuzzer never generates (`whila`,
     `whil@`, 0xff filler) simply never carries a positive pin, so it never
     gains priority. (kquery.py does the pin extraction; validated on ~47k
     real tiny.c paths.)

  2. TRUE SURVIVAL (closes leak #2). Each candidate is classified by the
     program's control flow, read from an instrumented native replay
     (tinyc_survival.c): accept (exit 0), alive = errored only after reading
     PAST end-of-input (exit 10, a viable prefix that wants more — KLEE's
     len-fork made concrete), dead = errored on a byte that was present
     (exit 11, committed). This is the "did it error or want more?" pFuzzer
     observes, not v6's strcmp-stop inference.

Priority pursues *distinct comparison-match progress* (the count of distinct
byte-values the program matched along the lineage — a repeated structural token
like '(' is matched every time but adds no new value, so a "(((..." basin cannot
monopolise the queue), banks accepts, and prunes lineages that stop making NEW
matches via a survival budget — so a keyword ladder (w->wh->whi->whil->while,
five new values) is climbed while a dead-end fragment (`whila`, distinct frozen
at 4) or a repetitive basin sinks. The single unmatched
byte a `while(<expr>)` needs (the range-recognised digit/identifier) costs one
budget unit, refilled by the next matched byte (`)`, `;`). Reaching a banked
keyword statement (`while(0);`, `if(0)d;`, `do d;while(0);`) on tiny.c is the
criterion-2 result v5/v6 could not produce; staying at 0 on hash-intern luac
(no positive pins to a keyword gradient) is the boundary confirmation.

Subject-agnostic: any window-1 KLEE bitcode (frontier_common.h: objects `win`,
`len`) + a survival harness reading stdin and exiting 0/10/11.
"""

import argparse
import heapq
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kquery import win_pins  # noqa: E402


def parse_ktest(path):
    """{name: bytes} for the objects in a .ktest (KTEST/BOUT format)."""
    objs = {}
    with open(path, "rb") as f:
        if f.read(5) not in (b"KTEST", b"BOUT\n"):
            return objs

        def u32():
            return struct.unpack(">I", f.read(4))[0]

        version = u32()
        for _ in range(u32()):              # args
            f.read(u32())
        if version >= 2:
            u32(); u32()                    # symArgvs, symArgvLen
        for _ in range(u32()):              # objects
            name = f.read(u32())
            objs[name.decode("latin1")] = f.read(u32())
    return objs


# survival-harness exit codes (tinyc_survival.c)
ACCEPT, ALIVE, DEAD = 0, 10, 11


class ConstraintDriver:
    def __init__(self, args):
        self.args = args
        self.work = Path(args.workdir)
        self.valid_dir = self.work / "valid"
        self.klee_dir = self.work / "klee-iters"
        for d in (self.valid_dir, self.klee_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.log = open(self.work / "log.jsonl", "a", buffering=1)

        self.budget = args.survival_budget
        self.root_fair = args.root_fair
        self.no_requeue = args.no_requeue
        self.prefer_kwdepth = args.prefer_kwdepth
        self.unified = args.unified
        self.requeue_gated = args.requeue_gated   # v18: requeue accept only if new coverage
        self.requeue_budget = args.requeue_budget  # v18: small budget for requeued accepts
        self.edges = set()       # global afl-showmap edge ids (unified / requeue-gate)
        self.seen = set()        # candidate bytes already classified
        self.heap = []           # global mode: (-distinct, plen, seq, prefix, matched, budget)
        self.buckets = {}        # root_fair mode: first-byte -> heap
        self.roots = []          # root_fair mode: bucket keys, round-robin order
        self.rr = 0              # round-robin cursor
        self.n_queued = 0        # entries queued (either mode)
        self.seq = 0
        self.n_valid = 0
        self.n_kw_valid = 0      # banked valids containing a keyword token
        self.n_iter = 0
        self.n_cands = 0
        self.max_matched = 0     # max distinct matched values, any lineage
        self.max_kwdepth = 0     # max keyword-prefix match depth, any lineage
        self.best_prefix = b""
        # keyword tokens to *recognise* in banked valids for reporting only —
        # NOT used for steering (steering is the program's own Eq pins). tiny.c
        # keywords; override via --keywords for another subject.
        self.keywords = [k.encode() for k in args.keywords.split(",") if k]
        self.push(b"", frozenset(), self.budget, 0, 0)

    # ---- queue ----------------------------------------------------------
    def push(self, prefix, matched, budget, kwdepth, cur_run, novelty=0):
        # priority keys, in order:
        #  1. (--prefer-kwdepth) deepest keyword-prefix match — LOCAL token
        #     progress. `while` reaches depth 5, retained as max-ever even after
        #     the token completes (so the lineage isn't abandoned at `while(`),
        #     while diverse expressions stay ~1. The v1 global-distinct count
        #     could NOT prefer the narrow keyword ladder (run13-v1 cause 3); this
        #     can. Accurate survival prunes deep-but-dead fragments (`whil_`)
        #     before they reach the queue, so they cannot inflate kwdepth.
        #  2. most DISTINCT matched values — kills repeated-token basins ("(((").
        #  3. shortest, then FIFO.
        primary = -kwdepth if self.prefer_kwdepth else 0
        # secondary key, criterion-appropriate in --unified mode:
        #   keyword lineages (kwdepth>0) keep DISTINCT — v14's beeline to
        #     completion, and no afl-showmap cost on the hot path;
        #   validity lineages (kwdepth==0) use coverage NOVELTY — run9's breadth.
        # So v15 == v14 on keyword climbing and adds coverage only where it
        # helps. (Plain mode always uses distinct.)
        if self.unified and kwdepth == 0:
            secondary = -novelty
        else:
            secondary = -len(matched)
        entry = (primary, secondary, len(prefix), self.seq,
                 prefix, matched, budget, kwdepth, cur_run)
        self.seq += 1
        if self.root_fair:
            # bucket by first byte so the large expression space (e.g. '('-rooted)
            # cannot monopolise pops and starve the 'w'-rooted keyword lineage.
            root = prefix[:1]
            bucket = self.buckets.get(root)
            if bucket is None:
                bucket = self.buckets[root] = []
                self.roots.append(root)
            heapq.heappush(bucket, entry)
        else:
            heapq.heappush(self.heap, entry)
        self.n_queued += 1

    def pop(self):
        if self.root_fair:
            n = len(self.roots)
            for _ in range(n):
                bucket = self.buckets[self.roots[self.rr % n]]
                self.rr += 1
                if bucket:
                    self.n_queued -= 1
                    e = heapq.heappop(bucket)
                    return e[4], e[5], e[6], e[7], e[8]
            return None
        if not self.heap:
            return None
        self.n_queued -= 1
        e = heapq.heappop(self.heap)
        return e[4], e[5], e[6], e[7], e[8]  # prefix, matched, budget, kwdepth, cur_run

    def emit(self, **kw):
        kw["t"] = round(time.time() - self.t0, 1)
        self.log.write(json.dumps(kw) + "\n")

    # ---- classification (true survival, instrumented native replay) -----
    def classify(self, data):
        try:
            r = subprocess.run([self.args.survival_bin], input=data,
                               capture_output=True, timeout=5)
            return r.returncode
        except subprocess.TimeoutExpired:
            return -1
        except OSError:
            return -2

    def novelty(self, data):
        """afl-showmap edge ids new vs the global set (and absorb them) — the
        --unified secondary steering signal (run9/pFuzzer's coverage novelty).
        It drives kwdepth-0 (non-keyword) lineages toward diverse valid inputs
        (criterion 1), while kwdepth still owns the keyword ladders (criterion 2)
        as the PRIMARY key. Requires an afl-instrumented survival harness.
        Also used (when --requeue-gated) as the accept-requeue coverage gate."""
        if not self.args.showmap:
            return 0
        mapfile = self.work / "showmap.tmp"
        try:
            subprocess.run(
                [self.args.showmap, "-m", "none", "-t", "1000", "-q",
                 "-o", str(mapfile), "--", self.args.survival_bin],
                input=data, capture_output=True, timeout=10,
                env={"AFL_QUIET": "1", "PATH": "/usr/bin:/bin"})
        except (subprocess.TimeoutExpired, OSError):
            return 0
        try:
            ids = {line.split(":", 1)[0]
                   for line in mapfile.read_text().splitlines() if ":" in line}
        except OSError:
            return 0
        new = ids - self.edges
        self.edges |= new
        return len(new)

    def is_keyword_bearing(self, data):
        # Word-boundary token match (so `while_x`/`whilst` do not count).
        # REPORTING ONLY — never used for steering. Caveat: this does NOT
        # exclude a keyword inside a string/comment, so on luac a flagged
        # `-kw` valid must be confirmed by hand as a keyword-gated production
        # (tinyc has no strings and rejects multi-char non-keyword identifiers,
        # so the flag is exact there). See DESIGN.md "Honest limitations".
        return any(re.search(br"(?<![A-Za-z0-9_])" + re.escape(k)
                             + br"(?![A-Za-z0-9_])", data)
                   for k in self.keywords)

    # ---- one KLEE iteration: directed frontier bytes for a prefix -------
    def klee_iter(self, prefix, iter_time):
        """Run KLEE on `prefix` with a symbolic window (size baked into the
        bitcode) + symbolic length + --write-kqueries. Return {suffix_bytes:
        [(byte, pinned?), ...]} — each terminated path's continuation win[:len]
        with, per position, whether it carries a positive Eq pin (a directed /
        keyword / token byte) vs only a range bound (the id/number slot)."""
        out = self.klee_dir / f"iter-{self.n_iter:06d}"
        shutil.rmtree(out, ignore_errors=True)
        hexarg = prefix.hex() if prefix else "-"
        cmd = [self.args.klee,
               "--libc=uclibc", "--posix-runtime",
               f"--max-memory={self.args.klee_memory}",
               f"--max-time={iter_time}s",
               f"--output-dir={out}",
               "--write-kqueries",
               "--warnings-only-to-file",
               self.args.subject_bc, hexarg]
        try:
            subprocess.run(cmd, capture_output=True, timeout=iter_time * 3 + 60)
        except subprocess.TimeoutExpired:
            subprocess.run(["pkill", "-9", "-f", str(out)], capture_output=True)

        frontier = {}      # suffix bytes -> tuple of (byte, pinned?) for the new bytes
        for kt in sorted(out.glob("*.ktest")):
            objs = parse_ktest(kt)
            win, ln = objs.get("win"), objs.get("len")
            if win is None or ln is None or not ln or ln[0] < 1 or not win:
                continue                    # len-0 path = the prefix itself
            n = min(ln[0], len(win))
            suffix = bytes(win[:n])
            kq = kt.with_suffix(".kquery")
            pins = win_pins(kq.read_text()) if kq.exists() else {}
            info = tuple((win[i], i in pins) for i in range(n))
            prev = frontier.get(suffix)
            if prev is None:
                frontier[suffix] = info
            else:   # same suffix via another path: pinned if either path pinned it
                frontier[suffix] = tuple((b, p or q)
                                         for (b, p), (_, q) in zip(prev, info))
        if not self.args.keep_iters:
            shutil.rmtree(out, ignore_errors=True)
        return frontier

    # ---- main loop ------------------------------------------------------
    def run(self):
        self.t0 = time.time()
        deadline = self.t0 + self.args.max_seconds
        idle = 0

        while time.time() < deadline:
            if self.n_queued == 0:
                # Re-seed. With --no-requeue (single-statement grammars) banked
                # valids are NOT extended (every extension is an extra-token
                # error), so re-seed only from the empty prefix. Otherwise extend
                # recent banked valids (multi-statement grammars).
                refill = [] if self.no_requeue \
                    else sorted(self.valid_dir.glob("*.bin"))[-10:]
                for p in refill:
                    self.push(p.read_bytes(), frozenset(), self.budget, 0, 0)
                if not refill:
                    self.push(b"", frozenset(), self.budget, 0, 0)
                idle += 1
                if idle > 30:
                    self.emit(event="giving-up", reason="queue dry")
                    break

            remaining = int(deadline - time.time())
            if remaining < 5:
                break
            iter_time = min(self.args.iter_time, max(remaining - 2, 1))

            popped = self.pop()
            if popped is None:
                continue
            prefix, matched, budget, kwdepth, cur_run = popped
            if len(prefix) >= self.args.max_len:
                continue
            self.n_iter += 1

            frontier = self.klee_iter(prefix, iter_time)
            fresh = 0
            for suffix, info in sorted(frontier.items()):
                cand = prefix + suffix
                if cand in self.seen:
                    continue
                self.seen.add(cand)
                fresh += 1
                self.n_cands += 1
                rc = self.classify(cand)
                # Fold the new window byte(s) into the lineage state, left to
                # right (window-1 = one iteration of this loop):
                #   - distinct matched set (kills repeated-token basins);
                #   - progress = a NEW pinned value was added (refills survival
                #     budget; repetitive basins exhaust it);
                #   - keyword-prefix depth = trailing run of consecutive matched
                #     LETTERS (keyword-path signature), retained max-ever.
                new_matched, run, kd, progress = matched, cur_run, kwdepth, False
                for byteval, pinned in info:
                    if pinned and byteval not in new_matched:
                        progress = True
                    if pinned:
                        new_matched = new_matched | {byteval}
                    is_letter = 97 <= byteval <= 122
                    run = (run + 1) if (pinned and is_letter) else 0
                    kd = max(kd, run)
                mc, new_kwdepth, new_run = len(new_matched), kd, run
                # coverage novelty as the SECONDARY steering key: only in
                # --unified mode, and only for validity lineages (kwdepth==0), so
                # keyword ladders stay v14-fast. In --requeue-gated mode novelty
                # is NOT computed per candidate (it would absorb edges before the
                # accept gate sees them) — it is computed once, at the accept.
                nov = self.novelty(cand) if (self.unified and new_kwdepth == 0) else 0
                if mc > self.max_matched:
                    self.max_matched = mc
                if new_kwdepth > self.max_kwdepth:
                    self.max_kwdepth, self.best_prefix = new_kwdepth, cand

                banked_kw = False
                if rc == ACCEPT:
                    self.n_valid += 1
                    kw = self.is_keyword_bearing(cand)
                    if kw:
                        self.n_kw_valid += 1
                        banked_kw = True
                    name = f"{self.n_valid:06d}{'-kw' if kw else ''}.bin"
                    (self.valid_dir / name).write_bytes(cand)
                    # an accepted prefix can still be extended into a larger
                    # program — but only where the grammar allows >1 statement.
                    # --no-requeue (tinyc) skips this: extending a complete
                    # statement just spawns extra-token dead-ends (run13 cause 1).
                    if self.requeue_gated:
                        # v18 controlled requeue: extend an accepted prefix ONLY
                        # if it reaches NEW coverage, on a small separate budget.
                        # Trivial valids (`;`, `;\n`, `--`) re-cover the same edges
                        # → novelty 0 after the first → not requeued, so requeue
                        # cannot mine trivial-valid volume (run17's failure mode);
                        # the budget goes to accepts that reach new structure.
                        if self.novelty(cand) > 0:
                            self.push(cand, new_matched, self.requeue_budget,
                                      new_kwdepth, new_run, 0)
                    elif not self.no_requeue:
                        self.push(cand, new_matched, self.budget, new_kwdepth, new_run, nov)
                elif rc == ALIVE:
                    child_budget = self.budget if progress else budget - 1
                    if child_budget >= 0:
                        self.push(cand, new_matched, child_budget, new_kwdepth, new_run, nov)
                # DEAD / timeout: drop (committed error — no append recovers it)

                self.emit(event="cand", n=self.n_cands, bytes=cand.hex(),
                          ascii=cand.decode("latin1", "replace"),
                          suffix=suffix.decode("latin1", "replace"),
                          progress=progress, distinct=mc,
                          kwdepth=new_kwdepth, novelty=nov, rc=rc,
                          valid=self.n_valid, kw=banked_kw)

            if fresh:
                idle = 0
            self.emit(event="iter", i=self.n_iter,
                      prefix=prefix.decode("latin1", "replace"), plen=len(prefix),
                      distinct=len(matched), kwdepth=kwdepth,
                      frontier_n=len(frontier), fresh=fresh,
                      queue=self.n_queued, valid=self.n_valid,
                      kw_valid=self.n_kw_valid, max_matched=self.max_matched,
                      max_kwdepth=self.max_kwdepth)

        self.emit(event="done", iters=self.n_iter, candidates=self.n_cands,
                  valid=self.n_valid, kw_valid=self.n_kw_valid,
                  max_matched=self.max_matched, max_kwdepth=self.max_kwdepth,
                  best=self.best_prefix.decode("latin1", "replace"))
        print(f"constraint-driver done: {self.n_iter} iters, {self.n_cands} "
              f"candidates, {self.n_valid} valid ({self.n_kw_valid} "
              f"keyword-bearing), max keyword-prefix depth {self.max_kwdepth} "
              f"(distinct {self.max_matched}), deepest "
              f"{self.best_prefix.decode('latin1', 'replace')!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject-bc", required=True,
                    help="window-1 KLEE bitcode (frontier_common.h, FRONTIER_WINDOW=1)")
    ap.add_argument("--survival-bin", required=True,
                    help="native harness reading stdin, exit 0=accept/10=alive/11=dead")
    ap.add_argument("--klee", default="klee")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--max-seconds", type=int, default=3600)
    ap.add_argument("--iter-time", type=int, default=15,
                    help="KLEE --max-time per frontier byte (s); window-1 runs "
                         "are tiny, this is a ceiling")
    ap.add_argument("--klee-memory", type=int, default=2000)
    ap.add_argument("--max-len", type=int, default=64,
                    help="stop extending prefixes beyond this length")
    ap.add_argument("--survival-budget", type=int, default=8,
                    help="consecutive UNMATCHED extensions a lineage may take "
                         "before it is dropped; refilled on a matched byte. Lets "
                         "a keyword statement cross its one range-recognised "
                         "expression byte (the digit/identifier in `while(0)`)")
    ap.add_argument("--no-requeue", action="store_true",
                    help="do not re-queue banked valids for extension. Correct "
                         "for single-statement grammars (tinyc): extending a "
                         "complete statement only spawns extra-token dead-ends "
                         "(run13-v1 cause 1)")
    ap.add_argument("--root-fair", action="store_true",
                    help="round-robin over first-byte buckets instead of one "
                         "global heap, so a large lineage family (e.g. '('-rooted "
                         "expressions) cannot starve the 'w'-rooted keyword "
                         "lineage (run13-v1 cause 3 mitigation)")
    ap.add_argument("--prefer-kwdepth", action="store_true",
                    help="prioritise by keyword-prefix match depth (trailing run "
                         "of matched letters, retained max-ever) over distinct "
                         "count — local token progress, so the narrow keyword "
                         "ladder (`while`, depth 5) is pursued over diverse "
                         "expressions (depth ~1). Needs accurate survival so "
                         "deep-but-dead fragments are pruned (run13-v1 cause 3 fix)")
    ap.add_argument("--keywords", default="do,else,if,while",
                    help="comma-separated keyword tokens to flag in banked "
                         "valids FOR REPORTING ONLY (not used for steering)")
    ap.add_argument("--unified", action="store_true",
                    help="run15: combine run9 + run14. Keyword-depth stays the "
                         "PRIMARY priority (criterion 2); the secondary becomes "
                         "afl-showmap coverage novelty (criterion 1 breadth — "
                         "run9's validity-generation signal) instead of distinct "
                         "count. Requires --showmap and an afl-instrumented "
                         "survival harness. A clean superset of run14: keyword "
                         "climbing is unchanged, validity steering is improved")
    ap.add_argument("--showmap", default=None,
                    help="path to afl-showmap (required with --unified or "
                         "--requeue-gated)")
    ap.add_argument("--requeue-gated", action="store_true",
                    help="v18: CONTROLLED requeue. Requeue an accepted valid only "
                         "if it earns NEW afl-showmap coverage, on a small "
                         "--requeue-budget. Targets run17's failure mode where "
                         "uncontrolled requeue mined trivial-valid volume "
                         "(`;\\n\\n`); the budget instead goes to accepts reaching "
                         "new structure (`x=1;y=2`). Requires --showmap; mutually "
                         "exclusive with --no-requeue and --unified.")
    ap.add_argument("--requeue-budget", type=int, default=3,
                    help="survival budget for controlled-requeue accepted-prefix "
                         "extensions (v18). Small, so they cannot dominate/basin.")
    ap.add_argument("--keep-iters", action="store_true")
    args = ap.parse_args()
    if args.unified and not args.showmap:
        ap.error("--unified requires --showmap (and an afl-instrumented "
                 "--survival-bin)")
    if args.requeue_gated:
        if not args.showmap:
            ap.error("--requeue-gated requires --showmap (the coverage gate)")
        if args.no_requeue or args.unified:
            ap.error("--requeue-gated is mutually exclusive with --no-requeue "
                     "and --unified")
    ConstraintDriver(args).run()


if __name__ == "__main__":
    sys.exit(main())
