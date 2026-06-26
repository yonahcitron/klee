#!/usr/bin/env python3
"""frontier driver — concrete-prefix / symbolic-frontier input generation.

pFuzzer's loop with KLEE's eyes (design + motivation:
.dev/parser-guided-eval/run8_pivot-synthesis/REPORT.md §7):

  queue of concrete prefixes (recipes, not states)
    -> pop best prefix
    -> one short KLEE run: prefix is concrete (hex argv), only a small
       window + its length are symbolic; every terminated path's ktest
       is one feasible continuation (solver-exact, no guess-and-check)
    -> candidates = prefix + win[:len], deduped
    -> classify each by native replay (exit 0 = parser accepted -> bank)
    -> novelty via afl-showmap edge bitmap; novel candidates re-enter
       the queue (accepted ones too: extension explores continuations)

The queue priority is (new edges desc, length asc) — pFuzzer's
novelty + minimality heuristic. Length never exceeds what the parse
asked for, so the length-exact wall (run8 REPORT §5.1) never appears.
"""

import argparse
import hashlib
import heapq
import json
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path


def parse_ktest(path):
    """Return {name: bytes} for the objects in a .ktest file."""
    objs = {}
    with open(path, "rb") as f:
        if f.read(5) not in (b"KTEST", b"BOUT\n"):
            return objs

        def u32():
            return struct.unpack(">I", f.read(4))[0]

        version = u32()
        for _ in range(u32()):          # args
            f.read(u32())
        if version >= 2:
            u32(); u32()                # symArgvs, symArgvLen
        for _ in range(u32()):          # objects
            name = f.read(u32())
            objs[name.decode("latin1")] = f.read(u32())
    return objs


