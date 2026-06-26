#!/usr/bin/env python3
"""kquery.py — read pFuzzer's comparison signal out of KLEE's own constraints.

run13 (constraint-steering / pFuzzer-via-KLEE) steers generation by the values
the program *compared the frontier byte against* — pFuzzer's directed signal —
instead of by concrete ktest bytes (v1-v4) or a wrapped strcmp proxy (v5/v6).
KLEE already has that signal: every time the parser tests an input byte it
leaves an `Eq(C, Read(i, win))` in the path condition. This module pulls those
pins out of the `.kquery` dumps KLEE writes with `--write-kqueries`.

The window object is named `win` (frontier_common.h: klee_make_symbolic(win,
WINDOW, "win")). A window byte at position i is a *directed* continuation iff
some terminated path positively pins it: `(Eq C N)` where N resolves — through
ZExt / SExt / Extract / let-bindings — to `(Read w8 i win)`. That is the byte
the program compared and *matched* (a keyword char via strcmp, a token char via
the lexer switch). A byte that appears only in a *negated* equality
`(Eq false (Eq C N))` or in range bounds `(Sle 97 N) (Sle N 122)` was not
matched — it is the filler the solver picked to satisfy "some letter != 'e'"
(the `whila`/`whil@` junk pFuzzer never generates). Distinguishing the two at
the source is leak #1's fix (run13 README §1).

Validated against ~47k real tiny.c kqueries (window 8): "do" pins {0:'d',1:'o'},
"da" pins only {0:'d'} (win[1] negated), "wa" pins only {0:'w'} — see __main__.

Limitation: handles direct byte reads wrapped in ZExt/SExt/Extract (the shape
tiny.c / luac lexers produce). Multi-byte word compares that reach the solver
as Concat(Read,Read,...) (yyjson byte_match_4) are out of scope — that lexer
class is not a run13 subject.
"""

import re
import sys
from pathlib import Path


_BRACKET = re.compile(r"([()\[\]])")
_LABEL = re.compile(r"^N\d+:$")
_INT = re.compile(r"^-?\d+$")


def _tokens(text):
    """Tokenise a kquery body. Array-decl lines are dropped (they carry the
    only `:` / `->` / `[n]` that are not constraint syntax); `[` `]` are
    normalised to `(` `)` since we only need the expression tree."""
    body = "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("array ")
    )
    body = body.replace("[", "(").replace("]", ")")
    return _BRACKET.sub(r" \1 ", body).split()


def _parse(tokens):
    """Recursive-descent into nested lists. A `Nk:` label binds the node that
    follows it; bare `Nk` stays an atom (a reference resolved on demand). Lists
    are python lists of nodes; atoms are strings. Returns (nodes, binds)."""
    binds = {}
    pos = 0

    def node():
        nonlocal pos
        tok = tokens[pos]
        if _LABEL.match(tok):           # "N2:" labels the next node
            pos += 1
            inner = node()
            binds[tok[:-1]] = inner
            return inner
        if tok == "(":
            pos += 1
            lst = []
            while tokens[pos] != ")":
                lst.append(node())
            pos += 1                    # consume ")"
            return lst
        pos += 1
        return tok                      # atom (operator, width, int, ref, ...)

    out = []
    while pos < len(tokens):
        out.append(node())
    return out, binds


def _win_index(expr, binds, seen=()):
    """If expr ultimately reads one byte of `win`, return its index, else None.
    Sees through let-references and the ZExt/SExt/Extract wrappers KLEE puts
    around a single-byte read."""
    if isinstance(expr, str):
        if expr in binds and expr not in seen:
            return _win_index(binds[expr], binds, seen + (expr,))
        return None
    if isinstance(expr, list) and expr:
        head = expr[0]
        if head == "Read" and len(expr) >= 4 and expr[3] == "win":
            return int(expr[2]) if _INT.match(expr[2]) else None
        if head in ("ZExt", "SExt") and len(expr) >= 3:
            return _win_index(expr[2], binds, seen)
        if head == "Extract" and len(expr) >= 4:        # (Extract w8 0 X)
            return _win_index(expr[3], binds, seen)
    return None


