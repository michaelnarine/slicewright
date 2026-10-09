#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""SPDX boundary check (CONTRIBUTING.md, "Licences by directory").

Every source file we write under ``addon/`` must carry
``SPDX-License-Identifier: GPL-3.0-or-later`` and every one under ``engine/``
``SPDX-License-Identifier: AGPL-3.0-only``, within its first lines.  Files
under the Orca submodule, ``engine/patches/`` and exported fixtures keep their
upstream licensing and are skipped.  A file carrying the *other* directory's
identifier is an error too: that is how code would cross the licence boundary.

Usage: check_spdx.py [repo_root]    (checks ``git ls-files``)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REQUIRED = {
    "addon": "GPL-3.0-or-later",
    "engine": "AGPL-3.0-only",
}

# Extensions that are source code or build scripts. Data (json, toml, md, txt)
# is not checked.
SOURCE_SUFFIXES = {
    ".py", ".pyi", ".glsl", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cmake",
    ".sh", ".ps1",
}
SOURCE_NAMES = {"CMakeLists.txt"}

# Path prefixes (relative to the repo root) that keep upstream licensing.
EXCLUDED_PREFIXES = (
    "engine/third_party/",
    "engine/patches/",
    "engine/tests/fixtures/",
    "addon/slicewright/wheels/",
)

HEADER_LINES = 12
SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.\-+ ()]+?)\s*(?:\*/|-->)?\s*$")


def is_checked(rel: str) -> bool:
    if rel.startswith(EXCLUDED_PREFIXES):
        return False
    top = rel.split("/", 1)[0]
    if top not in REQUIRED:
        return False
    p = Path(rel)
    return p.suffix in SOURCE_SUFFIXES or p.name in SOURCE_NAMES


def spdx_ids(text: str) -> list[str]:
    ids = []
    for line in text.splitlines()[:HEADER_LINES]:
        m = SPDX_RE.search(line)
        if m:
            ids.append(m.group(1).strip())
    return ids


def check_file(rel: str, text: str) -> str | None:
    """Return an error message, or None if the file is fine."""
    expected = REQUIRED[rel.split("/", 1)[0]]
    ids = spdx_ids(text)
    if not ids:
        return f"{rel}: missing 'SPDX-License-Identifier: {expected}' in the first {HEADER_LINES} lines"
    if ids != [expected]:
        return f"{rel}: found SPDX {', '.join(ids)}; this directory requires exactly {expected}"
    return None


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    files = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.split("\0")
    errors = []
    checked = 0
    for rel in filter(None, files):
        if not is_checked(rel):
            continue
        path = root / rel
        if not path.is_file():
            continue
        checked += 1
        err = check_file(rel, path.read_text(encoding="utf-8", errors="replace"))
        if err:
            errors.append(err)
    for e in errors:
        print(f"::error::{e}")
    print(f"SPDX boundary check: {checked} files checked, {len(errors)} problems")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
