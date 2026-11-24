#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Open Blender with the Slicewright add-on loaded from this checkout and the locally built engine.
#
#   addon/tools/run_blender.sh [--fake] [--profile DIR] [FILE.blend] [-- extra Blender args]
#
# It uses its own Blender profile (default: ../BlenderSlicer-local/dev-profile, outside the repo), so your
# normal Blender preferences are never read or written. Preferences you change inside this Blender (themes,
# window layout) persist in that profile between runs. Open the Slicer tab with N in the 3D viewport.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
PROFILE="$REPO/../BlenderSlicer-local/dev-profile"
ENGINE="real"
FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --fake) ENGINE="fake"; shift ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --) shift; break ;;
    -h|--help) sed -n 2,11p "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) FILE="$1"; shift ;;
  esac
done

[ -x "$BLENDER" ] || { echo "Blender not found at $BLENDER (set BLENDER=...)" >&2; exit 1; }
mkdir -p "$PROFILE"
PROFILE="$(cd "$PROFILE" && pwd)"

if [ "$ENGINE" = "real" ]; then
  ENGINE_DIR="$REPO/engine/build/engine/python"
  if [ ! -f "$ENGINE_DIR/slicewright_engine/_native.abi3.so" ]; then
    echo "The engine is not built ($ENGINE_DIR). Run engine/tools/build_local_macos.sh, or use --fake." >&2
    exit 1
  fi
  export SLICEWRIGHT_ENGINE_PATH="$ENGINE_DIR"
else
  export SLICEWRIGHT_ENGINE_MODULE="fake_engine"
  export PYTHONPATH="$REPO/addon/tests${PYTHONPATH:+:$PYTHONPATH}"
fi

export BLENDER_USER_RESOURCES="$PROFILE"
BOOT="import sys; sys.path.insert(0, r'$REPO/addon'); import slicewright; slicewright.register()"

# Run from the profile folder so Blender never drops stray files (recovery, untitled.blend) into the repo.
cd "$PROFILE"
ARGS=(--python-expr "$BOOT")
if [ -n "$FILE" ]; then
  FILE="$(cd "$(dirname "$FILE")" && pwd)/$(basename "$FILE")"
  exec "$BLENDER" "$FILE" "${ARGS[@]}" "$@"
fi
exec "$BLENDER" "${ARGS[@]}" "$@"
