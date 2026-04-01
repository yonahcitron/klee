# Build dependencies
install_build_dependencies_uclibc() {
  source "${DIR}/common-functions"

  dependencies=(
    base-devel
    ca-certificates
    git
    ncurses
    python
    wget
  )

  #Install essential dependencies
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}"
}
