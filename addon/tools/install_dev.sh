#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Make the add-on appear whenever you open Blender, loaded from this checkout (a development install).
#
#   addon/tools/install_dev.sh [--uninstall]
#
# It links  <Blender config>/extensions/user_default/slicewright  to  <repo>/addon/slicewright. Run it yourself:
# it writes under ~/Library/Application Support/Blender, which the assistant never touches on its own. Edits to
# the repo are live after restarting Blender. The engine is found automatically from <repo>/engine/build
# (build it with engine/tools/build_local_macos.sh). Enable the add-on once in Preferences > Add-ons
# (or Get Extensions > Installed) if Blender does not turn it on by itself. Blender must be closed.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${BLENDER_VERSION:-5.1}"
BASE="$HOME/Library/Application Support/Blender/$VERSION/extensions/user_default"
LINK="$BASE/slicewright"

if pgrep -f "Blender.app" >/dev/null; then
  echo "Blender is running. Quit it first." >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  if [ -L "$LINK" ]; then rm "$LINK" && echo "removed $LINK"; else echo "nothing to remove ($LINK is not a link)"; fi
  exit 0
fi

[ -d "$BASE" ] || { echo "$BASE does not exist: open Blender $VERSION once first (it creates the folder)." >&2; exit 1; }
if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
  echo "$LINK already exists and is not a link; not touching it." >&2
  exit 1
fi
[ -f "$REPO/engine/build/engine/python/slicewright_engine/_native.abi3.so" ] || \
  echo "warning: the engine is not built ($REPO/engine/build); the add-on will show its diagnostics panel until you run engine/tools/build_local_macos.sh" >&2
ln -sfn "$REPO/addon/slicewright" "$LINK"
echo "linked $LINK -> $REPO/addon/slicewright"
echo "Open Blender; enable Slicewright under Preferences > Add-ons if it is not on."
