install_build_dependencies_gtest() {
  source "${DIR}/common-functions"

  dependencies=(
    ca-certificates
    wget
    unzip
  )

  #Install essential dependencies
  with_sudo pacman --noconfirm -Sy "${dependencies[@]}"
}
