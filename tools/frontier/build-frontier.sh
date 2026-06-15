#!/usr/bin/env bash
# Build the frontier KLEE bitcode for one subject, reusing the stock
# per-subject build recipes from the modern-baseline pipeline.
#
# Usage: build-frontier.sh <subjects_dir> <subject> <work_dir> [window]
#   subjects_dir  e.g. .dev/modern-baseline/subjects
#   subject       e.g. luac
#   work_dir      output dir; subject.bc lands here
#   window        FRONTIER_WINDOW override (default 4); v4 uses 8 so KLEE can
#                 spell every keyword (function=8) in one symbolic window
#
# Mechanism: copy the subject's recipe dir to a sibling
# "<subjects_dir>-frontier/<subject>/" (so its ../../config/paths.sh
# still resolves), swap in the frontier harness, patch the recipe's
# harness.c copy to also ship frontier_common.h, then run the recipe's
# klee target unchanged.
set -euo pipefail

FRONTIER_DIR="$(cd "$(dirname "$0")" && pwd)"
SUBJECTS_DIR="$(cd "${1:?usage: build-frontier.sh <subjects_dir> <subject> <work_dir>}" && pwd)"
SUBJECT="${2:?need subject}"
WORK="${3:?need work dir}"
WINDOW="${4:-4}"

RECIPE_PARENT="$(dirname "${SUBJECTS_DIR}")/$(basename "${SUBJECTS_DIR}")-frontier"
RECIPE="${RECIPE_PARENT}/${SUBJECT}"

[[ -f "${FRONTIER_DIR}/harnesses/${SUBJECT}.c" ]] || {
  echo "no frontier harness for '${SUBJECT}'" >&2; exit 1; }

mkdir -p "${RECIPE}"
cp "${SUBJECTS_DIR}/${SUBJECT}/build.sh" "${RECIPE}/build.sh"
cp "${FRONTIER_DIR}/harnesses/${SUBJECT}.c" "${RECIPE}/harness.c"
cp "${FRONTIER_DIR}/frontier_common.h" "${RECIPE}/frontier_common.h"

# Override the compile-time window if requested (default header value is 4).
if [[ "${WINDOW}" != "4" ]]; then
  sed -i "s/#define FRONTIER_WINDOW 4/#define FRONTIER_WINDOW ${WINDOW}/" \
    "${RECIPE}/frontier_common.h"
  echo "FRONTIER_WINDOW = ${WINDOW}"
fi

# Ship frontier_common.h alongside harness.c into the build work dir.
sed -i 's|"${SCRIPT_DIR}/harness.c"|"${SCRIPT_DIR}/harness.c" "${SCRIPT_DIR}/frontier_common.h"|' \
  "${RECIPE}/build.sh"

bash "${RECIPE}/build.sh" "${WORK}" klee
[[ -f "${WORK}/subject.bc" ]] || { echo "build produced no subject.bc" >&2; exit 1; }
echo "frontier bitcode: ${WORK}/subject.bc"
