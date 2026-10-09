#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Cache key for the deps: Orca tag + hash of Orca's deps/ tree + our deps list/scripts + platform.
set -euo pipefail
plat="$1"; orca="${2:-orca}"; here="$(cd "$(dirname "$0")" && pwd)"
if command -v sha256sum >/dev/null 2>&1; then sum() { sha256sum; }; else sum() { shasum -a 256; }; fi
h="$( { git -C "$orca" rev-parse HEAD:deps; tr -d '\r' < "$here/deps_targets.txt"; tr -d '\r' < "$here/deps_unix.sh"; tr -d '\r' < "$here/deps_win.sh"; } | sum | cut -c1-16)"
echo "deps-v2.4.2-$plat-$h"
