#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# Regenerates the golden reference G-code (engine/tests/fixtures/spike_cube/reference_orca_v2.4.2.gcode) with the
# OFFICIAL OrcaSlicer v2.4.2 release binary, so that nobody has to trust a file of unknown origin
# (engine/tests/golden/PROVENANCE.md records the exact command, hashes and host).
#
#   engine/tools/regen_golden.sh [--work DIR] [--out FILE] [--keep]
#
# macOS only for now (Windows and Linux are paused). The release asset is downloaded from GitHub, its SHA-256 is
# checked against the pinned value below (and aborts on mismatch), the CLI runs from the mounted disk image, and
# the result is compared, after normalisation, with the committed reference. Exit status 0 means the committed
# reference is reproduced; 1 means it is not (the new file is left at --out for inspection).
set -euo pipefail

TAG="v2.4.2"
ASSET="OrcaSlicer_Mac_universal_V2.4.2.dmg"
ASSET_SHA256="e15e7bb1b66214ec6e96b169b388004179c4f5f705effcdaf8c80d4992ee0366"
URL="https://github.com/OrcaSlicer/OrcaSlicer/releases/download/${TAG}/${ASSET}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIX="$HERE/../tests/fixtures/spike_cube"
WORK="${TMPDIR:-/tmp}/slicewright-regen-golden"
OUT=""
KEEP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --work) WORK="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ "$(uname -s)" = Darwin ] || { echo "regen_golden.sh supports macOS only for now" >&2; exit 2; }
mkdir -p "$WORK"
OUT="${OUT:-$WORK/reference_regenerated.gcode}"

DMG="$WORK/$ASSET"
if [ ! -f "$DMG" ]; then
    echo "downloading $URL"
    curl -fL --retry 3 -o "$DMG" "$URL"
fi
actual="$(shasum -a 256 "$DMG" | cut -d' ' -f1)"
if [ "$actual" != "$ASSET_SHA256" ]; then
    echo "SHA-256 mismatch for $ASSET: expected $ASSET_SHA256, got $actual" >&2
    rm -f "$DMG"
    exit 1
fi
echo "asset verified: $ASSET sha256 $ASSET_SHA256"

MNT="$WORK/mnt"
mkdir -p "$MNT"
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$DMG" > /dev/null
cleanup() {
    hdiutil detach "$MNT" -quiet || true
    if [ "$KEEP" = 0 ]; then rm -f "$DMG"; fi
}
trap cleanup EXIT

# The CLI refuses a process or filament preset whose compatible_printers does not include the printer, and the
# spike profiles list none: it needs [""] (the official CLI's own convention for "any printer").
IN="$WORK/in"
rm -rf "$IN" "$WORK/out"
mkdir -p "$IN"
cp "$FIX/machine.json" "$FIX/cube.stl" "$IN/"
python3 - "$FIX" "$IN" <<'EOF'
import json, sys
src, dst = sys.argv[1:3]
for name in ("process", "filament"):
    d = json.load(open(f"{src}/{name}.json"))
    d["compatible_printers"] = [""]
    json.dump(d, open(f"{dst}/{name}.json", "w"), indent=1)
EOF

CLI="$MNT/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"
(cd "$IN" && "$CLI" --slice 0 --load-settings "machine.json;process.json" --load-filaments "filament.json" \
    --outputdir "$WORK/out" cube.stl > "$WORK/cli.log" 2>&1) || { tail -20 "$WORK/cli.log" >&2; exit 1; }
cp "$WORK/out/plate_1.gcode" "$OUT"
echo "regenerated reference: $OUT ($(wc -l < "$OUT") lines)"

python3 - "$FIX/reference_orca_v2.4.2.gcode" "$OUT" "$HERE" <<'EOF'
import sys
sys.path.insert(0, sys.argv[3])
import normalize_gcode as n
ref = n.normalize(open(sys.argv[1], errors="replace").read())
new = n.normalize(open(sys.argv[2], errors="replace").read())
if ref == new:
    print(f"OK: the committed reference is reproduced ({len(ref)} normalised lines, 0 differences)")
    sys.exit(0)
import difflib
d = [x for x in difflib.unified_diff(ref, new, "committed", "regenerated", lineterm="", n=0)]
print(f"DIFFERENT: {len(d)} diff lines", file=sys.stderr)
print("\n".join(d[:40]), file=sys.stderr)
sys.exit(1)
EOF
