install_binary_artifact_sanitizer() {
  if [[ -z "${SANITIZER_BUILD:-}" ]]; then
    return 1
  fi

  source "${DIR}/common-functions"

  dependencies=(
    llvm
    clang
  )

  #Install essential dependencies
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}" || return 1
}

setup_artifact_variables_sanitizer() {
  if [[ -z "${SANITIZER_BUILD:-}" ]]; then
    return 1
  fi

  # Only set LLVM_CONFIG if not set yet
  if [[ -z "${SANITIZER_LLVM_CONFIG-}" ]]; then
    SANITIZER_LLVM_CONFIG=$(command -v llvm-config)
  fi

  local bin_path=""
  bin_path="$(dirname "$(readlink -f "$(command -v clang)")")"

  SANITIZER_BITCODE_CC="${bin_path}/clang"
  SANITIZER_BITCODE_CXX="${bin_path}/clang++"

  if [[ -z "${SANITIZER_C_COMPILER:-}" ]]; then
    SANITIZER_C_COMPILER="${SANITIZER_BITCODE_CC}"
  fi

  if [[ -z "${SANITIZER_CXX_COMPILER:-}" ]]; then
    SANITIZER_CXX_COMPILER="${SANITIZER_BITCODE_CXX}"
  fi
}

is_installed_sanitizer() {
  if [[ -z "${SANITIZER_BUILD:-}" ]]; then
    return 1
  fi
  command -v clang &>/dev/null
}

get_docker_config_id_sanitizer() {
  return 0
}
