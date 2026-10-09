#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Check that the trimmed deps superbuild would build exactly the intended set.

    check_dep_list.py <driver-build-dir>

``<driver-build-dir>`` is the build directory of ``engine/deps`` after
``cmake --build <dir> --target orca_deps-configure`` (the nested Orca deps project
is configured; nothing is downloaded or compiled).  The script asks the nested
Ninja build what ``deps`` would do (``ninja -n deps``) and compares the set of
``dep_*`` targets with ``SLICEWRIGHT_DEPS_LIST`` from the driver's cache plus the
dependencies CGAL pulls in (GMP, MPFR).  Exit status 1 on any difference.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

IMPLIED = {"GMP", "MPFR"}  # CGAL DEPENDS dep_GMP dep_MPFR (deps/CGAL/CGAL.cmake)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    driver = Path(argv[0])
    cache = (driver / "CMakeCache.txt").read_text()
    m = re.search(r"^SLICEWRIGHT_DEPS_LIST:[A-Z]+=(.*)$", cache, re.M)
    if not m:
        print(f"error: SLICEWRIGHT_DEPS_LIST not found in {driver}/CMakeCache.txt", file=sys.stderr)
        return 2
    wanted = {n for n in m.group(1).split(";") if n}
    nested = driver / "orca-deps-build"
    out = subprocess.run(["ninja", "-n", "deps"], cwd=nested, capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stdout + out.stderr, file=sys.stderr)
        return 2
    planned = set(re.findall(r"for 'dep_([A-Za-z0-9_]+)'", out.stdout))
    expected = wanted | (IMPLIED if "CGAL" in wanted else set())
    extra, missing = sorted(planned - expected), sorted(expected - planned)
    print(f"planned:  {' '.join(sorted(planned))}")
    print(f"expected: {' '.join(sorted(expected))}")
    if extra or missing:
        print(f"::error::deps plan differs: unexpected {extra}, missing {missing}")
        return 1
    print(f"ok: {len(planned)} dependencies, nothing else")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