class Frontier:
    def __init__(self, args):
        self.args = args
        self.work = Path(args.workdir)
        self.valid_dir = self.work / "valid"
        self.queue_dir = self.work / "queue"
        self.klee_dir = self.work / "klee-iters"
        for d in (self.valid_dir, self.queue_dir, self.klee_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.log = open(self.work / "log.jsonl", "a", buffering=1)

        # v2 queue strategy. Both default to the v1 behaviour when unset, so
        # a plain invocation (run9's run.sh) is byte-for-byte unchanged.
        #   survival_budget > 0 : a zero-novelty prefix survives that many
        #       consecutive extensions before it is dropped, instead of being
        #       pruned on the first one. Lets a lineage cross the identifier
        #       "valley" (w -> wh -> whi -> whil, each ~0 new edges) to reach
        #       the keyword peak (while, +88 edges) that the run9 probe found.
        #   root_fair : round-robin over first-byte buckets instead of one
        #       global novelty heap, so the high-novelty basin (e.g. luac's
        #       U[-0]=... , 96% of run9's valid corpus) cannot monopolise pops
        #       and starve every other root. Survival keeps the valley alive;
        #       root_fair is what actually pops it. They are co-required.
        #   prefer_depth : among equal-novelty entries, pop the DEEPEST prefix
        #       (drive a lineage down depth-first) instead of the shortest.
        #       v2 left the shortest-first tiebreaker, so survival flooded the
        #       priority-0 tier with shallow prefixes and selection went
        #       breadth-first — run10 luac never exceeded length 4, too shallow
        #       for any keyword statement (`do end` needs 6). This is the v3
        #       fix: novelty still dominates; this only re-orders ties.
        self.survival_budget = args.survival_budget
        self.root_fair = args.root_fair
        self.prefer_depth = args.prefer_depth
        # v5: steer by the program's own comparison signal instead of coverage.
        #   steer_score : queue priority is a 'SCORE <n>' the native harness
        #       prints — a comparison-match depth read from the program's
        #       comparison (pFuzzer's signal: how close the input got to a
        #       gated token), NOT afl-showmap edge novelty. Coverage novelty
        #       is captured by trivially-valid productions (runs 9-12);
        #       comparison-match is not.
        #   max_cands : cap candidates classified per iteration so a wide
        #       window's candidate flood cannot starve prefix-building.
        self.steer_score = args.steer_score
        self.steer_survival = args.steer_survival
        self.max_cands = args.max_cands
        self.seen = set()        # sha1 of candidate bytes
        self.edges = set()       # global showmap edge ids
        self.heap = []           # global mode: (-new_edges, len, seq, bytes, budget)
        self.buckets = {}        # root_fair mode: first-byte -> heap
        self.roots = []          # root_fair mode: bucket keys in round-robin order
        self.rr = 0              # round-robin cursor
        self.n_queued = 0        # entries currently queued (either mode)
        self.seq = 0
        self.n_valid = 0
        self.n_iter = 0
        self.n_candidates = 0
        self.push(b"", 1, self.survival_budget)   # start from the empty prefix

    def push(self, prefix, new_edges, budget):
        length_key = -len(prefix) if self.prefer_depth else len(prefix)
        entry = (-new_edges, length_key, self.seq, prefix, budget)
        self.seq += 1
        if self.root_fair:
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
        """Next (prefix, budget) to extend, or None if the queue is empty.

        Global mode: strict best-first (-new_edges, then shortest, then FIFO).
        root_fair mode: round-robin over first-byte buckets, taking each
        bucket's best entry — fair across roots, best-first within a root.
        """
        if self.root_fair:
            n = len(self.roots)
            for _ in range(n):
                bucket = self.buckets[self.roots[self.rr % n]]
                self.rr += 1
                if bucket:
                    self.n_queued -= 1
                    entry = heapq.heappop(bucket)
                    return entry[3], entry[4]
            return None
        if not self.heap:
            return None
        self.n_queued -= 1
        entry = heapq.heappop(self.heap)
        return entry[3], entry[4]

    def emit(self, **kw):
        kw["t"] = round(time.time() - self.t0, 1)
        self.log.write(json.dumps(kw) + "\n")

    # ---- native replay -------------------------------------------------
    def classify(self, data):
        """Run the native (AFL-instrumented) binary; return exit code."""
        try:
            r = subprocess.run([self.args.native_bin], input=data,
                               capture_output=True, timeout=5)
            return r.returncode
        except subprocess.TimeoutExpired:
            return -1
        except OSError:
            return -2

    def novelty(self, data):
        """afl-showmap edge ids new vs the global set (and absorb them)."""
        mapfile = self.work / "showmap.tmp"
        try:
            subprocess.run(
                [self.args.showmap, "-m", "none", "-t", "1000", "-q",
                 "-o", str(mapfile), "--", self.args.native_bin],
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

    def classify_score(self, data):
        """Run the native harness; return (exit_code, depth, alive). The harness
        prints 'SCORE <depth> [alive]' to stderr: depth is a comparison-match
        depth lifted from the program's own comparison (e.g. an instrumented
        strcmp — the keyword values come from the program, not a list); alive
        (v6, default 1 if absent) is 1 iff that match broke at the input's end
        (a recoverable prefix) rather than on a committed byte (dead)."""
        try:
            r = subprocess.run([self.args.native_bin], input=data,
                               capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            return -1, 0, 0
        except OSError:
            return -2, 0, 0
        depth, alive = 0, 1
        for line in r.stderr.splitlines():
            if line.startswith(b"SCORE "):
                parts = line.split()
                try:
                    d = int(parts[1])
                    if d >= depth:
                        depth = d
                        alive = int(parts[2]) if len(parts) > 2 else 1
                except (ValueError, IndexError):
                    pass
        return r.returncode, depth, alive

    def measure(self, data):
        """(exit_code, signal); signal is the queue-steering quantity —
        comparison-match score (v5/v6) or afl-showmap edge novelty (default)."""
        if self.steer_score:
            rc, depth, alive = self.classify_score(data)
            if self.steer_survival:
                # v6: comparison-match DEPTH leads; the alive bit only prunes
                # dead ends. A match that broke on a committed byte (dead) and
                # did not parse gets no priority (survival budget only);
                # everything still live (alive prefix) or accepted (valid — and
                # banked at generation regardless) is ranked by how deep it
                # matched a keyword, so the deepest live ladder (`while`, 5)
                # outranks trivial valids (`d;`, 1) and completes into
                # `while(0);`. A flat valid bonus instead let one-char valids
                # (`d;`, signal 1001) swamp the whole ladder — the first-cut bug.
                sig = depth if (alive or rc == 0) else 0
            else:
                sig = depth          # v5: raw match depth (can't tell whil/whila)
            return rc, sig
        return self.classify(data), self.novelty(data)

    # ---- one KLEE iteration --------------------------------------------
    def klee_iter(self, prefix, iter_time):
        out = self.klee_dir / f"iter-{self.n_iter:06d}"
        shutil.rmtree(out, ignore_errors=True)
        hexarg = prefix.hex() if prefix else "-"
        cmd = [self.args.klee,
               "--libc=uclibc", "--posix-runtime",
               f"--max-memory={self.args.klee_memory}",
               f"--max-time={iter_time}s",
               f"--output-dir={out}",
               "--warnings-only-to-file",
               self.args.subject_bc, hexarg]
        try:
            subprocess.run(cmd, capture_output=True,
                           timeout=iter_time * 3 + 120)
        except subprocess.TimeoutExpired:
            subprocess.run(["pkill", "-9", "-f", str(out)],
                           capture_output=True)
        cands = []
        for kt in sorted(out.glob("*.ktest")):
            objs = parse_ktest(kt)
            win, ln = objs.get("win"), objs.get("len")
            if win is None or ln is None or not ln:
                continue
            n = min(ln[0], len(win))
            cands.append(prefix + win[:n])
        if not self.args.keep_iters:
            shutil.rmtree(out, ignore_errors=True)
        return cands

    # ---- main loop -------------------------------------------------------
    def run(self):
        self.t0 = time.time()
        deadline = self.t0 + self.args.max_seconds
        idle_refills = 0

        while time.time() < deadline:
            if self.n_queued == 0:
                # Re-extend banked valid inputs (KLEE truncation + search
                # randomness mean a re-run can yield new continuations);
                # fall back to the empty prefix.
                refill = sorted(self.valid_dir.glob("*.bin"))[-20:]
                for p in refill:
                    self.push(p.read_bytes(), 0, self.survival_budget)
                if not refill:
                    self.push(b"", 0, self.survival_budget)
                idle_refills += 1
                if idle_refills > 50:
                    self.emit(event="giving-up", reason="queue dry")
                    break

            # Clamp the iteration budget so a run started near the deadline
            # cannot overshoot it by a full --iter-time.
            remaining = int(deadline - time.time())
            if remaining < 15:
                break
            iter_time = min(self.args.iter_time, remaining)

            popped = self.pop()
            if popped is None:
                continue
            prefix, budget = popped
            if len(prefix) >= self.args.max_len:
                continue
            self.n_iter += 1
            cands = self.klee_iter(prefix, iter_time)
            if self.max_cands and len(cands) > self.max_cands:
                cands = cands[:self.max_cands]
            fresh = 0
            for cand in cands:
                h = hashlib.sha1(cand).hexdigest()
                if h in self.seen:
                    continue
                self.seen.add(h)
                fresh += 1
                self.n_candidates += 1
                rc, signal = self.measure(cand)
                if rc == 0:
                    self.n_valid += 1
                    (self.valid_dir / f"{self.n_valid:06d}.bin"
                     ).write_bytes(cand)
                if signal > 0 or rc == 0:
                    (self.queue_dir / f"{h[:16]}.bin").write_bytes(cand)
                if signal > 0:
                    # Promising: full priority, budget refilled.
                    self.push(cand, signal, self.survival_budget)
                elif budget > 0:
                    # No signal but still in survival budget: keep it alive
                    # at floor priority so it can reach a downstream peak.
                    self.push(cand, 0, budget - 1)
                self.emit(event="cand", n=self.n_candidates, len=len(cand),
                          rc=rc, new_edges=signal, valid=self.n_valid)
            if fresh:
                idle_refills = 0
            self.emit(event="iter", i=self.n_iter, plen=len(prefix),
                      ktests=len(cands), fresh=fresh, queue=self.n_queued,
                      valid=self.n_valid, edges=len(self.edges))

        self.emit(event="done", iters=self.n_iter, valid=self.n_valid,
                  candidates=self.n_candidates, edges=len(self.edges))
        print(f"frontier done: {self.n_iter} iters, "
              f"{self.n_candidates} candidates, {self.n_valid} valid, "
              f"{len(self.edges)} edges")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subject-bc", required=True)
    ap.add_argument("--native-bin", required=True,
                    help="native harness: classification + showmap (default) "
                         "or SCORE output (--steer-score)")
    ap.add_argument("--showmap", default=None,
                    help="path to afl-showmap (required unless --steer-score)")
    ap.add_argument("--klee", default="klee")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--max-seconds", type=int, default=3600)
    ap.add_argument("--iter-time", type=int, default=30,
                    help="KLEE --max-time per iteration (s)")
    ap.add_argument("--klee-memory", type=int, default=2000)
    ap.add_argument("--max-len", type=int, default=512,
                    help="stop extending prefixes beyond this length")
    ap.add_argument("--survival-budget", type=int, default=0,
                    help="consecutive zero-novelty extensions a lineage may "
                         "survive before being dropped (0 = v1: prune on the "
                         "first; >0 lets it cross the identifier valley)")
    ap.add_argument("--root-fair", action="store_true",
                    help="round-robin over first-byte buckets instead of one "
                         "global novelty heap, so the basin cannot monopolise "
                         "pops (v1 = off)")
    ap.add_argument("--prefer-depth", action="store_true",
                    help="among equal-novelty entries pop the deepest prefix "
                         "(depth-first within a lineage) instead of the "
                         "shortest; needed so survival does not thrash "
                         "breadth-first (v3, off in v1/v2)")
    ap.add_argument("--steer-score", action="store_true",
                    help="steer the queue by the 'SCORE <n>' the native "
                         "harness prints (comparison-match depth) instead of "
                         "afl-showmap edge novelty (v5)")
    ap.add_argument("--steer-survival", action="store_true",
                    help="v6: combine the score with the harness's alive bit "
                         "and exit code — valid > alive keyword-prefix > dead "
                         "fragment — so dead matches and trivia cannot starve "
                         "the live prefixes (requires --steer-score)")
    ap.add_argument("--max-cands", type=int, default=0,
                    help="cap candidates classified per iteration (0 = no "
                         "cap); stops a wide window's flood starving the loop")
    ap.add_argument("--keep-iters", action="store_true")
    args = ap.parse_args()
    if not args.steer_score and not args.showmap:
        ap.error("--showmap is required unless --steer-score is set")
    Frontier(args).run()


if __name__ == "__main__":
    sys.exit(main())
