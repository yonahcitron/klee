# Build dependencies Z3
install_build_dependencies_z3() {
  source "${DIR}/common-functions"

  dependencies=(
    base-devel
    ca-certificates
    git
    python
    python-setuptools # provides distutils (removed in Python 3.12+)
    wget
  )

  #Install essential dependencies
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}"
}
