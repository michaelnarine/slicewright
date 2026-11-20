#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Pack Orca's profile JSON into one deterministic zip (02 section 7.1).

    make_profiles_zip.py <orca resources/profiles dir> <out.zip>

Only ``*.json`` files are stored under a root folder ``profiles/`` (so ``profiles/<Vendor>.json`` and
``profiles/<Vendor>/{machine,process,filament}/*.json``), paths with forward slashes, in sorted
order with a fixed timestamp, so the same tree always gives the same bytes. The stray
``FlyingBear/error_hull_show`` directory is skipped. One zip read with ``zipfile`` avoids Windows MAX_PATH
problems on deep vendor paths.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
SKIP_PARTS = {"error_hull_show"}
# The archive mirrors Orca's resources/profiles/: profiles/<Vendor>.json and
# profiles/<Vendor>/{machine,process,filament}/*.json (lead's decision for profiles_archive()).
ROOT_FOLDER = "profiles/"


def collect(root: Path) -> list[Path]:
    files = [p for p in root.rglob("*.json") if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_PARTS)]
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def build(root: Path, out: Path) -> int:
    files = collect(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            info = zipfile.ZipInfo(ROOT_FOLDER + p.relative_to(root).as_posix(), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())
    tmp.replace(out)
    return len(files)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    root, out = Path(argv[0]), Path(argv[1])
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    n = build(root, out)
    print(f"{out}: {n} profiles, {out.stat().st_size / 1e6:.1f} MB")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
