#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Summarise an ASan/UBSan log from the sanitizer CI job and check it against the allowlist.

    summarize_sanitizer_log.py LOG

Prints UBSan findings grouped by (file, kind) and counts AddressSanitizer errors. Exit status 1 when

* ASan reported anything (memory errors are never acceptable),
* UBSan reported a (file, kind) that is not in ALLOWED_UBSAN (new undefined behaviour), or
* an entry of ALLOWED_UBSAN did not occur (a stale entry: the finding was fixed, or a patch moved it, so the
  allowlist must shrink with it).

Exit 2 on unreadable input. Findings are keyed by source file (relative to Orca's ``src/``) and kind, not by line,
because the lines move with every Orca rebase; the line of the first report is printed for orientation.
"""
from __future__ import annotations

import argparse
import collections
import re
import sys

UBSAN = re.compile(r"^(?P<loc>\S+?):(?P<line>\d+)(?::\d+)?: runtime error: (?P<msg>.*)$")
ASAN = re.compile(r"ERROR: AddressSanitizer: (?P<kind>[\w-]+)")

# (file relative to Orca's src/, kind) -> why it is tolerated. Both are upstream Orca code that we do not patch.
ALLOWED_UBSAN: dict[tuple[str, str], str] = {
    ("libslic3r/PrintConfig.cpp", "float-cast-overflow"):
        "a NaN default converted to unsigned char while a config option is initialised (upstream Orca; the value "
        "is never used)",
    ("libslic3r/GCode.cpp", "null-reference-binding"):
        "GCodeGenerator binds a reference to the SpiralVase member that is null when spiral vase is off "
        "(upstream Orca; the reference is not read in that case)",
}

_KINDS = (
    (re.compile(r"is outside the range of representable values"), "float-cast-overflow"),
    (re.compile(r"reference binding to null pointer"), "null-reference-binding"),
    (re.compile(r"^load of (?:misaligned|null)"), "invalid-load"),
    (re.compile(r"^load of value \d+, which is not a valid value for type 'bool'"), "invalid-bool"),
    (re.compile(r"^(member (?:access|call) (?:within|on) null pointer)"), "null-member-access"),
    (re.compile(r"^(?:signed|unsigned) integer overflow"), "integer-overflow"),
    (re.compile(r"^(?:left |right )?shift"), "bad-shift"),
    (re.compile(r"^division by zero"), "division-by-zero"),
    (re.compile(r"^index \S+ out of bounds"), "index-out-of-bounds"),
)


def kind_of(msg: str) -> str:
    for rx, name in _KINDS:
        if rx.search(msg):
            return name
    return re.sub(r"[-\d.]+", "N", msg)[:60]


def relative_file(path: str) -> str:
    marker = "/src/"
    i = path.rfind("orca-src" + marker)
    if i >= 0:
        return path[i + len("orca-src") + len(marker):]
    return "/".join(path.split("/")[-3:])


def summarize(lines):
    sites: "collections.OrderedDict[tuple[str, str], list]" = collections.OrderedDict()  # key -> [count, first line]
    asan = collections.Counter()
    for line in lines:
        m = UBSAN.match(line.strip())
        if m:
            key = (relative_file(m["loc"]), kind_of(m["msg"]))
            entry = sites.setdefault(key, [0, int(m["line"])])
            entry[0] += 1
            continue
        a = ASAN.search(line)
        if a:
            asan[a["kind"]] += 1
    return sites, asan


def check(sites, asan, allowed=None):
    """Returns the list of problems (empty when the log is clean against the allowlist)."""
    allowed = ALLOWED_UBSAN if allowed is None else allowed
    problems = []
    if asan:
        problems.append(f"AddressSanitizer reported errors: {dict(asan)}")
    for (path, kind), (n, line) in sites.items():
        if (path, kind) not in allowed:
            problems.append(f"new UBSan finding not in the allowlist: {path}:{line} {kind} ({n} reports)")
    for (path, kind) in allowed:
        if (path, kind) not in sites:
            problems.append(f"stale allowlist entry (no such finding any more): {path} {kind}; remove it from ALLOWED_UBSAN")
    return problems


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    args = ap.parse_args(argv)
    try:
        lines = open(args.log, errors="replace").read().splitlines()
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    sites, asan = summarize(lines)
    print(f"AddressSanitizer errors: {sum(asan.values())} {dict(asan)}")
    print(f"UBSan: {len(sites)} distinct (file, kind), {sum(n for n, _ in sites.values())} reports")
    for (path, kind), (n, line) in sorted(sites.items(), key=lambda kv: -kv[1][0]):
        status = "allowed" if (path, kind) in ALLOWED_UBSAN else "NEW"
        print(f"  {n:6d}  {path}:{line}  {kind}  [{status}]")
    problems = check(sites, asan)
    for p in problems:
        print(f"::error::{p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
