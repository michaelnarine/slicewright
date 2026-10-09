#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Configure + build the nanobind module on macOS/Linux. Usage: build_module_unix.sh <python-exe>
# Expects ./orca (patched), ./deps-out, writes ./build and ./dist.
set -euxo pipefail
PY="$1"
extra=()
if [ "$(uname -s)" = Darwin ]; then
  extra+=(-DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=11.2 -DCMAKE_IGNORE_PREFIX_PATH=/opt/local:/usr/local:/opt/homebrew)
fi
"$PY" -m pip install -q nanobind wheel
cmake -S spike -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DORCA_DIR="$PWD/orca" -DCMAKE_PREFIX_PATH="$PWD/deps-out/usr/local" -DPython_EXECUTABLE="$PY" "${extra[@]}"
cmake --build build --target slicewright_engine -j "$(getconf _NPROCESSORS_ONLN)"
ls -la build/*.so
