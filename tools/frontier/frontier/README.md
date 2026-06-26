# frontier — parser-directed symbolic execution on KLEE, as one tool

This package consolidates the scattered frontier-driver line (`driver.py` v1–v6 +
`constraint_driver.py` v14/v18 + the per-run build scripts) into **one pipeline**.
Every published "version" is a point in a small knob space, expressed as a named
profile; a reproduction is one flag, an ablation is one override, and the
resolved config written to `run.yaml` *is* the provenance.

```
frontier --profile run9  --subject luac  --workdir runs/luac  --showmap $AFL/afl-showmap
frontier --profile run14 --subject tinyc --workdir runs/tinyc
frontier --profile run9  --subject luac  --workdir runs/abl --window 2 --showmap …   # ablation
```

## The one loop

pFuzzer's prefix-growing loop with KLEE's solver. Every version runs *this* loop;
only the strategy at four points changes.

```
  ┌─ Schedule ──┐   pop the best concrete prefix (queue: best-first, or root-fair
  │             │   round-robin over first-byte buckets)
  │   Explore   │   one short KLEE run: prefix concrete (hex argv), a small window
  │             │   + its length symbolic → one ktest per terminated path
  │   Generate  │   candidates = prefix + win[:len]  (take-all │ Eq-pinned)
  │   Classify  │   native survival replay → accept(0) │ alive(10) │ dead(11)
  │ Bank/Requeue│   bank accepts; re-queue promising prefixes; drop dead
  └──────△──────┘
         └──────────────── repeat until the time budget ─────────────────
```

## The knobs (what changes between versions)

| stage | knob (flag) | values | role |
|-------|-------------|--------|------|
| Explore | `--window N` | 1, 2, 4, … | symbolic frontier width (compile-time; selects `subject_w<N>.bc`) |
| Generate | `--gen` | `take-all` · `eq-pinned` | every path, vs only Eq-pinned (directed) continuations |
| Schedule | `--steer` | `coverage` · `kwdepth` | afl-showmap edge novelty, vs keyword-prefix match depth |
| Classify | `--survival` | `basic` · `accurate` | exit-0 only, vs 3-way accept/alive/dead |
| Bank | `--requeue` | `none` · `plain` · `gated` | how an accepted valid is re-extended |
| Schedule | `--root-fair`, `--survival-budget`, `--requeue-budget`, `--secondary-coverage` | | queue fairness, valley-crossing budget, unified secondary key |

### Profiles (version → knobs)

| profile | window | gen | steer | survival | requeue | result it owns |
|---------|:------:|-----|-------|----------|---------|----------------|
| **run9**  | 4 | take-all  | coverage | basic    | plain | criterion 1 — length-exact validity |
| **run14** | 1 | eq-pinned | kwdepth  | accurate | none  | criterion 2 — keyword climbing (tinyc class) |
| **run18** | 2 | eq-pinned | kwdepth  | accurate | gated | full modern sweep (controlled requeue) |
| **run15** | 4 | eq-pinned | kwdepth  | accurate | plain (+`--secondary-coverage`) | the unified driver (run9 economics under run14 steering) |

## Modules

| file | responsibility |
|------|----------------|
| `config.py` | `Config`, the knob enums, `PROFILES`, `resolve`, `validate` |
| `models.py` | value types: `Verdict`, `WinByte`, `Candidate`, `Lineage`, `LineageState`, `Stats` |
| `klee.py` | run one KLEE iteration; parse ktests (+ kqueries) into `PathResult`s |
| `kquery.py` | extract positive `Eq` window-byte pins from `.kquery` dumps |
| `generate.py` | `PathResult`s → deduped `Candidate`s (take-all / eq-pinned) |
| `oracle.py` | `SurvivalOracle` (classify) and `Coverage` (afl-showmap novelty) |
| `schedule.py` | the `Queue` and the two `Steering` strategies (coverage / kwdepth) |
| `pipeline.py` | the loop: wires the stages, banks valids, decides requeue |
| `subjects.py` | per-subject registry: artifact paths, budgets, report keywords |
| `report.py` | `run.yaml` provenance, `log.jsonl` events, `metrics.json` + summary |
| `cli.py` | argument parsing, profile expansion, path resolution, dispatch |

## Build & run

`build.sh <subject> <window> [--plain]` builds `subject_w<N>.bc` (any subject,
via the per-subject recipe) and the survival harness (tinyc / luac / yyjson
ported; add a one-line link for others), under `.local/frontier-builds/<subject>/`.

```bash
# criterion 1 — validity (run9 reuses the modern-baseline AFL++ harness as the
# basic oracle; build the bitcode here, point --survival-bin at that harness):
tools/frontier/build.sh luac 4
frontier --profile run9 --subject luac --workdir runs/luac \
         --survival-bin .dev/modern-baseline/.../luac/harness --showmap $AFL/afl-showmap

# criterion 2 — keyword climbing (accurate survival harness, no showmap):
tools/frontier/build.sh tinyc 1
frontier --profile run14 --subject tinyc --workdir runs/tinyc
```

## Honest limitations

- **`--window` is compile-time.** It selects a prebuilt `subject_w<N>.bc`; build
  the windows a profile needs (run9→4, run14→1, run18→2). It is not a runtime flag.
- **The build script ports three subjects' survival harnesses** (tinyc, luac,
  yyjson — the canonical reproductions). Other subjects' *bitcode* builds
  generically; their survival-harness link is a one-liner to add (see `build.sh`).
- **run9's basic oracle reuses the modern-baseline AFL++ harness** (exit 0/1);
  pass it via `--survival-bin --survival basic`. The accurate 3-way oracle is the
  frontier survival harness.
- `is_keyword_bearing` flags are report-only and exact only where the grammar has
  no strings (tinyc); elsewhere confirm a `-kw` valid by hand.

## Supersedes (kept in place for the run records that reference them)

`driver.py` → `--gen take-all --steer coverage --survival basic` (+ `--steer-score`
was v5/v6, now folded into kwdepth). `constraint_driver.py` → `--gen eq-pinned
--steer kwdepth --survival accurate` (+ `--no-requeue`/`--requeue-gated`/`--unified`
→ `--requeue none|gated` / `--secondary-coverage`). `build-run{13,15,16}.sh` →
`build.sh <subject> <window>`.
