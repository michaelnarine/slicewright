#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Build the trimmed Orca deps (macOS arm64 / manylinux). Usage: deps_unix.sh <orca-src> <install-dir>
# The build tree lives in <orca-src>/deps/build, as in Orca's own scripts (its patch steps depend on that layout).
set -euo pipefail
ORCA="$(cd "$1" && pwd)"; mkdir -p "$2"; OUT="$(cd "$2" && pwd)"
BLD="$ORCA/deps/build"; mkdir -p "$BLD"
targets="$(tr -d '\r' < "$(dirname "$0")/deps_targets.txt" | tr '\n' ' ')"
if [ "$(uname -s)" = Darwin ]; then
  platform_args=(-DCMAKE_OSX_ARCHITECTURES=arm64 "-DCMAKE_OSX_DEPLOYMENT_TARGET=${OSX_DEPLOYMENT_TARGET:-11.2}" -DCMAKE_IGNORE_PREFIX_PATH=/opt/local:/usr/local:/opt/homebrew)
else
  platform_args=()
fi
export CMAKE_POLICY_VERSION_MINIMUM=3.5
# Force static zlib/expat from deps rather than whatever the system provides.
cmake -S "$ORCA/deps" -B "$BLD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DDESTDIR="$OUT" \
  -DCMAKE_DISABLE_FIND_PACKAGE_ZLIB=ON -DCMAKE_DISABLE_FIND_PACKAGE_EXPAT=ON "${platform_args[@]}"
# shellcheck disable=SC2086
cmake --build "$BLD" --target $targets -j1
echo "deps installed in $OUT/usr/local"; du -sh "$OUT"
