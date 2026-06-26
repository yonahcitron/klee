#!/usr/bin/env bash
# Build run13 (constraint-steering) subjects: a window-1 KLEE bitcode (one
# symbolic frontier byte, pFuzzer's grain) + a native survival harness
# (exit 0=accept / 10=alive / 11=dead) per subject.
#
#   tinyc — favourable char-comparison lexer (PRIMARY). subject_w1.bc from
#           tinyc_fr.c -DFRONTIER_WINDOW=1; harness_survival from tinyc_survival.c.
#   luac  — hash-intern boundary. Built from the modern-baseline luac recipe via
#           build-frontier.sh (window 1); survival harness links the lua core.
#
# Usage: build-run13.sh [tinyc|luac|all]
set -euo pipefail

FRONTIER_DIR="$(cd "$(dirname "$0")" && pwd)"
KLEE_ROOT="$(cd "${FRONTIER_DIR}/../.." && pwd)"
DEPS="${KLEE_ROOT}/.local/klee-deps"
CLANG="${DEPS}/llvm-16-install/bin/clang"
OUT="${KLEE_ROOT}/.local/frontier-builds-run13"
SUBJECTS_DIR="${KLEE_ROOT}/.dev/modern-baseline/subjects"
TINYC_DIR="${KLEE_ROOT}/.local/frontier-builds-tinyc"

export PATH="${DEPS}/llvm-16-install/bin:${PATH}"

LUA_CORE=(lapi.c lcode.c lctype.c ldebug.c ldo.c ldump.c lfunc.c lgc.c llex.c
          lmem.c lobject.c lopcodes.c lparser.c lstate.c lstring.c ltable.c
          ltm.c lundump.c lvm.c lzio.c lauxlib.c)

build_tinyc() {
  if [[ -f "${TINYC_DIR}/subject_w1.bc" && -x "${TINYC_DIR}/harness_survival" \
        && -x "${TINYC_DIR}/harness_survival2" ]]; then
    echo "[tinyc] artifacts present"; return
  fi
  # tiny.c + tinyc_fr.c are the machine-local subject sources under
  # ${TINYC_DIR} (tiny.c is Marc Feeley's tiny-C, "All Rights Reserved" — kept
  # in .local, not committed, per the v5/v6 convention). The survival harnesses
  # ARE tracked (tools/frontier/harnesses/); copy them in.
  if [[ ! -f "${TINYC_DIR}/tiny.c" || ! -f "${TINYC_DIR}/tinyc_fr.c" ]]; then
    echo "[tinyc] missing machine-local subject sources in ${TINYC_DIR}" >&2
    echo "        (need tiny.c + tinyc_fr.c — see run13 DESIGN.md)" >&2
    exit 1
  fi
  cp "${FRONTIER_DIR}/harnesses/tinyc_survival.c" \
     "${FRONTIER_DIR}/harnesses/tinyc_survival2.c" "${TINYC_DIR}/"
  echo "[tinyc] window-1 bitcode + survival harnesses (v1 read-past-end, v2 accurate)"
  ( cd "${TINYC_DIR}"
    "${CLANG}" -emit-llvm -c -g -O0 -DFRONTIER_WINDOW=1 -I. tinyc_fr.c -o subject_w1.bc
    gcc -O0 -w -o harness_survival  tinyc_survival.c
    gcc -O0 -w -o harness_survival2 tinyc_survival2.c )
}

build_luac() {
  local d="${OUT}/luac"
  if [[ -f "${d}/subject.bc" && -x "${d}/harness_survival" ]]; then
    echo "[luac] artifacts present"; return
  fi
  echo "[luac] window-1 bitcode via build-frontier.sh"
  mkdir -p "${d}"
  "${FRONTIER_DIR}/build-frontier.sh" "${SUBJECTS_DIR}" luac "${d}" 1
  echo "[luac] survival harness (lua core, native)"
  cp "${FRONTIER_DIR}/harnesses/luac_survival.c" "${d}/"
  ( cd "${d}"
    "${CLANG}" -O2 -DLUA_USE_POSIX -Wno-everything luac_survival.c "${LUA_CORE[@]}" \
      -o harness_survival -lm )
}

case "${1:-all}" in
  tinyc) build_tinyc ;;
  luac)  build_luac ;;
  all)   build_tinyc; build_luac ;;
  *) echo "usage: build-run13.sh [tinyc|luac|all]" >&2; exit 2 ;;
esac
echo "run13 build done."
