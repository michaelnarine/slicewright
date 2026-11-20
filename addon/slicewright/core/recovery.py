# SPDX-License-Identifier: GPL-3.0-or-later
"""File-name bookkeeping for recovery copies (03 section 6.1). No ``bpy``.

A recovery copy is ``<stem>-<n>.blend`` in one directory, ``n`` counting up across the directory so the newest
copy is the one with the largest number whatever the stem. Only files with that shape are ever listed or removed.
"""
from __future__ import annotations

import os
import re

INTERVAL_S = 300.0          # at most one copy per 5 minutes
KEEP = 3                    # the newest copies that survive pruning
_COPY_RE = re.compile(r"^(?P<stem>.+)-(?P<n>\d+)\.blend$")
_UNSAFE_RE = re.compile(r"[^\w.\- ]+")
MAX_STEM = 40


def safe_stem(name: str) -> str:
    """The .blend's base name made safe for a file name (``untitled`` for an unsaved file)."""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    stem = _UNSAFE_RE.sub("_", stem).strip(" .")[:MAX_STEM].strip(" .")
    return stem or "untitled"


def list_copies(directory: str) -> list[tuple[int, str]]:
    """``(n, path)`` of every recovery copy in ``directory``, oldest first."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = []
    for name in names:
        m = _COPY_RE.match(name)
        if m:
            found.append((int(m.group("n")), os.path.join(directory, name)))
    return sorted(found)


def next_path(directory: str, name: str) -> str:
    """The path of the next copy for the file ``name`` (a .blend path or name; empty when unsaved)."""
    copies = list_copies(directory)
    n = copies[-1][0] + 1 if copies else 1
    return os.path.join(directory, f"{safe_stem(name)}-{n}.blend")


def prune(directory: str, keep: int = KEEP) -> list[str]:
    """Remove all but the newest ``keep`` copies. A copy that cannot be removed is skipped. Returns what went."""
    removed = []
    copies = list_copies(directory)
    for _, path in copies[:max(0, len(copies) - keep)]:
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed
