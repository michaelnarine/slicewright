#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Launcher of the UI screenshot tool. Run from the repo root:

    python3 addon/tests/tools/run_ui_shots.py [--out DIR] [--engine real|fake] [--only a,b] [--scale 1.0]
                                              [--size WxH] [--blender PATH] [--timeout 480]

Starts a REAL (non-background) Blender with ``--factory-startup`` and a fresh scratch profile outside the repo
(``BLENDER_USER_RESOURCES``, ``TMPDIR``, cwd all point into it), opens a saved scratch .blend (so no splash screen) and
runs ``ui_shots.py`` in it. A watchdog kills the process group if Blender hangs. See ``README.md`` in this folder.
"""
from __future__ import annotations

import argparse
import datetime
import os
import shutil
import signal
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="output directory (default: ui-shots-<timestamp> under the system temp dir)")
    ap.add_argument("--engine", choices=("real", "fake"), default="real")
    ap.add_argument("--only", default="", help="comma-separated step names (a prefix of a step also matches)")
    ap.add_argument("--size", default="", help="WxH of the Blender window in points (best effort)")
    ap.add_argument("--scale", default="1.0", help="UI scale (Preferences > Interface > Resolution Scale), in memory only")
    ap.add_argument("--blender", default=os.environ.get("BLENDER", DEFAULT_BLENDER))
    ap.add_argument("--timeout", type=float, default=480.0, help="seconds before the watchdog kills Blender")
    args = ap.parse_args()

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.abspath(args.out or os.path.join(tempfile.gettempdir(), f"ui-shots-{stamp}"))
    if os.path.commonpath([out, REPO]) == REPO:
        sys.exit(f"refusing to write into the repo: {out}")
    os.makedirs(out, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="slw-uishots-")          # profile, TMPDIR and cwd of Blender; never the repo
    if os.path.commonpath([scratch, REPO]) == REPO:
        sys.exit("the scratch directory is inside the repo")
    prof, tmp = os.path.join(scratch, "profile"), os.path.join(scratch, "tmp")
    os.makedirs(prof)
    os.makedirs(tmp)

    env = dict(os.environ, BLENDER_USER_RESOURCES=prof, TMPDIR=tmp, PYTHONDONTWRITEBYTECODE="1")
    for k in ("BLENDER_USER_CONFIG", "BLENDER_USER_SCRIPTS", "BLENDER_USER_EXTENSIONS", "BLENDER_USER_DATAFILES"):
        env.pop(k, None)
    if args.engine == "real":
        build = os.path.join(REPO, "engine", "build", "engine", "python")
        if not os.path.isdir(os.path.join(build, "slicewright_engine")):
            sys.exit(f"the real engine is not built ({build}); use --engine fake")
        env["SLICEWRIGHT_ENGINE_PATH"] = build
        env["SLICEWRIGHT_ENGINE_MODULE"] = "slicewright_engine"
    else:
        env["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        env.pop("SLICEWRIGHT_ENGINE_PATH", None)

    base = os.path.join(scratch, "base.blend")                  # opening a file instead of the startup scene: no splash
    subprocess.run([args.blender, "-b", "--factory-startup", "--python-expr",
                    f"import bpy; bpy.ops.wm.save_as_mainfile(filepath={base!r})"],
                   cwd=scratch, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)

    cmd = [args.blender, "--factory-startup", "--no-window-focus", "--enable-event-simulate"]
    if args.size:
        w, h = args.size.lower().split("x")
        cmd += ["--window-geometry", "0", "0", w, h]
    cmd += [base, "--python", os.path.join(HERE, "ui_shots.py"), "--", "--out", out, "--engine", args.engine,
            "--scale", args.scale, "--size", args.size, "--watchdog", str(max(30.0, args.timeout - 40))]
    if args.only:
        cmd += ["--only", args.only]
    log_path = os.path.join(out, "blender.log")
    print(f"scratch profile: {scratch}\noutput: {out}", flush=True)
    with open(log_path, "w") as log:
        proc = subprocess.Popen(cmd, cwd=scratch, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            print("watchdog: killing Blender", flush=True)
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code = -9
    shutil.rmtree(scratch, ignore_errors=True)
    manifest = os.path.join(out, "manifest.json")
    print(f"Blender exited with {code}; manifest: {manifest if os.path.exists(manifest) else 'MISSING'}")
    return 0 if os.path.exists(manifest) else 1


if __name__ == "__main__":
    sys.exit(main())
