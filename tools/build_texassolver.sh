#!/usr/bin/env bash
# Build third_party/texassolver/build/console_solver (TexasSolver, console branch). Idempotent;
# re-run after upstream changes.
#   macOS: brew install cmake libomp. Apple clang needs libomp passed explicitly; OMP_PREFIX
#          overrides its location (default: `brew --prefix libomp`).
#   Linux: cmake and a C++17 compiler with OpenMP (GCC's libgomp works as is; for clang install
#          libomp-dev). OMP_PREFIX is not used; CMake's FindOpenMP locates the runtime.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/third_party/texassolver"
mkdir -p "$SRC/build"
cd "$SRC/build"
if [ "$(uname -s)" = Darwin ]; then
  OMP="${OMP_PREFIX:-$(brew --prefix libomp)}"
  cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DOpenMP_CXX_FLAGS="-Xpreprocessor -fopenmp -I$OMP/include" -DOpenMP_CXX_LIB_NAMES=omp \
    -DOpenMP_C_FLAGS="-Xpreprocessor -fopenmp -I$OMP/include" -DOpenMP_C_LIB_NAMES=omp \
    -DOpenMP_omp_LIBRARY="$OMP/lib/libomp.dylib" -DCMAKE_EXE_LINKER_FLAGS="-L$OMP/lib -lomp" >/dev/null
  JOBS="$(sysctl -n hw.ncpu)"
else
  cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 >/dev/null
  JOBS="$(nproc)"
fi
make -j"$JOBS" console_solver
echo "built: $SRC/build/console_solver"
