# SPDX-License-Identifier: AGPL-3.0-only
"""Shared patch-header parsing for the Orca patch series (02 section 2, compliance.md 5a).

A patch in ``engine/patches/orca/`` is a ``git format-patch`` style file: an
mbox-like header, a commit message, then ``diff --git`` hunks relative to the
root of the Orca tree.  Besides the usual ``From:``, ``Date:`` and ``Subject:``
lines the message body must carry these trailers, one per line::

    Purpose: <one line, what this changes and why>
    SPDX-License-Identifier: (the identifier line; its value must be AGPL-3.0-only)
    Upstream-Status: Pending | to be submitted | Submitted <url> | Merged <url> | Backport <ref> | Not-upstreamable <reason>
    Orca-Base: v2.4.2

``Noop: true`` marks a patch with no hunks (the tooling example); it is skipped
by ``apply_patches.py`` and by NOTICE generation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

NAME_RE = re.compile(r"^(\d{4})-([a-z0-9]+(?:-[a-z0-9]+)*)\.patch$")
FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):[ \t]*(.*?)\s*$")
DIFF_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
UPSTREAM_RE = re.compile(
    r"^(Pending|(?i:to be submitted)|Submitted\s+https?://\S+|Merged\s+https?://\S+|Backport\s+\S+|Not-upstreamable\s+\S.*)$"
)
EXPECTED_SPDX = "AGPL-3.0-only"
REQUIRED = (
    "From",
    "Date",
    "Subject",
    "Purpose",
    "SPDX-License-Identifier",
    "Upstream-Status",
    "Orca-Base",
)


@dataclass
class Patch:
    path: Path
    number: int | None
    fields: dict[str, str] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)

    @property
    def noop(self) -> bool:
        return self.fields.get("Noop", "").lower() == "true"


def parse(path: Path) -> Patch:
    m = NAME_RE.match(path.name)
    patch = Patch(path=path, number=int(m.group(1)) if m else None)
    in_header = True
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        d = DIFF_RE.match(line)
        if d:
            in_header = False
            patch.files.append(d.group(2))
        if not in_header:
            continue
        f = FIELD_RE.match(line)
        if f and f.group(1) not in patch.fields:
            patch.fields[f.group(1)] = f.group(2)
    return patch


def parse_date(value: str) -> datetime | None:
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def series(patch_dir: Path) -> list[Path]:
    """Patch files in application order (the four-digit prefix sorts lexically)."""
    return sorted(patch_dir.glob("*.patch"))


def read_pin(pin_file: Path) -> dict[str, str]:
    pin: dict[str, str] = {}
    for line in pin_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            pin[k.strip()] = v.strip()
    return pin


def problems(patch: Patch, pin_tag: str | None = None) -> list[str]:
    """Return human-readable problems; empty means the header is fine."""
    out: list[str] = []
    name = patch.path.name
    if patch.number is None:
        out.append(f"{name}: file name must match NNNN-short-name.patch (lowercase, digits, hyphens)")
    for key in REQUIRED:
        if not patch.fields.get(key):
            out.append(f"{name}: missing header field '{key}:'")
    frm = patch.fields.get("From", "")
    if frm and not re.search(r"\S.*<[^<>@\s]+@[^<>\s]+>", frm):
        out.append(f"{name}: 'From:' must be 'Name <email>'")
    date = patch.fields.get("Date", "")
    if date and parse_date(date) is None:
        out.append(f"{name}: 'Date:' is not an RFC 2822 or ISO 8601 date: {date!r}")
    spdx = patch.fields.get("SPDX-License-Identifier")
    if spdx and spdx != EXPECTED_SPDX:
        out.append(f"{name}: SPDX-License-Identifier must be {EXPECTED_SPDX} (Orca is AGPL-3.0-only), got {spdx!r}")
    up = patch.fields.get("Upstream-Status")
    if up and not UPSTREAM_RE.match(up):
        out.append(
            f"{name}: Upstream-Status must be Pending, 'to be submitted', 'Submitted <url>', 'Merged <url>', "
            f"'Backport <ref>' or 'Not-upstreamable <reason>', got {up!r}"
        )
    base = patch.fields.get("Orca-Base")
    if base and pin_tag and base != pin_tag:
        out.append(f"{name}: Orca-Base is {base} but engine/ORCA_PIN pins {pin_tag}; rebase the patch and update the header")
    if patch.noop and patch.files:
        out.append(f"{name}: 'Noop: true' patches must not contain hunks")
    if not patch.noop and not patch.files:
        out.append(f"{name}: no 'diff --git' hunks (use 'Noop: true' only for the tooling example)")
    for f in patch.files:
        parts = f.split("/")
        if f.startswith("/") or ".." in parts or parts[0] in (".git", "engine", "third_party"):
            out.append(f"{name}: hunk path {f!r} must be relative to the Orca tree root")
    return out
