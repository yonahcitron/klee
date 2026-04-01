install_build_dependencies_tcmalloc() {
  source "${DIR}/common-functions"

  dependencies=(
    base-devel
    ca-certificates
    cmake
    git
    wget
  )

  #Install essential dependencies
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}"
}
