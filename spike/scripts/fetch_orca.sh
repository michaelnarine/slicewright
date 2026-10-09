#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Fetch OrcaSlicer at the pinned tag into $1 (default: orca).
set -euo pipefail
dest="${1:-orca}"
tag="v2.4.2"
sha="8500fcdccaa10b5099ac20d252af3a7c560046f1"
if [ ! -d "$dest/.git" ]; then
  git clone -q --depth 1 --branch "$tag" https://github.com/OrcaSlicer/OrcaSlicer.git "$dest"
fi
got="$(git -C "$dest" rev-parse HEAD)"
if [ "$got" != "$sha" ]; then echo "unexpected Orca commit $got"; exit 1; fi
echo "orca $tag @ $got"
