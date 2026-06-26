"""klee.py — run one KLEE iteration over a concrete prefix + symbolic window.

The concrete prefix is passed as a hex ``argv[1]``; the window (object ``win``)
and its length (object ``len``) are symbolic, their sizes baked into the bitcode
(frontier_common.h ``FRONTIER_WINDOW``). Each terminated path is one feasible,
solver-exact continuation — no guess-and-check. ``--write-kqueries`` is added
only when eq-pinned generation needs the comparison constraints.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .kquery import win_pins


@dataclass(frozen=True)
class PathResult:
    """One terminated KLEE path: the window bytes it chose, the length it forked,
    and (eq-pinned only) the {window-index: byte} it positively pinned."""

    win: bytes
    length: int
    pins: dict = field(default_factory=dict)


def parse_ktest(path: Path) -> dict:
    """{object-name: bytes} for a .ktest (KTEST / BOUT format)."""
    objs: dict = {}
    with open(path, "rb") as f:
        if f.read(5) not in (b"KTEST", b"BOUT\n"):
            return objs

        def u32() -> int:
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


class KleeRunner:
    """Runs KLEE per popped prefix and parses out the terminated paths. Window
    size is a property of the bitcode, so this runner is window-agnostic."""

    def __init__(self, cfg: Config, klee_dir: Path):
        self.cfg = cfg
        self.klee_dir = klee_dir
        self._n = 0

    def run(self, prefix: bytes, iter_time: int) -> list[PathResult]:
        out = self.klee_dir / f"iter-{self._n:06d}"
        self._n += 1
        shutil.rmtree(out, ignore_errors=True)

        cmd = [
            self.cfg.klee, "--libc=uclibc", "--posix-runtime",
            f"--max-memory={self.cfg.klee_memory}",
            f"--max-time={iter_time}s", f"--output-dir={out}",
            "--warnings-only-to-file",
        ]
        if self.cfg.needs_kqueries:
            cmd.append("--write-kqueries")
        cmd += [self.cfg.subject_bc, prefix.hex() if prefix else "-"]

        try:
            subprocess.run(cmd, capture_output=True, timeout=iter_time * 3 + 120)
        except subprocess.TimeoutExpired:
            # KLEE can ignore --max-time near a fork storm; kill by output dir.
            subprocess.run(["pkill", "-9", "-f", str(out)], capture_output=True)

        results: list[PathResult] = []
        for kt in sorted(out.glob("*.ktest")):
            objs = parse_ktest(kt)
            win, ln = objs.get("win"), objs.get("len")
            if win is None or ln is None or not ln:
                continue
            length = min(ln[0], len(win))
            if length < 1:
                continue                # len-0 path is the prefix itself
            pins: dict = {}
            if self.cfg.needs_kqueries:
                kq = kt.with_suffix(".kquery")
                if kq.exists():
                    pins = win_pins(kq.read_text())
            results.append(PathResult(win=bytes(win), length=length, pins=pins))

        if not self.cfg.keep_iters:
            shutil.rmtree(out, ignore_errors=True)
        return results
