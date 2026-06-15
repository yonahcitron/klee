# frontier — concrete-prefix / symbolic-frontier input generation

pFuzzer's prefix-growing loop with KLEE's solver replacing taint
guesswork. Motivation, evidence, and pre-registered success criteria:
`.dev/parser-guided-eval/run8_pivot-synthesis/REPORT.md` (private
experiment notes; summary below stands alone).

## Why

Stock KLEE with fixed-size `-sym-stdin N` cannot produce valid inputs
for *length-exact* parsers (those whose end-of-input is "byte N of the
buffer" — e.g. `luaL_loadbuffer(buf, n)`, `yyjson_read(buf, n, 0)`):
the only accepting states sit past ~N reward-free lexer iterations, so
no search heuristic reaches them. Parsers that take a C string escape
via a forkable `byte == '\0'` check; length-exact ones expose no fork
at all. pFuzzer (PLDI'19) sidesteps this by growing a concrete input
byte-by-byte — but pays several instrumented guess-runs per byte and
cannot see through transformed comparisons.

This driver combines the two:

- a queue of **concrete prefixes** (recipes — bytes, not KLEE states);
- per iteration, one short KLEE run where the prefix is concrete
  (hex in argv) and only a small **window + symbolic length** is
  symbolic — the symbolic length restores the "input could end here"
  fork at every offset;
- every terminated path's ktest is a solver-exact continuation;
- candidates are classified by native replay (exit 0 → banked as
  valid) and prioritised by afl-showmap edge novelty + minimality,
  pFuzzer's queue heuristic.

No constraint accumulation across iterations, no state pool, no
fixed-length wall.

## Layout

- `driver.py` — the loop (stdlib-only Python).
- `frontier_common.h` — prefix decode + symbolic window/length.
- `harnesses/<subject>.c` — per-subject harness: stock harness body
  with the stdin read replaced by `frontier_fill()`.
- `build-frontier.sh` — builds `subject.bc` by reusing a stock
  per-subject build recipe with the harness swapped.

## Usage

```bash
./build-frontier.sh <subjects_dir> luac /tmp/fr-luac
python3 driver.py \
  --subject-bc /tmp/fr-luac/subject.bc \
  --native-bin <aflpp-build>/harness \
  --showmap <aflpp>/bin/afl-showmap \
  --klee <klee-build>/bin/klee \
  --workdir /tmp/fr-luac/run \
  --max-seconds 3600
```

Outputs under `--workdir`: `valid/*.bin` (parser-accepted inputs),
`queue/*.bin` (novel candidates), `log.jsonl` (per-candidate and
per-iteration records).

## Knobs

- `--iter-time` (default 30 s): KLEE `--max-time` per iteration; the
  window is explored breadth-complete well within this on most
  subjects, solver-bound ones get truncated and retried later.
- `FRONTIER_WINDOW` (compile-time, default 4): window width; wider =
  more paths per iteration, fewer iterations per byte of progress.
- `--max-len`: stop extending prefixes beyond this (memory/corpus cap).

## Queue strategy (v2)

Default (no flags) is the original best-first behaviour: a single
global heap keyed by `(new edges desc, length asc)`, and a candidate is
re-queued only if it found new edges. run9 showed this collapses into
one basin — 96 % of luac's valid corpus was extensions of a single
`U[-0]=...` root, with zero keyword-gated productions. A seeded-prefix
probe found the cause: keywords *are* reachable and high-value (a
recognised `while` fires 88 edges an identifier doesn't), but the route
crosses partial-identifier prefixes (`w`→`wh`→`whi`→`whil`) that intern
as plain names with ~0 novelty, so the re-push gate deletes them before
they reach the keyword, while the basin (which never hits a zero-novelty
step) monopolises every pop. Two opt-in, co-required flags address it:

- `--survival-budget N` (default 0 = off): a zero-novelty prefix
  survives `N` consecutive extensions at floor priority instead of being
  pruned on the first, so a lineage can cross the valley. Any novel step
  refills the budget.
- `--root-fair` (default off): round-robin over first-byte buckets
  (best-first *within* a bucket) instead of one global heap, so the
  high-novelty basin cannot starve every other root.

Survival keeps the valley alive; root-fair is what actually pops it —
neither works alone. Both off ⇒ identical to v1 (and to run9). Note
these address short keywords (≤ window) and extension to valid
statements; long keywords (≥5 bytes) additionally need a wider
`FRONTIER_WINDOW` to avoid a blind multi-byte valley — a separate lever.
