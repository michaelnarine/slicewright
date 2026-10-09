# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for GUI smoke runs: ``Blender --factory-startup --python script.py -- <out_dir>``.

A smoke script is a generator of waits (seconds) driven by one ``bpy.app.timers`` callback; it
captures the 3D viewport through a ``POST_PIXEL`` handler (``screen.screenshot`` is unreliable)
and ends by quitting Blender. A failed step prints the traceback and quits with exit code 1,
so no window is ever left open.
"""
from __future__ import annotations

import os
import sys
import traceback

import numpy as np

import bpy
import gpu

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.dirname(TESTS_DIR)
for _p in (ADDON_DIR, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_cap = {"want": False, "img": None, "err": None}


def out_dir() -> str:
    """The directory after ``--`` on the command line (created), else ``./gui-out``."""
    argv = sys.argv
    path = argv[argv.index("--") + 1] if "--" in argv and argv.index("--") + 1 < len(argv) else "gui-out"
    os.makedirs(path, exist_ok=True)
    return path


def find_view3d():
    """(window, area, region, space) of the first 3D viewport's main region."""
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                region = next(r for r in area.regions if r.type == "WINDOW")
                return win, area, region, area.spaces.active
    raise RuntimeError("no 3D viewport")


def set_view(location, rotation_euler_deg, distance) -> None:
    """Orbit the viewport camera: perspective, looking at ``location`` from ``distance``."""
    import math
    from mathutils import Euler
    _, _, _, space = find_view3d()
    r3d = space.region_3d
    r3d.view_perspective = "PERSP"
    r3d.view_location = location
    r3d.view_rotation = Euler([math.radians(a) for a in rotation_euler_deg], "XYZ").to_quaternion()
    r3d.view_distance = distance


def hide_ui_clutter() -> None:
    """Hide the cursor, relationship lines and the default overlay text so screenshots stay clean."""
    _, _, _, space = find_view3d()
    space.overlay.show_cursor = False
    space.overlay.show_text = False
    space.overlay.show_stats = False
    space.overlay.show_extras = False


def _post_pixel() -> None:
    if not _cap["want"]:
        return
    try:
        x, y, w, h = gpu.state.viewport_get()
        buf = gpu.state.active_framebuffer_get().read_color(x, y, w, h, 4, 0, "UBYTE")
        flat = np.asarray(memoryview(buf)).T.reshape(-1)
        _cap["img"] = flat.reshape(h, w, 4).copy()
    except Exception:  # noqa: BLE001
        _cap["err"] = traceback.format_exc()


_handle = None


def capture(path: str) -> np.ndarray:
    """Redraw the viewport and save its pixels (rows bottom-up, as Blender stores images) as a PNG."""
    global _handle
    win, area, region, _ = find_view3d()
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_post_pixel, (), "WINDOW", "POST_PIXEL")
    _cap.update(want=True, img=None, err=None)
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    _cap["want"] = False
    if _cap["img"] is None:
        raise RuntimeError(f"capture failed: {_cap['err']}")
    img = _cap["img"]
    image = bpy.data.images.new("slicewright_capture", img.shape[1], img.shape[0], alpha=True)
    image.pixels.foreach_set((img.astype(np.float32) / 255.0).ravel())
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    return img


def drive(steps) -> None:
    """Run the generator ``steps()`` (yielding seconds to wait) from a timer, then quit Blender."""
    gen = steps()

    def tick():
        try:
            return float(next(gen))
        except StopIteration:
            print("GUI SMOKE OK", flush=True)
            bpy.ops.wm.quit_blender()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            print("GUI SMOKE FAILED", flush=True)
            sys.stdout.flush()
            os._exit(1)
        return None

    bpy.app.timers.register(tick, first_interval=1.0)
