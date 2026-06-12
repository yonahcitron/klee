# Arch-specific helper: ensure GCC 11 is available as CC/CXX.
#
# LLVM 15/16 headers use CRTP patterns that trigger P1787R6 strict
# two-phase name lookup errors with GCC 13+ and Clang 16+. Arch is a
# rolling release and ships only the current GCC (no gcc11/gcc12
# compatibility packages), so build GCC 11.5.0 from source once and
# cache it in $BASE/gcc-11-install. Not needed for LLVM 17+, whose
# headers compile fine with current toolchains.
#
# Must be sourced after common-functions and called after $BASE is set.

GCC11_VERSION="11.5.0"
# From https://gcc.gnu.org/pub/gcc/releases/gcc-11.5.0/sha512.sum
GCC11_TARBALL_SHA512="88f17d5a5e69eeb53aaf0a9bc9daab1c4e501d145b388c5485ebeb2cc36178fbb2d3e49ebef4a8c007a05e88471a06b97cf9b08870478249f77fbfa3d4abd9a8"

setup_gcc11() {
  local gcc_ver
  gcc_ver=$(gcc -dumpversion 2>/dev/null || echo "0")
  local gcc_major="${gcc_ver%%.*}"

  if [[ "${gcc_major}" == "11" ]]; then
    export CC=gcc
    export CXX=g++
    return 0
  fi

  local GCC_INSTALL="${BASE}/gcc-11-install"

  if [[ -x "${GCC_INSTALL}/bin/gcc" ]]; then
    echo "Using cached GCC 11 from ${GCC_INSTALL}"
    export CC="${GCC_INSTALL}/bin/gcc"
    export CXX="${GCC_INSTALL}/bin/g++"
    return 0
  fi

  echo "Building GCC ${GCC11_VERSION} from source (one-time, takes a while)..."
  local GCC_SRC="${BASE}/gcc-${GCC11_VERSION}"
  local GCC_BUILD="${BASE}/gcc-11-build"

  (
    if [[ ! -d "${GCC_SRC}" ]]; then
      mkdir -p "${BASE}"
      cd "${BASE}"
      wget -q "https://ftp.gnu.org/gnu/gcc/gcc-${GCC11_VERSION}/gcc-${GCC11_VERSION}.tar.xz"
      echo "${GCC11_TARBALL_SHA512}  gcc-${GCC11_VERSION}.tar.xz" | sha512sum -c -
      tar xf "gcc-${GCC11_VERSION}.tar.xz"
      rm -f "gcc-${GCC11_VERSION}.tar.xz"
      cd "${GCC_SRC}"
      ./contrib/download_prerequisites
    fi

    mkdir -p "${GCC_BUILD}"
    cd "${GCC_BUILD}"
    "${GCC_SRC}/configure" \
      --prefix="${GCC_INSTALL}" \
      --enable-languages=c,c++ \
      --disable-multilib \
      --disable-bootstrap \
      --disable-libsanitizer

    make -j"$(nproc)"
    make install
  ) || return 1

  export CC="${GCC_INSTALL}/bin/gcc"
  export CXX="${GCC_INSTALL}/bin/g++"
}
