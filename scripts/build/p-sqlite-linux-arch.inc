install_build_dependencies_sqlite() {
  source "${DIR}/common-functions"

  dependencies=(
    base-devel
    git
    unzip
    wget
  )

  #Install essential dependencies
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}"
}

install_binary_artifact_sqlite() {
  [[ "${SANITIZER_SUFFIX}x" == "x" ]] || return 1

  source "${DIR}/common-functions"

  dependencies=(
    sqlite
  )
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}"
}

# Check if the binary artifact is installed
is_installed_sqlite() {
  [[ -z "${SANITIZER_SUFFIX:-}" ]] || return 1
  [[ -f /usr/lib/libsqlite3.so ]] || return 1
  [[ -f /usr/include/sqlite3.h ]] || return 1
}

# System package — nothing to copy between Docker stages
get_build_artifacts_sqlite() {
  return 0
}
