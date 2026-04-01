# Build dependencies
install_build_dependencies_stp() {
  source "${DIR}/common-functions"

  dependencies=(
    base-devel
    ca-certificates
    cmake
    git
    ncurses
    zlib
  )

  #Install essential dependencies
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}"
}
