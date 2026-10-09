#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Cache key for the deps: Orca tag + hash of Orca's deps/ tree + our deps list/scripts + platform.
set -euo pipefail
plat="$1"; orca="${2:-orca}"; here="$(cd "$(dirname "$0")" && pwd)"
h="$( { git -C "$orca" rev-parse HEAD:deps; cat "$here/deps_targets.txt" "$here/deps_unix.sh" "$here/deps_win.sh" 2>/dev/null; } | sha256sum | cut -c1-16)"
echo "deps-v2.4.2-$plat-$h"
