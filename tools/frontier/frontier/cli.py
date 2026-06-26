"""cli.py — argument parsing, profile expansion, path resolution, run dispatch.

    frontier --profile run9  --subject luac  --workdir runs/luac  --showmap ...
    frontier --profile run14 --subject tinyc --workdir runs/tinyc
    frontier --profile run9  --subject luac  --window 2   # an ablation

A run is `--profile <name>` plus any `--<knob>` overrides. Paths to the bitcode
and survival harness come from the subject registry by convention; override them
with `--subject-bc` / `--survival-bin` for ad-hoc builds.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, subjects
from .config import (
    PROFILES, Config, Gen, Requeue, Steer, Survival, resolve, validate,
)
from .pipeline import Pipeline


def _enum_choices(enum) -> list[str]:
    return [m.value for m in enum]


def build_parser() -> argparse.ArgumentParser:
    profiles = ", ".join(sorted(PROFILES))
    p = argparse.ArgumentParser(
        prog="frontier",
        description="Parser-directed symbolic execution on KLEE — one pipeline, "
                    "flag-selected per version.",
        epilog=f"profiles: {profiles} (each expands to a knob set; override any "
               "knob on top of a profile)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"frontier {__version__}")
    p.add_argument("--profile", choices=sorted(PROFILES),
                   help="named version to start from")
    p.add_argument("--subject", required=True, help="subject name (see registry)")
    p.add_argument("--workdir", required=True, help="output dir (valid/, run.yaml, …)")

    knobs = p.add_argument_group("knobs (override the profile / default)")
    knobs.add_argument("--window", type=int, help="symbolic window size (must "
                       "match a built bitcode subject_w<N>.bc)")
    knobs.add_argument("--gen", choices=_enum_choices(Gen), help="candidate generation")
    knobs.add_argument("--steer", choices=_enum_choices(Steer), help="queue priority")
    knobs.add_argument("--survival", choices=_enum_choices(Survival), help="oracle precision")
    knobs.add_argument("--requeue", choices=_enum_choices(Requeue), help="accepted-valid policy")
    knobs.add_argument("--survival-budget", type=int,
                       help="no-progress extensions a lineage survives")
    knobs.add_argument("--requeue-budget", type=int, help="budget for gated requeue")
    knobs.add_argument("--root-fair", action="store_true", default=None,
                       help="round-robin first-byte buckets")
    knobs.add_argument("--secondary-coverage", action="store_true", default=None,
                       help="unified: coverage as kwdepth's secondary key")
    knobs.add_argument("--keywords", help="comma-separated report-only keywords")

    budgets = p.add_argument_group("budgets")
    budgets.add_argument("--max-seconds", type=int, help="wall-clock budget")
    budgets.add_argument("--iter-time", type=int, help="KLEE --max-time per byte "
                         "(default: subject registry)")
    budgets.add_argument("--max-len", type=int, help="stop extending past this length")
    budgets.add_argument("--klee-memory", type=int, help="KLEE --max-memory (MB)")
    budgets.add_argument("--keep-iters", action="store_true", default=None,
                         help="keep per-iteration KLEE output dirs")

    paths = p.add_argument_group("paths / tools")
    paths.add_argument("--builds-root", default=subjects.DEFAULT_BUILDS_ROOT,
                       help="root of <subject>/subject_w<N>.bc + harness_survival[_afl]")
    paths.add_argument("--subject-bc", help="override the bitcode path")
    paths.add_argument("--survival-bin", help="override the survival harness path")
    paths.add_argument("--showmap", help="afl-showmap path (coverage/gated/unified)")
    paths.add_argument("--klee", default="klee", help="klee binary")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    subj = subjects.get(args.subject)

    overrides = dict(
        subject=args.subject,
        window=args.window,
        gen=Gen(args.gen) if args.gen else None,
        steer=Steer(args.steer) if args.steer else None,
        survival=Survival(args.survival) if args.survival else None,
        requeue=Requeue(args.requeue) if args.requeue else None,
        survival_budget=args.survival_budget,
        requeue_budget=args.requeue_budget,
        root_fair=args.root_fair,
        secondary_coverage=args.secondary_coverage,
        keywords=(tuple(k for k in args.keywords.split(",") if k)
                  if args.keywords else (subj.keywords or None)),
        max_seconds=args.max_seconds,
        iter_time=args.iter_time if args.iter_time is not None else subj.iter_time,
        max_len=args.max_len,
        klee_memory=args.klee_memory,
        keep_iters=args.keep_iters,
        showmap=args.showmap,
        klee=args.klee,
        workdir=args.workdir,
    )
    try:
        cfg = resolve(args.profile, **overrides)
    except ValueError as e:
        parser.error(str(e))

    # Resolve build artifacts from the registry (unless overridden), now that the
    # window and coverage requirement are known.
    afl = cfg.needs_coverage
    cfg = replace(
        cfg,
        subject_bc=args.subject_bc or str(subjects.bc_path(args.builds_root, subj, cfg.window)),
        survival_bin=args.survival_bin or str(subjects.harness_path(args.builds_root, subj, afl)),
    )
    try:
        validate(cfg)
    except ValueError as e:
        parser.error(str(e))

    missing = [p for p in (cfg.subject_bc, cfg.survival_bin) if not Path(p).exists()]
    if missing:
        return _fail("build artifact(s) not found:\n  " + "\n  ".join(missing)
                     + f"\n  build first, e.g.:  frontier-build {args.subject} {cfg.window}")

    Path(cfg.workdir).mkdir(parents=True, exist_ok=True)
    Pipeline(cfg).run()
    return 0


def _fail(message: str) -> int:
    print(f"frontier: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
