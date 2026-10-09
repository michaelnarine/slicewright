#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Apply the Orca patch series to a clean copy of the pinned Orca tree.

    apply_patches.py [--source DIR] [--patches DIR] [--work DIR] [--pin FILE] [--check] [--force]

The submodule is never modified.  The pinned commit is exported with
``git archive`` (tracked files only, so stray edits in the submodule cannot
leak in) into the work tree, a fresh directory under ``engine/build/``, and
each ``patches/orca/NNNN-*.patch`` is applied in order with ``git apply``.
Any rejected hunk fails the run and names the patch.  Patches marked
``Noop: true`` are skipped.  ``--check`` applies into a temporary directory and
removes it afterwards.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import patchhdr  # noqa: E402

ENGINE = Path(__file__).resolve().parents[1]
STAMP = ".slicewright-patched"


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def export_tree(source: Path, dest: Path) -> None:
    proc = subprocess.Popen(["git", "archive", "--format=tar", "HEAD"], cwd=source, stdout=subprocess.PIPE)
    assert proc.stdout is not None
    with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
        try:
            tar.extractall(dest, filter="data")  # Python 3.12+
        except TypeError:
            tar.extractall(dest)
    if proc.wait() != 0:
        raise SystemExit(f"git archive failed in {source}")


def series_digest(patches: list[Path], commit: str) -> str:
    h = hashlib.sha256(commit.encode())
    for p in patches:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def run(args: argparse.Namespace) -> int:
    pin = patchhdr.read_pin(args.pin)
    head = git("rev-parse", "HEAD", cwd=args.source) if args.source.is_dir() else None
    if head is None or head.returncode != 0:
        print(f"error: {args.source} is not a git checkout (run: git submodule update --init --depth 1)", file=sys.stderr)
        return 2
    commit = head.stdout.strip()
    if commit != pin.get("commit"):
        print(f"error: {args.source} is at {commit}, ORCA_PIN says {pin.get('commit')} ({pin.get('tag')})", file=sys.stderr)
        return 2
    if git("status", "--porcelain", "--untracked-files=no", cwd=args.source).stdout.strip():
        print(f"error: {args.source} has local modifications; the submodule must stay pristine", file=sys.stderr)
        return 2

    patches = patchhdr.series(args.patches)
    digest = series_digest(patches, commit)
    work = args.work
    if args.check:
        work = Path(tempfile.mkdtemp(prefix="orca-patch-check-")) / "orca-src"
    else:
        work.parent.mkdir(parents=True, exist_ok=True)
        stamp = work / STAMP
        if stamp.is_file() and stamp.read_text().strip() == digest and not args.force:
            print(f"{work} is already up to date ({len(patches)} patches); use --force to rebuild")
            return 0
    try:
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        export_tree(args.source, work)
        if git("init", "-q", cwd=work).returncode != 0:  # keeps git apply inside the work tree
            print("error: git init failed in the work tree", file=sys.stderr)
            return 2
        applied = 0
        for p in patches:
            if patchhdr.parse(p).noop:
                print(f"skip   {p.name} (Noop)")
                continue
            r = git("apply", "--verbose", "--whitespace=nowarn", str(p.resolve()), cwd=work)
            if r.returncode != 0:
                sys.stderr.write(r.stdout + r.stderr)
                print(f"error: {p.name} does not apply to {pin.get('tag')} ({commit[:8]})", file=sys.stderr)
                return 1
            print(f"apply  {p.name}")
            applied += 1
        if not args.check:
            (work / STAMP).write_text(digest + "\n")
        where = "(temporary, removed)" if args.check else str(work)
        print(f"{applied} patch(es) applied to {pin.get('tag')} -> {where}")
        return 0
    finally:
        if args.check:
            shutil.rmtree(work.parent, ignore_errors=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=ENGINE / "third_party" / "OrcaSlicer")
    ap.add_argument("--patches", type=Path, default=ENGINE / "patches" / "orca")
    ap.add_argument("--work", type=Path, default=ENGINE / "build" / "orca-src")
    ap.add_argument("--pin", type=Path, default=ENGINE / "ORCA_PIN")
    ap.add_argument("--check", action="store_true", help="apply into a temporary directory and discard it")
    ap.add_argument("--force", action="store_true", help="rebuild the work tree even if it is up to date")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
