#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""DCO check: every commit in BASE..HEAD needs a Signed-off-by trailer.

Usage: check_dco.py BASE HEAD
Merge commits are skipped.  Any sign-off present is accepted: the trailer must have the form
"Signed-off-by: Name <email>", but it is not compared against the commit author.
"""
from __future__ import annotations

import re
import subprocess
import sys

TRAILER_RE = re.compile(r"^Signed-off-by:\s+\S.*\s<[^<>\s]+@[^<>\s]+>\s*$", re.MULTILINE)


def unsigned(commits: list[tuple[str, str]]) -> list[str]:
    """commits: (sha, full message). Returns the shas lacking a sign-off."""
    return [sha for sha, msg in commits if not TRAILER_RE.search(msg)]


def read_commits(base: str, head: str) -> list[tuple[str, str]]:
    shas = subprocess.run(
        ["git", "rev-list", "--no-merges", f"{base}..{head}"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    out = []
    for sha in shas:
        msg = subprocess.run(
            ["git", "show", "-s", "--format=%B", sha],
            check=True, capture_output=True, text=True,
        ).stdout
        out.append((sha, msg))
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    commits = read_commits(argv[1], argv[2])
    bad = unsigned(commits)
    for sha in bad:
        print(f"::error::commit {sha[:10]} has no Signed-off-by trailer (use git commit -s)")
    print(f"DCO check: {len(commits)} commits, {len(bad)} unsigned")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
