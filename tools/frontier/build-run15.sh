#!/usr/bin/env bash
# Build run15 (unified driver) comparison subjects into one tree:
#   .local/frontier-builds-run15/<subject>/{subject*.bc, harness_survival_afl}
# Each subject gets a window-1 KLEE bitcode (criterion-1 symbolic length +
# criterion-2 one-byte frontier) and an AFL-instrumented survival oracle
# (afl-clang-fast → afl-showmap works, giving run15's coverage-novelty signal;
# the exit code 0/10/11 = accept/alive/dead serves both run14 and run15).
#
# Subjects: tinyc (criterion-2 keyword class), luac (criterion-1 length-exact,
# criterion-2 hash-blocked), yyjson (criterion-1 length-exact, criterion-2
# word-compare-blocked). [wren — the criterion-2 follow-up — is deferred: it
# executes input (infinite-loop hazard) and its sources aren't local.]
#
# Usage: build-run15.sh [tinyc|luac|yyjson|all]
set -euo pipefail

FRONTIER_DIR="$(cd "$(dirname "$0")" && pwd)"
KLEE_ROOT="$(cd "${FRONTIER_DIR}/../.." && pwd)"
DEPS="${KLEE_ROOT}/.local/klee-deps"
CLANG="${DEPS}/llvm-16-install/bin/clang"
AFLCC="${KLEE_ROOT}/.dev/modern-baseline/programs/aflpp/bin/afl-clang-fast"
SUBJECTS_DIR="${KLEE_ROOT}/.dev/modern-baseline/subjects"
TINYC_SRC="${KLEE_ROOT}/.local/frontier-builds-tinyc"
OUT="${KLEE_ROOT}/.local/frontier-builds-run15"

export PATH="${DEPS}/llvm-16-install/bin:$(dirname "${AFLCC}"):${PATH}"
export AFL_QUIET=1 AFL_SKIP_CPUFREQ=1

LUA_CORE=(lapi.c lcode.c lctype.c ldebug.c ldo.c ldump.c lfunc.c lgc.c llex.c
          lmem.c lobject.c lopcodes.c lparser.c lstate.c lstring.c ltable.c
          ltm.c lundump.c lvm.c lzio.c lauxlib.c)

build_tinyc() {
  local d="${OUT}/tinyc"
  if [[ -f "${d}/subject_w1.bc" && -x "${d}/harness_survival_afl" ]]; then
    echo "[tinyc] present"; return; fi
  [[ -f "${TINYC_SRC}/tiny.c" ]] || { echo "[tinyc] missing ${TINYC_SRC}/tiny.c" >&2; exit 1; }
  mkdir -p "${d}"
  cp "${TINYC_SRC}"/{tiny.c,tinyc_fr.c,frontier_common.h} "${d}/"
  cp "${FRONTIER_DIR}/harnesses/tinyc_survival2.c" "${d}/"
  echo "[tinyc] window-1 bitcode + AFL survival oracle"
  ( cd "${d}"
    "${CLANG}" -emit-llvm -c -g -O0 -DFRONTIER_WINDOW=1 -I. tinyc_fr.c -o subject_w1.bc
    "${AFLCC}" -O0 -w -o harness_survival_afl tinyc_survival2.c )
}

build_luac() {
  local d="${OUT}/luac"
  if [[ -f "${d}/subject.bc" && -x "${d}/harness_survival_afl" ]]; then
    echo "[luac] present"; return; fi
  echo "[luac] window-1 bitcode (build-frontier.sh) + AFL survival oracle"
  "${FRONTIER_DIR}/build-frontier.sh" "${SUBJECTS_DIR}" luac "${d}" 1
  cp "${FRONTIER_DIR}/harnesses/luac_survival.c" "${d}/"
  ( cd "${d}"
    "${AFLCC}" -O2 -DLUA_USE_POSIX -w -o harness_survival_afl luac_survival.c "${LUA_CORE[@]}" -lm )
}

build_yyjson() {
  local d="${OUT}/yyjson"
  if [[ -f "${d}/subject.bc" && -x "${d}/harness_survival_afl" ]]; then
    echo "[yyjson] present"; return; fi
  echo "[yyjson] window-1 bitcode (build-frontier.sh) + AFL survival oracle"
  "${FRONTIER_DIR}/build-frontier.sh" "${SUBJECTS_DIR}" yyjson "${d}" 1
  cp "${FRONTIER_DIR}/harnesses/yyjson_survival.c" "${d}/"
  ( cd "${d}"
    "${AFLCC}" -O2 -w -o harness_survival_afl yyjson_survival.c yyjson.c -lm )
}

case "${1:-all}" in
  tinyc)  build_tinyc ;;
  luac)   build_luac ;;
  yyjson) build_yyjson ;;
  all)    build_tinyc; build_luac; build_yyjson ;;
  *) echo "usage: build-run15.sh [tinyc|luac|yyjson|all]" >&2; exit 2 ;;
esac
echo "run15 build done."
