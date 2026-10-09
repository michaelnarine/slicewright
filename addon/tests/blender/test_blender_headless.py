# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs the ``check_*.py`` scripts in headless Blender (``-b --factory-startup``) from plain pytest.

Blender is found through ``$BLENDER``, the usual macOS and Windows install locations or ``PATH``.
Without one the tests skip, unless ``SLICEWRIGHT_REQUIRE_BLENDER=1`` (set in CI), which makes
a missing Blender a failure.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = sorted(p.name for p in HERE.glob("check_*.py"))
CANDIDATES = (
    "/Applications/Blender.app/Contents/MacOS/Blender",
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
)


def find_blender() -> str | None:
    env = os.environ.get("BLENDER")
    if env and Path(env).exists():
        return env
    for c in CANDIDATES:
        if Path(c).exists():
            return c
    return shutil.which("blender")


@pytest.fixture(scope="session")
def blender() -> str:
    exe = find_blender()
    if exe is None:
        if os.environ.get("SLICEWRIGHT_REQUIRE_BLENDER") == "1":
            pytest.fail("Blender is required (SLICEWRIGHT_REQUIRE_BLENDER=1) but was not found")
        pytest.skip("Blender not found (set $BLENDER)")
    return exe


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_passes_in_headless_blender(blender, script, tmp_path):
    env = {**os.environ, "BLENDER_USER_RESOURCES": str(tmp_path / "blender-user")}
    proc = subprocess.run(
        [blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(HERE / script)],
        capture_output=True, text=True, timeout=300, env=env)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    assert proc.returncode == 0, f"{script} failed (exit {proc.returncode}):\n{proc.stdout[-3000:]}"
