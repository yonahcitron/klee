# Import base LLVM build functions (setup_build_variables_llvm, etc.)
source "${DIR}/p-llvm.inc"

# Build dependencies
install_build_dependencies_llvm() {
  source "${DIR}/common-functions"

  dependencies=(
    base-devel
    ca-certificates
    git
    ninja
    python
    python-setuptools # provides distutils (removed in Python 3.12+)
    wget
    zlib
  )

  #Install essential dependencies
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}"

  # LLVM 13–16 headers use a CRTP pattern in SmallVector.h that is
  # rejected by GCC 13+ and Clang 16+ (P1787R6 strict two-phase
  # name lookup). Build with GCC 11 to work around this.
  setup_gcc11
}

install_binary_artifact_llvm() {
  # No need to check for optimised, we can build against LLVM with optimised and non-optimised versions
  #  local enable_optimized=$(to_bool "${ENABLE_OPTIMIZED}")
  local enable_debug
  enable_debug=$(to_bool "${ENABLE_DEBUG}")
  local disable_assertions
  disable_assertions=$(to_bool "${DISABLE_ASSERTIONS}")
  local requires_rtti
  requires_rtti=$(to_bool "${REQUIRES_RTTI}")
  local LLVM_VERSION_MAJOR="${LLVM_VERSION/.*/}"
  local version="${LLVM_VERSION_MAJOR}"

  # No support for LLVM packages with debug information, incompatible if requested otherwise
  [[ "${enable_debug}" -eq 1 ]] && return 1

  # Packages are build with assertions disabled, incompatible if requested otherwise
  [[ "${disable_assertions}" -eq 0 ]] && return 1

  # Packages are build with RTTI enabled, incompatible if requested otherwise
  [[ "${requires_rtti}" -eq 0 ]] && return 1

  # Enable/Disable optimized does not matter
  source "${DIR}/common-functions"

  dependencies=(
    llvm
    clang
  )

  # Install essential dependencies
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}" || return 1

  # Arch provides only the latest LLVM; verify it matches the requested version
  local lc
  lc=$(command -v llvm-config) || return 1
  check_llvm_config_version 1 "${lc}" || return 1
}

check_llvm_config_version() {
    local check_mode=1
    strict_mode="$1" # if llvm-config should be checked strictly
    local lc=""
    lc="$2" # path to llvm-config

    # If not set return error
    [[ -n "${lc}" ]] || return 1

    # First check, if the provided llvm-config is a full path
    if [[ ! -f "${lc}" ]]; then
      # Nothing found, assume it's just the name of the binary in path, find the path
      lc=$(command -v "${lc}")

      # If path not found return error
      [[ -z "${lc}" ]] && return 1
    fi

    local LLVM_VERSION_MAJOR="${LLVM_VERSION/.*/}"
    local version="${LLVM_VERSION_MAJOR}"

    # Check for llvm-config without suffix but correct version number
    [[ $($lc --version) == "${LLVM_VERSION}"* ]] || return 1

    # In case correct version numbers are required, return already
    [[ "${check_mode}" == "0" ]] && return 0;

    local rtti
    rtti="$(${lc} --has-rtti)"
    local assertion
    assertion="$(${lc} --assertion-mode)"
    local build_mode
    build_mode="$(${lc} --build-mode)"

    # Check requested mode with mode of the found item
    [[ $(to_bool "${REQUIRES_RTTI}") -eq $(to_bool "${rtti}") ]] || return 1
    [[ $(to_bool "${DISABLE_ASSERTIONS}") -ne $(to_bool "${assertion}") ]] || return 1

    local shared_mode
    shared_mode="$(${lc} --shared-mode)" || return 1
}

# Check if the binary artifact is installed
is_installed_llvm() {
  # First check if LLVM was built from source (computed install path)
  (
    setup_build_variables_llvm
    [[ -f "${LLVM_INSTALL}/bin/llvm-config" ]]
    [[ -f "${LLVM_INSTALL}/.install_finished" ]]
  ) && return 0

  # Fall back to system LLVM (Arch provides llvm-config without version suffix)
  local lc
  lc=$(command -v llvm-config 2>/dev/null) || return 1
  check_llvm_config_version 1 "${lc}" && return 0

  return 1
}

setup_artifact_variables_llvm() {
    # First check if LLVM was built from source
    setup_build_variables_llvm
    if [[ -f "${LLVM_INSTALL}/bin/llvm-config" ]]; then
        LLVM_CONFIG="${LLVM_INSTALL}/bin/llvm-config"
        LLVM_BIN="${LLVM_INSTALL}/bin"
        BITCODE_CC="${LLVM_BIN}/clang"
        BITCODE_CXX="${LLVM_BIN}/clang++"
        return 0
    fi

    # Fall back to system LLVM (Arch provides llvm-config without version suffix)
    local lc=""
    lc=$(command -v "llvm-config") || return 1
    check_llvm_config_version 1 "${lc}" || return 1

    LLVM_CONFIG="${lc}"
    LLVM_INSTALL="$(${lc} --prefix)"
    LLVM_BIN="$(${lc} --bindir)"
    BITCODE_CC="${LLVM_BIN}/clang"
    BITCODE_CXX="${LLVM_BIN}/clang++"
}

get_build_artifacts_llvm() {
  (
    setup_build_variables_llvm
    echo "${LLVM_INSTALL}"
    # Include GCC 11 install for downstream Docker stages that need it
    echo "${BASE}/gcc-11-install"
  )
}
