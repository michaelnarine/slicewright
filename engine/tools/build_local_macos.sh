#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Builds the real engine on a Mac the way .github/workflows/engine-ci.yml does (deps job, then the link job),
# in engine/build (gitignored). Idempotent: every step is skipped or incremental when it is already done.
#
#   engine/tools/build_local_macos.sh            # venv, submodule, patches, deps, engine + module + tests
#   SLICEWRIGHT_SKIP_TESTS=1 ...                 # leave the C++ tests out
#
# Needs: brew install ninja autoconf automake libtool texinfo ccache; uv (or python3.13 on PATH). Xcode.
# Result: engine/build/engine/python/slicewright_engine/_native.abi3.so (see engine/README.md for how to use it).
set -euo pipefail
cd "$(dirname "$0")/../.."
B=engine/build
mkdir -p "$B/logs"

# Python venv with the workflow's pinned test and build tools (CMake is pinned: Boost's configure fails with 4.5.0).
if [ ! -x "$B/venv/bin/python" ]; then
  if command -v uv >/dev/null; then uv venv --python 3.13 "$B/venv"; else python3.13 -m venv "$B/venv"; fi
  if command -v uv >/dev/null; then PIP="uv pip install --python $B/venv/bin/python"; else PIP="$B/venv/bin/python -m pip install"; fi
  $PIP "pytest==9.1.1" "numpy==2.5.3" "nanobind==3.1.0" "cmake==4.4.5"
fi
export PATH="$PWD/$B/venv/bin:$PATH"

# Orca at the pinned commit (shallow), then the patch series (apply_patches.py is a no-op when up to date).
git submodule update --init --depth 1
python engine/tools/lint_patch_headers.py --check-pin
python engine/tools/apply_patches.py

# Deps (Orca's trimmed superbuild): about 4 min cold on 10 cores.
start=$SECONDS
cmake -S engine/deps -B "$B/deps" -G Ninja -DCMAKE_OSX_ARCHITECTURES=arm64
cmake --build "$B/deps"
echo "deps: $((SECONDS-start)) s"

# libslic3r, the link check, the Orca test subset and the module.
start=$SECONDS
cmake -S engine -B "$B/engine" -G Ninja -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
  -DCMAKE_PREFIX_PATH="$PWD/$B/deps/prefix/usr/local" -DCMAKE_IGNORE_PREFIX_PATH="/opt/local;/usr/local" \
  -DSLICEWRIGHT_BUILD_TESTS="$([ "${SLICEWRIGHT_SKIP_TESTS:-0}" = 1 ] && echo OFF || echo ON)" \
  -DSLICEWRIGHT_BUILD_MODULE=ON -DPython_EXECUTABLE="$(which python)"
cmake --build "$B/engine"
echo "engine: $((SECONDS-start)) s"
ls -l "$B/engine/python/slicewright_engine/"
