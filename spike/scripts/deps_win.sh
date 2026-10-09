#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Build the trimmed Orca deps on Windows (MSVC 2022, /MD). Usage: deps_win.sh <orca-src> <install-dir> <build-dir>
set -euo pipefail
ORCA="$(cd "$1" && pwd)"; mkdir -p "$2" "$3"; OUT="$(cd "$2" && pwd)"; BLD="$(cd "$3" && pwd)"
targets="$(tr '\n' ' ' < "$(dirname "$0")/deps_targets.txt")"
export CMAKE_POLICY_VERSION_MINIMUM=3.5
cmake -S "$ORCA/deps" -B "$BLD" -G "Visual Studio 17 2022" -A x64 -DCMAKE_BUILD_TYPE=Release -DDESTDIR="$OUT" -DDEP_DOWNLOAD_DIR="$BLD/DL"
# shellcheck disable=SC2086
cmake --build "$BLD" --config Release --target $targets -- -m
echo "deps installed in $OUT/usr/local"
