#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Summarise an ASan/UBSan log from the sanitizer CI job.

    summarize_sanitizer_log.py LOG [--max-ubsan N]

Prints UBSan findings grouped by kind and source location (``file:line: runtime error: ...``) and counts AddressSanitizer
errors. Exit status 1 when ASan reported anything (memory errors are never acceptable) or when the number of
*distinct* UBSan sites exceeds ``--max-ubsan`` (the recorded baseline, so new undefined behaviour fails CI while the
known Orca findings are tracked in docs, not hidden). Exit 2 on unreadable input.
"""
from __future__ import annotations

import argparse
import collections
import re
import sys

UBSAN = re.compile(r"^(?P<loc>\S+?:\d+(?::\d+)?): runtime error: (?P<msg>.*)$")
ASAN = re.compile(r"ERROR: AddressSanitizer: (?P<kind>[\w-]+)")


def kind_of(msg: str) -> str:
    m = re.match(r"(?:load of|store to|member access within|member call on|index|shift|signed integer|unsigned integer|"
                 r"division by|reference binding to|applying|downcast of|call to|execution reached|"
                 r"-?\d[\d.e+-]* is outside|negation of|left shift|variable length)[\w ]*", msg)
    return (m.group(0) if m else msg)[:60]


def summarize(lines):
    sites = collections.OrderedDict()
    asan = collections.Counter()
    for line in lines:
        m = UBSAN.match(line.strip())
        if m:
            sites.setdefault((m["loc"], kind_of(m["msg"])), 0)
            sites[(m["loc"], kind_of(m["msg"]))] += 1
            continue
        a = ASAN.search(line)
        if a:
            asan[a["kind"]] += 1
    return sites, asan


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--max-ubsan", type=int, default=None, help="fail when more distinct UBSan sites than this")
    args = ap.parse_args(argv)
    try:
        lines = open(args.log, errors="replace").read().splitlines()
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    sites, asan = summarize(lines)
    by_kind = collections.Counter()
    for (_, kind), n in sites.items():
        by_kind[kind] += n
    print(f"AddressSanitizer errors: {sum(asan.values())} {dict(asan)}")
    print(f"UBSan: {len(sites)} distinct sites, {sum(sites.values())} reports")
    for kind, n in by_kind.most_common():
        print(f"  {n:6d}  {kind}")
    print("sites:")
    for (loc, kind), n in sorted(sites.items(), key=lambda kv: -kv[1])[:40]:
        print(f"  {n:6d}  {loc}  {kind}")
    bad = False
    if asan:
        print("::error::AddressSanitizer reported errors")
        bad = True
    if args.max_ubsan is not None and len(sites) > args.max_ubsan:
        print(f"::error::{len(sites)} distinct UBSan sites, baseline is {args.max_ubsan}")
        bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
