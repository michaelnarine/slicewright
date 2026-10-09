#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Symbol regression guards for the engine binaries (02 section 3.5, plan M2 acceptance).

    check_symbols.py [--only-pyinit] [--check-deps] FILE...

FILE is an executable, a shared module or a static archive. Every file is scanned with ``nm -gP``.

* Forbidden families: no symbol (defined or undefined) may belong to OpenCASCADE, OpenCV, OpenSSL or
  mcut. They are exactly the dependencies the headless build removes; a hit means a patch or a stub
  regressed and something pulled one back in.
* ``--only-pyinit``: for the Python module, the only symbols it may *export* are ``PyInit_*``
  (everything else is hidden, so it cannot clash with Blender's own libraries).
* ``--check-deps`` (macOS): ``otool -L`` may only list system libraries (``/usr/lib``, ``/System``),
  so the module never depends on a library the wheel does not ship.

Exit status 0 when clean, 1 on violations, 2 when a tool is missing.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys

FORBIDDEN = {
    # OpenCASCADE Technology (STEP, XCAF, TKernel ...). Mangled C++ names embed the class names.
    "OCCT": re.compile(
        r"(Standard_(Transient|Failure|Mutex|Type|Handle|Real|Integer)|TopoDS_|TopExp|BRepMesh|BRep_|XCAFDoc|"
        r"STEPControl|STEPCAF|OpenCASCADE|Message_Progress|TDocStd|TCollection_|Interface_Static|Handle_)"
    ),
    # OpenCV: namespace cv (mangled as 2cv) and the C API.
    "OpenCV": re.compile(r"(^_?_ZN[K]?2cv\d|^_?_ZN[K]?2cv[A-Z]|^_?cv[A-Z][A-Za-z]+$|opencv)"),
    # OpenSSL / LibreSSL C API (the Boost-based MD5 shim has C++ linkage, so it never matches).
    "OpenSSL": re.compile(r"^_?(MD5_|SHA\d*_|EVP_|SSL_|OPENSSL_|CRYPTO_|BIO_|ERR_|X509_|OpenSSL_)"),
    # mcut (patch 0006 removes it): its C API and namespace.
    "mcut": re.compile(r"(mcut|^_?mc(Dispatch|CreateContext|ReleaseContext|GetInfo|GetConnectedComponents|DebugMessageCallback)\b)"),
}
PYINIT = re.compile(r"^_?PyInit_\w+$")
SYSTEM_LIB = re.compile(r"^(/usr/lib/|/System/Library/)")


def parse_nm(text: str) -> list[tuple[str, str]]:
    """(name, type) pairs from ``nm -gP`` output. Archive member headers and blank lines are skipped."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[1]) == 1 and not line.endswith(":"):
            out.append((parts[0], parts[1]))
    return out


def forbidden_hits(symbols: list[tuple[str, str]]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for name, _ in symbols:
        for family, rx in FORBIDDEN.items():
            if rx.search(name):
                hits.setdefault(family, []).append(name)
    return hits


def non_pyinit_exports(symbols: list[tuple[str, str]]) -> list[str]:
    """Defined external symbols that are not PyInit_*. ``U`` (undefined) and weak undefined do not count."""
    return sorted({n for n, t in symbols if t not in ("U", "u", "w", "v") and not PYINIT.match(n)})


def bad_dependencies(otool_output: str) -> list[str]:
    deps = []
    for line in otool_output.splitlines()[1:]:  # first line is the file name
        path = line.strip().split(" (", 1)[0]
        if path and not SYSTEM_LIB.match(path):
            deps.append(path)
    return deps


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    return proc.stdout


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--only-pyinit", action="store_true")
    ap.add_argument("--check-deps", action="store_true")
    ap.add_argument("--nm", default=shutil.which("nm"))
    ap.add_argument("--otool", default=shutil.which("otool"))
    args = ap.parse_args(argv)
    if not args.nm:
        print("error: nm not found", file=sys.stderr)
        return 2
    if args.check_deps and not args.otool:
        print("error: --check-deps needs otool (macOS)", file=sys.stderr)
        return 2

    failures = 0
    for path in args.files:
        symbols = parse_nm(run([args.nm, "-gP", path]))
        problems: list[str] = []
        for family, names in forbidden_hits(symbols).items():
            problems.append(f"{family} symbols present ({len(names)}), e.g. {', '.join(sorted(set(names))[:5])}")
        if args.only_pyinit:
            extra = non_pyinit_exports(symbols)
            if extra:
                problems.append(f"exports other than PyInit_* ({len(extra)}), e.g. {', '.join(extra[:5])}")
            if not any(PYINIT.match(n) and t not in ("U", "u") for n, t in symbols):
                problems.append("no PyInit_* export found")
        if args.check_deps:
            bad = bad_dependencies(run([args.otool, "-L", path]))
            if bad:
                problems.append(f"non-system dependencies: {', '.join(bad)}")
        if problems:
            failures += 1
            for p in problems:
                print(f"::error::{path}: {p}")
        else:
            print(f"ok   {path} ({len(symbols)} symbols)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
