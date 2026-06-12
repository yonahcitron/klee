install_build_dependencies_libcxx() {
  source "${DIR}/common-functions"

  dependencies=(
    base-devel
    ca-certificates
    curl
    git
    python
    python-setuptools # provides distutils (removed in Python 3.12+)
  )

  with_sudo pacman -Syu --needed --noconfirm "${dependencies[@]}"

  # Create venv and bootstrap pip inside it (avoids system pip conflicts)
  /usr/bin/python3 -m venv --clear "${BASE}/wllvm-venv"
  "${BASE}/wllvm-venv/bin/python" -m pip install --quiet wllvm
  export PATH="${BASE}/wllvm-venv/bin:$PATH"
}