def _query_constraints(nodes):
    """The constraint list inside `(query [ c1 c2 ... ] goal)`."""
    for n in nodes:
        if isinstance(n, list) and n and n[0] == "query" and len(n) >= 2:
            return n[1] if isinstance(n[1], list) else []
    return []


def win_pins(text):
    """{win_index: byte_value} for every window byte a path positively pins via
    equality. Negated equalities and range bounds are deliberately excluded."""
    nodes, binds = _parse(_tokens(text))
    pins = {}
    for c in _query_constraints(nodes):
        # A top-level constraint that is itself `(Eq false ...)` is a negation
        # (e.g. the win!=0 assume, or "byte != 'o'"); never a positive pin.
        if not (isinstance(c, list) and len(c) == 3 and c[0] == "Eq"):
            continue
        a, b = c[1], c[2]
        if a == "false" or b == "false":
            continue
        for const, other in ((a, b), (b, a)):
            if isinstance(const, str) and _INT.match(const):
                idx = _win_index(other, binds)
                if idx is not None:
                    pins[idx] = int(const) & 0xFF
                    break
    return pins


def win_pins_file(path):
    try:
        return win_pins(Path(path).read_text())
    except OSError:
        return {}


# --- self-test / validation harness -------------------------------------
def _selftest(probe_dir):
    """Validate against a directory of real (ktest, kquery) pairs: confirm that
    keyword window bytes are pinned and filler bytes are not."""
    import struct

    def parse_ktest(p):
        objs = {}
        with open(p, "rb") as f:
            if f.read(5) not in (b"KTEST", b"BOUT\n"):
                return objs
            u32 = lambda: struct.unpack(">I", f.read(4))[0]
            ver = u32()
            for _ in range(u32()):
                f.read(u32())
            if ver >= 2:
                u32(); u32()
            for _ in range(u32()):
                name = f.read(u32()).decode("latin1")
                objs[name] = f.read(u32())
        return objs

    probe = Path(probe_dir)
    kqs = sorted(probe.glob("*.kquery"))
    print(f"validating {len(kqs)} kqueries in {probe} ...")
    n_pinned = n_input = 0
    examples = {}
    for kq in kqs:
        kt = kq.with_suffix(".ktest")
        if not kt.exists():
            continue
        objs = parse_ktest(kt)
        win, ln = objs.get("win"), objs.get("len")
        if win is None or ln is None or not ln:
            continue
        n = min(ln[0], len(win))
        inp = win[:n]
        pins = win_pins_file(kq)
        if n:
            n_input += 1
        if pins:
            n_pinned += 1
        # every pin must match the realised ktest byte (constraints are sat)
        for i, v in pins.items():
            if i < len(win):
                assert win[i] == v, f"{kq.name}: pin win[{i}]={v} != ktest {win[i]}"
        # collect a few labelled examples
        if 1 <= len(inp) <= 3 and all(0x20 <= c < 0x7F for c in inp):
            key = bytes(inp).decode("latin1")
            if key not in examples:
                # matched run = leading window bytes that are pinned
                run = 0
                while run in pins and run < n:
                    run += 1
                examples[key] = (pins, run)
    print(f"  {n_input} paths with input, {n_pinned} carry >=1 positive pin")
    print("  examples (input -> pins, directed-prefix-len):")
    for k in sorted(examples):
        pins, run = examples[k]
        tag = ""
        if k in ("do", "if", "el", "wh", "in", "or"):
            tag = "  <- keyword prefix (expect fully pinned)"
        print(f"    {k!r:8} pins={ {i: chr(v) for i, v in pins.items()} } "
              f"directed_len={run}{tag}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        sys.exit(_selftest(sys.argv[2]))
    if len(sys.argv) == 2:
        print(win_pins_file(sys.argv[1]))
    else:
        print("usage: kquery.py <file.kquery> | --selftest <dir>", file=sys.stderr)
        sys.exit(2)
