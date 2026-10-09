# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs the ``gui_*.py`` scripts in a real Blender window (opt in: it opens and closes a window).

    SLICEWRIGHT_GUI=1 [SLICEWRIGHT_GPU_BACKEND=opengl|vulkan|metal] pytest addon/tests/gui

Each script writes ``results.json`` (checks, backend, timings) and screenshots to a directory
that is kept under ``$SLICEWRIGHT_GUI_OUT`` (default: pytest's tmp dir) for CI to upload.
Blender is found the same way as for the headless tests.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "blender"))
from test_blender_headless import find_blender  # noqa: E402

SCRIPTS = sorted(p.name for p in HERE.glob("gui_*.py") if p.name != "gui_common.py")
pytestmark = pytest.mark.skipif(os.environ.get("SLICEWRIGHT_GUI") != "1",
                                reason="set SLICEWRIGHT_GUI=1 to run GUI Blender tests")


@pytest.mark.parametrize("script", SCRIPTS)
def test_gui_script_passes(script, tmp_path):
    exe = find_blender()
    assert exe, "Blender not found (set $BLENDER)"
    out = Path(os.environ.get("SLICEWRIGHT_GUI_OUT", tmp_path)) / Path(script).stem
    out.mkdir(parents=True, exist_ok=True)
    backend = os.environ.get("SLICEWRIGHT_GPU_BACKEND")
    cmd = [exe, "--factory-startup", *(["--gpu-backend", backend] if backend else []),
           "--python", str(HERE / script), "--", "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    sys.stdout.write("\n".join(ln for ln in proc.stdout.splitlines() if "[slw]" in ln))
    results = json.loads((out / "results.json").read_text()) if (out / "results.json").exists() else {}
    failed = [k for k, v in results.get("checks", {}).items() if not v["pass"]]
    assert proc.returncode == 0 and results and not failed and not results["errors"], (
        f"{script}: exit {proc.returncode}, failed {failed}, errors {results.get('errors')}")
