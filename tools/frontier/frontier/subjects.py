"""Subject registry: per-subject build artifacts, budgets, and report keywords.

The old line scattered these facts across build-run{13,15,16}.sh and each run's
run.sh (different ``.local`` layouts per run). Here they are one table, and the
build artifacts follow one convention::

    <builds_root>/<subject>/<bc_stem>_w<window>.bc   # KLEE bitcode
    <builds_root>/<subject>/harness_survival[_afl]   # native survival oracle

``--subject-bc`` / ``--survival-bin`` still override the convention for ad-hoc
runs. Keyword lists are report-only (they flag keyword-bearing valids); they
never steer — steering reads the program's own Eq pins.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_BUILDS_ROOT = "~/repos/klee/.local/frontier-builds"


@dataclass(frozen=True)
class Subject:
    name: str
    keywords: tuple[str, ...] = ()
    iter_time: int = 30          # per-subject KLEE budget per frontier byte (s)
    bc_stem: str = "subject"     # janet: "subject_parse" (parse-only KLEE rescue)


# Per-subject KLEE budgets and bc stems are the run20 values; keyword lists are
# the languages' own reserved words (report-only, overridable via --keywords).
SUBJECTS: dict[str, Subject] = {
    "tinyc": Subject("tinyc", keywords=("do", "else", "if", "while"), iter_time=15),
    "luac": Subject(
        "luac", iter_time=30,
        keywords=("and", "break", "do", "else", "elseif", "end", "false", "for",
                  "function", "goto", "if", "in", "local", "nil", "not", "or",
                  "repeat", "return", "then", "true", "until", "while"),
    ),
    "sqlite": Subject(
        "sqlite", iter_time=120,
        keywords=("SELECT", "FROM", "WHERE", "INSERT", "UPDATE", "DELETE",
                  "CREATE", "TABLE", "DROP", "INDEX", "VALUES"),
    ),
    "wren": Subject(
        "wren", iter_time=60,
        keywords=("var", "if", "else", "for", "while", "class", "construct",
                  "foreign", "import", "return", "break", "continue", "true",
                  "false", "null", "this", "super", "is", "in", "static"),
    ),
    # janet uses the parse-only bitcode (the normal harness hits undefined
    # _setjmp under KLEE); no per-byte keyword space (special forms are interned).
    "janet": Subject("janet", iter_time=120, bc_stem="subject_parse"),
    # data-format parsers: the only "keywords" are the JSON literals.
    "cjson": Subject("cjson", keywords=("true", "false", "null"), iter_time=30),
    "yyjson": Subject("yyjson", keywords=("true", "false", "null"), iter_time=30),
    "inih": Subject("inih", iter_time=30),
    "tomlc99": Subject("tomlc99", iter_time=30),
}


def get(name: str) -> Subject:
    """Registry entry for ``name``, or a bare default (so unknown subjects still
    run with the convention paths and no report keywords)."""
    return SUBJECTS.get(name, Subject(name))


def bc_path(builds_root: str, subject: Subject, window: int) -> Path:
    return Path(builds_root).expanduser() / subject.name / f"{subject.bc_stem}_w{window}.bc"


def harness_path(builds_root: str, subject: Subject, afl: bool) -> Path:
    name = "harness_survival_afl" if afl else "harness_survival"
    return Path(builds_root).expanduser() / subject.name / name
