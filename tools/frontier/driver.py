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

        self.seen = set()        # sha1 of candidate bytes
        self.edges = set()       # global showmap edge ids
        self.heap = []           # (-new_edges, len, seq, bytes)
        self.seq = 0
        self.n_valid = 0
        self.n_iter = 0
        self.n_candidates = 0
        self.push(b"", 1)        # start from the empty prefix

    def push(self, prefix, new_edges):
        heapq.heappush(self.heap, (-new_edges, len(prefix), self.seq, prefix))
        self.seq += 1

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
            if not self.heap:
                # Re-extend banked valid inputs (KLEE truncation + search
                # randomness mean a re-run can yield new continuations);
                # fall back to the empty prefix.
                refill = sorted(self.valid_dir.glob("*.bin"))[-20:]
                for p in refill:
                    self.push(p.read_bytes(), 0)
                if not refill:
                    self.push(b"", 0)
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

            _, _, _, prefix = heapq.heappop(self.heap)
            if len(prefix) >= self.args.max_len:
                continue
            self.n_iter += 1
            cands = self.klee_iter(prefix, iter_time)
            fresh = 0
            for cand in cands:
                h = hashlib.sha1(cand).hexdigest()
                if h in self.seen:
                    continue
                self.seen.add(h)
                fresh += 1
                self.n_candidates += 1
                rc = self.classify(cand)
                new_edges = self.novelty(cand)
                if rc == 0:
                    self.n_valid += 1
                    (self.valid_dir / f"{self.n_valid:06d}.bin"
                     ).write_bytes(cand)
                if new_edges > 0 or rc == 0:
                    (self.queue_dir / f"{h[:16]}.bin").write_bytes(cand)
                if new_edges > 0:
                    self.push(cand, new_edges)
                self.emit(event="cand", n=self.n_candidates, len=len(cand),
                          rc=rc, new_edges=new_edges, valid=self.n_valid)
            if fresh:
                idle_refills = 0
            self.emit(event="iter", i=self.n_iter, plen=len(prefix),
                      ktests=len(cands), fresh=fresh, queue=len(self.heap),
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
                    help="AFL-instrumented harness (classification + showmap)")
    ap.add_argument("--showmap", required=True, help="path to afl-showmap")
    ap.add_argument("--klee", default="klee")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--max-seconds", type=int, default=3600)
    ap.add_argument("--iter-time", type=int, default=30,
                    help="KLEE --max-time per iteration (s)")
    ap.add_argument("--klee-memory", type=int, default=2000)
    ap.add_argument("--max-len", type=int, default=512,
                    help="stop extending prefixes beyond this length")
    ap.add_argument("--keep-iters", action="store_true")
    args = ap.parse_args()
    Frontier(args).run()


if __name__ == "__main__":
    sys.exit(main())
