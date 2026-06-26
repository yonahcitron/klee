#!/usr/bin/env bash
# Build run16 (window-isolation sweep) bitcode:
#   v16 = unified driver @ window-2  -> subject_w2.bc per subject
#   v9  = run9's driver.py @ window-4 (faithful rerun) -> subject_w4.bc per subject
# (v15 was window-1; this sweeps the window variable: 1 / 2 / 4.)
# Survival/coverage harnesses are reused from run15 (afl-instrumented,
# exit 0/10/11; driver.py reads only exit 0 = valid, constraint_driver uses all
# three; afl-showmap gives coverage for both). So only the bitcode is new here.
#
# Usage: build-run16.sh [tinyc|luac|yyjson|all]
set -euo pipefail

FRONTIER_DIR="$(cd "$(dirname "$0")" && pwd)"
KLEE_ROOT="$(cd "${FRONTIER_DIR}/../.." && pwd)"
DEPS="${KLEE_ROOT}/.local/klee-deps"
CLANG="${DEPS}/llvm-16-install/bin/clang"
SUBJECTS_DIR="${KLEE_ROOT}/.dev/modern-baseline/subjects"
TINYC_SRC="${KLEE_ROOT}/.local/frontier-builds-tinyc"
R15="${KLEE_ROOT}/.local/frontier-builds-run15"
OUT="${KLEE_ROOT}/.local/frontier-builds-run16"
export PATH="${DEPS}/llvm-16-install/bin:${PATH}"

build_bc() {                    # subject, window
  local s="$1" w="$2" d="${OUT}/${s}"
  mkdir -p "${d}"
  if [[ -f "${d}/subject_w${w}.bc" ]]; then echo "[${s} w${w}] present"; return; fi
  echo "[${s} w${w}] building window-${w} bitcode"
  if [[ "${s}" == tinyc ]]; then
    cp "${TINYC_SRC}"/{tiny.c,tinyc_fr.c,frontier_common.h} "${d}/"
    ( cd "${d}"; "${CLANG}" -emit-llvm -c -g -O0 -DFRONTIER_WINDOW="${w}" -I. \
        tinyc_fr.c -o "subject_w${w}.bc" )
  else
    local tmp="${d}/build_w${w}"
    "${FRONTIER_DIR}/build-frontier.sh" "${SUBJECTS_DIR}" "${s}" "${tmp}" "${w}"
    cp "${tmp}/subject.bc" "${d}/subject_w${w}.bc"
  fi
}

do_subject() {
  local s="$1"
  build_bc "${s}" 2     # v16 (unified @ window-2)
  build_bc "${s}" 4     # v9  (run9 driver.py @ window-4)
  if [[ ! -x "${R15}/${s}/harness_survival_afl" ]]; then
    echo "[${s}] missing run15 afl survival harness — run build-run15.sh ${s}" >&2
    exit 1
  fi
}

case "${1:-all}" in
  tinyc)  do_subject tinyc ;;
  luac)   do_subject luac ;;
  yyjson) do_subject yyjson ;;
  all)    do_subject tinyc; do_subject luac; do_subject yyjson ;;
  *) echo "usage: build-run16.sh [tinyc|luac|yyjson|all]" >&2; exit 2 ;;
esac
echo "run16 build done."
