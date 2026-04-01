install_build_dependencies_klee() {
  source "${DIR}/common-functions"

  # Install KLEE's dependencies
  dependencies=(
    base-devel
    lib32-glibc # 32-bit support (requires multilib repo in /etc/pacman.conf)
    ncurses
    python
    wget
    zlib
  )

  if [[ "${SOLVERS:-}" == "metaSMT" ]]; then
    dependencies+=(boost gmp)
  fi
  if [[ $(to_bool "${COVERAGE}") -eq 1 ]]; then
    dependencies+=(lcov curl git)
  fi
  if [[ $(to_bool "${ENABLE_DOXYGEN}") -eq 1 ]]; then
    dependencies+=(doxygen graphviz)
  fi

  with_sudo pacman --noconfirm -Sy "${dependencies[@]}"

  # LLVM 13–16 headers use a CRTP pattern in SmallVector.h that is
  # rejected by GCC 13+ and Clang 16+ (P1787R6 strict two-phase
  # name lookup). Build with GCC 11 to work around this.
  setup_gcc11

  # Create venv and bootstrap pip inside it (avoids system pip conflicts)
  /usr/bin/python3 -m venv --clear "${BASE}/klee-tools-venv"
  "${BASE}/klee-tools-venv/bin/python" -m pip install --quiet lit tabulate
  export PATH="${BASE}/klee-tools-venv/bin:$PATH"
}
