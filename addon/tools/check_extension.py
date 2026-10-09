#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks on built extension zips beyond ``blender --command extension validate`` (03 section 10.1).

Usage: check_extension.py ZIP...
Fails if a zip contains ``__pycache__``, tests or tools, binary assets, a source file without
its SPDX header, a bad manifest ``copyright`` entry, or exceeds the size cap.
"""
from __future__ import annotations

import os
import re
import sys
import tomllib
import zipfile
from pathlib import PurePosixPath

MAX_BYTES = 150 * 1024 * 1024               # hard cap per platform zip (03 section 10.3)
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".blend", ".obj", ".stl", ".3mf",
                  ".ttf", ".otf", ".woff", ".woff2", ".exr", ".hdr", ".mp4"}
SPDX = "SPDX-License-Identifier: GPL-3.0-or-later"


def check_zip(path: str) -> list[str]:
    problems = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        size = sum(i.file_size for i in z.infolist())
        if os.path.getsize(path) > MAX_BYTES:
            problems.append(f"{path}: larger than {MAX_BYTES} bytes")
        if "blender_manifest.toml" not in names:
            return problems + [f"{path}: no blender_manifest.toml at the root"]
        for name in names:
            p = PurePosixPath(name)
            if "__pycache__" in p.parts or name.endswith(".pyc"):
                problems.append(f"{path}: contains {name}")
            if p.parts[0] in ("tests", "tools") or ".git" in p.parts:
                problems.append(f"{path}: contains {name}")
            if p.suffix.lower() in ASSET_SUFFIXES:
                problems.append(f"{path}: binary asset {name} (add-on must be asset-free)")
            if p.suffix == ".py":
                head = z.read(name).decode("utf-8", "replace").splitlines()[:5]
                if not any(SPDX in line for line in head):
                    problems.append(f"{path}: {name} has no '{SPDX}' header")
        manifest = tomllib.loads(z.read("blender_manifest.toml").decode("utf-8"))
        for entry in manifest.get("copyright", []):
            if not re.match(r"^\d{4}(-\d{4})? \S", entry):
                problems.append(f"{path}: copyright entry {entry!r} must start with a year")
        if size == 0:
            problems.append(f"{path}: empty")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    problems = [p for path in argv[1:] for p in check_zip(path)]
    for p in problems:
        print(f"::error::{p}")
    print(f"checked {len(argv) - 1} zip(s), {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
