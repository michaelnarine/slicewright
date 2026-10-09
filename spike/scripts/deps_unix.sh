#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Build the trimmed Orca deps (macOS arm64 / manylinux). Usage: deps_unix.sh <orca-src> <install-dir> <build-dir>
set -euo pipefail
ORCA="$(cd "$1" && pwd)"; OUT="$2"; BLD="$3"
mkdir -p "$OUT" "$BLD"; OUT="$(cd "$OUT" && pwd)"; BLD="$(cd "$BLD" && pwd)"
targets="$(tr '\n' ' ' < "$(dirname "$0")/deps_targets.txt")"
[ "$(uname -s)" = Darwin ] && platform_args=(-DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET="${OSX_DEPLOYMENT_TARGET:-11.2}" -DCMAKE_IGNORE_PREFIX_PATH=/opt/local:/usr/local:/opt/homebrew) || platform_args=()
export CMAKE_POLICY_VERSION_MINIMUM=3.5
cmake -S "$ORCA/deps" -B "$BLD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DDESTDIR="$OUT" -DDEP_DOWNLOAD_DIR="$BLD/DL" "${platform_args[@]}"
# shellcheck disable=SC2086
cmake --build "$BLD" --target $targets -j1
echo "deps installed in $OUT/usr/local"; du -sh "$OUT"
