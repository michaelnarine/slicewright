# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for GUI scripts: ``Blender --factory-startup --python gui_x.py -- --out DIR``.

``-b`` cannot create GPU shaders, so these run in a real (or xvfb) window. All work happens in a
one-shot ``bpy.app.timers`` callback once the window exists; the script writes ``results.json``
and its PNGs, then exits the process (no window is left open). Not importable outside Blender.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.dirname(TESTS_DIR)
for _p in (ADDON_DIR, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name: str, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


OUT = arg("--out", os.path.join(os.environ.get("TMPDIR", "/tmp"), "slw-gui"))
os.makedirs(OUT, exist_ok=True)
RESULTS: dict = {"checks": {}, "errors": [], "info": {}}


def log(*a) -> None:
    print("[slw]", *a, flush=True)


def check(name: str, ok: bool, **info) -> bool:
    RESULTS["checks"][name] = {"pass": bool(ok), **info}
    log("CHECK", name, "PASS" if ok else "FAIL", info)
    return bool(ok)


def finish(code: int | None = None) -> None:
    import bpy
    import gpu
    RESULTS["info"].update(blender=bpy.app.version_string, backend=gpu.platform.backend_type_get(),
                           device=gpu.platform.renderer_get(), vendor=gpu.platform.vendor_get())
    failed = [k for k, v in RESULTS["checks"].items() if not v["pass"]]
    if code is None:
        code = 1 if (failed or RESULTS["errors"]) else 0
    with open(os.path.join(OUT, "results.json"), "w") as f:
        json.dump(RESULTS, f, indent=1)
    log("DONE exit", code, "failed:", failed)
    sys.stdout.flush()
    os._exit(code)


def run(main, delay: float = 1.0) -> None:
    """Run ``main()`` once from a timer (a GPU context exists there), then exit."""
    import bpy

    def go():
        t0 = time.perf_counter()
        try:
            main()
        except Exception:  # noqa: BLE001
            RESULTS["errors"].append(traceback.format_exc())
            log("ERROR", RESULTS["errors"][-1])
        RESULTS["info"]["seconds"] = round(time.perf_counter() - t0, 2)
        finish()

    bpy.app.timers.register(go, first_interval=delay)
