install_binary_artifact_clang() {
  source "${DIR}/common-functions"

  dependencies=(
    llvm
    clang
  )

  #Install essential dependencies
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}" || return 1
}

setup_artifact_variables_clang() {
  # Only set LLVM_CONFIG if not set yet
  if [[ -z "${LLVM_CONFIG:-}" ]]; then
    LLVM_CONFIG=$(command -v llvm-config)
  fi

  local bin_path=""
  bin_path=$(command -v clang)
  [[ -z "${bin_path}" ]] && return 1

  bin_path="$(dirname "$(readlink -f "${bin_path}")")"
  [[ -z "${bin_path}" ]] && return 1

  BITCODE_CC="${bin_path}/clang"
  BITCODE_CXX="${bin_path}/clang++"
}

is_installed_clang() {
  command -v clang &>/dev/null
}
