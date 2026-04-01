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

  with_sudo pacman --noconfirm -Sy "${dependencies[@]}"

  # LLVM 13-14's merge_archives.py imports distutils.spawn, which was
  # removed in Python 3.12+.  Create a minimal shim so the import works.
  # We check the stdlib directory rather than trying "import distutils"
  # because python-setuptools provides distutils via an import hook that
  # only works when site-packages are loaded (not inside venvs without
  # --system-site-packages).
  local py_ver
  py_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  local distutils_dir="/usr/lib/python${py_ver}/distutils"
  if [[ ! -f "${distutils_dir}/__init__.py" ]]; then
    with_sudo mkdir -p "${distutils_dir}"
    echo '' | with_sudo tee "${distutils_dir}/__init__.py" >/dev/null
    echo 'from shutil import which as find_executable' | with_sudo tee "${distutils_dir}/spawn.py" >/dev/null
  fi

  # Create venv and bootstrap pip inside it (avoids system pip conflicts)
  /usr/bin/python3 -m venv --clear "${BASE}/wllvm-venv"
  "${BASE}/wllvm-venv/bin/python" -m pip install --quiet wllvm
  export PATH="${BASE}/wllvm-venv/bin:$PATH"
}
