#!/usr/bin/env bash
# build.sh — build a frontier subject for the unified driver.
#
#   build.sh <subject> [window] [--plain]
#
# Produces, under <builds_root>/<subject>/ (default .local/frontier-builds):
#   subject_w<window>.bc    window-N KLEE bitcode: concrete prefix (hex argv) +
#                           symbolic window + symbolic length. Built via
#                           build-frontier.sh, which reuses each subject's own
#                           modern-baseline recipe (so any subject's bitcode
#                           builds here, not just the three with a survival link
#                           below).
#   harness_survival_afl    AFL-instrumented 3-way survival oracle (default):
#                           exit 0/10/11 = accept/alive/dead, and afl-showmap
#                           works on it (coverage steering / gated requeue).
#   harness_survival        plain survival oracle (--plain): same exit codes,
#                           no coverage instrumentation.
#
# This supersedes build-run{13,15,16}.sh: one entry point, window is an argument
# rather than a per-run script. Subjects with a ported survival-harness recipe:
# tinyc, luac, yyjson (the canonical-reproduction subjects). For another subject
# the bitcode still builds; add its one-line survival link in the case below
# (follow luac). run9's *basic* oracle instead reuses the modern-baseline AFL++
# harness — pass it with `--survival-bin … --survival basic`; it is not built here.
set -euo pipefail

FRONTIER_DIR="$(cd "$(dirname "$0")" && pwd)"
KLEE_ROOT="$(cd "${FRONTIER_DIR}/../.." && pwd)"
DEPS="${KLEE_ROOT}/.local/klee-deps"
CLANG="${DEPS}/llvm-16-install/bin/clang"
AFLCC="${KLEE_ROOT}/.dev/modern-baseline/programs/aflpp/bin/afl-clang-fast"
SUBJECTS_DIR="${KLEE_ROOT}/.dev/modern-baseline/subjects"
TINYC_SRC="${KLEE_ROOT}/.local/frontier-builds-tinyc"   # machine-local tiny.c sources
OUT_ROOT="${FRONTIER_BUILDS_ROOT:-${KLEE_ROOT}/.local/frontier-builds}"

SUBJECT="${1:?usage: build.sh <subject> [window] [--plain]}"
WINDOW="${2:-4}"
PLAIN=0
[[ "${3:-}" == "--plain" ]] && PLAIN=1
[[ "${WINDOW}" =~ ^[0-9]+$ ]] || { echo "window must be an integer" >&2; exit 2; }

export PATH="${DEPS}/llvm-16-install/bin:$(dirname "${AFLCC}"):${PATH}"
export AFL_QUIET=1 AFL_SKIP_CPUFREQ=1

OUT="${OUT_ROOT}/${SUBJECT}"
mkdir -p "${OUT}"
if [[ ${PLAIN} -eq 1 ]]; then HARNESS="harness_survival"; CC="${CLANG}"; else HARNESS="harness_survival_afl"; CC="${AFLCC}"; fi

LUA_CORE=(lapi.c lcode.c lctype.c ldebug.c ldo.c ldump.c lfunc.c lgc.c llex.c
          lmem.c lobject.c lopcodes.c lparser.c lstate.c lstring.c ltable.c
          ltm.c lundump.c lvm.c lzio.c lauxlib.c)

# --- bitcode: window-N, generic across subjects -------------------------------
build_bc() {
  local bc="${OUT}/subject_w${WINDOW}.bc"
  if [[ "${SUBJECT}" == tinyc ]]; then
    [[ -f "${TINYC_SRC}/tinyc_fr.c" ]] || { echo "missing ${TINYC_SRC}/tinyc_fr.c" >&2; exit 1; }
    cp "${TINYC_SRC}"/{tiny.c,tinyc_fr.c,frontier_common.h} "${OUT}/" 2>/dev/null || true
    ( cd "${OUT}" && "${CLANG}" -emit-llvm -c -g -O0 -DFRONTIER_WINDOW="${WINDOW}" -I. tinyc_fr.c -o "subject_w${WINDOW}.bc" )
  else
    local tmp; tmp="$(mktemp -d)"
    "${FRONTIER_DIR}/build-frontier.sh" "${SUBJECTS_DIR}" "${SUBJECT}" "${tmp}" "${WINDOW}"
    cp "${tmp}/subject.bc" "${bc}"
    rm -rf "${tmp}"
  fi
  echo "[${SUBJECT}] bitcode -> ${bc}"
}

# --- survival harness: subject-specific link ----------------------------------
build_harness() {
  cp "${FRONTIER_DIR}/harnesses/${SUBJECT}_survival"*.c "${OUT}/" 2>/dev/null || true
  case "${SUBJECT}" in
    tinyc)
      cp "${FRONTIER_DIR}/harnesses/tinyc_survival2.c" "${OUT}/"
      ( cd "${OUT}" && "${CC}" -O0 -w -o "${HARNESS}" tinyc_survival2.c ) ;;
    luac)
      ( cd "${OUT}" && "${CC}" -O2 -DLUA_USE_POSIX -w -o "${HARNESS}" luac_survival.c "${LUA_CORE[@]}" -lm ) ;;
    yyjson)
      ( cd "${OUT}" && "${CC}" -O2 -w -o "${HARNESS}" yyjson_survival.c yyjson.c -lm ) ;;
    *)
      echo "[${SUBJECT}] no survival-harness recipe yet — bitcode is built; add a" >&2
      echo "  case here (link ${SUBJECT}_survival.c with the subject's core .c, see luac)," >&2
      echo "  or run with --survival-bin pointing at an existing harness." >&2
      return 0 ;;
  esac
  echo "[${SUBJECT}] ${HARNESS} -> ${OUT}/${HARNESS}"
}

build_bc
build_harness
echo "done: ${OUT}"
