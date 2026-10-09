#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Lint the Orca patch series: names, numbering and headers (compliance.md 5a).

    lint_patch_headers.py [--patches DIR] [--pin FILE] [--check-pin]

``--check-pin`` also verifies that the submodule gitlink recorded in the git
index equals ``commit=`` in ``engine/ORCA_PIN`` and that ``.gitmodules`` keeps
``shallow = true``; it needs a git checkout but not an initialised submodule.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import patchhdr  # noqa: E402

ENGINE = Path(__file__).resolve().parents[1]
SUBMODULE = "engine/third_party/OrcaSlicer"


def check_pin(repo: Path, pin: dict[str, str]) -> list[str]:
    errors = []
    out = subprocess.run(
        ["git", "ls-files", "-s", "--", SUBMODULE], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.split()
    if len(out) < 4 or out[0] != "160000":
        errors.append(f"{SUBMODULE} is not a gitlink in the index")
    elif out[1] != pin.get("commit"):
        errors.append(f"submodule is at {out[1]} but engine/ORCA_PIN pins {pin.get('commit')} ({pin.get('tag')})")
    cfg = subprocess.run(
        ["git", "config", "-f", ".gitmodules", "--get", f"submodule.{SUBMODULE}.shallow"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if cfg != "true":
        errors.append(f".gitmodules: submodule.{SUBMODULE}.shallow must be true (the tree is about 500 MB)")
    return errors


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patches", type=Path, default=ENGINE / "patches" / "orca")
    ap.add_argument("--pin", type=Path, default=ENGINE / "ORCA_PIN")
    ap.add_argument("--check-pin", action="store_true")
    args = ap.parse_args(argv)

    pin = patchhdr.read_pin(args.pin) if args.pin.is_file() else {}
    errors: list[str] = []
    if not pin.get("tag") or not pin.get("commit"):
        errors.append(f"{args.pin}: needs tag= and commit=")
    files = patchhdr.series(args.patches)
    seen: dict[int, str] = {}
    for f in files:
        patch = patchhdr.parse(f)
        errors += patchhdr.problems(patch, pin.get("tag"))
        if patch.number is not None:
            if patch.number in seen:
                errors.append(f"{f.name}: number {patch.number:04d} is also used by {seen[patch.number]}")
            seen[patch.number] = f.name
    if args.patches.is_dir():
        for p in args.patches.iterdir():
            if p.is_file() and p.suffix != ".patch" and p.name != ".gitkeep":
                errors.append(f"{p.name}: only *.patch files belong in {args.patches}")
    if args.check_pin:
        errors += check_pin(ENGINE.parent, pin)

    for e in errors:
        print(f"::error::{e}")
    print(f"patch lint: {len(files)} patches, {len(errors)} problems")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
