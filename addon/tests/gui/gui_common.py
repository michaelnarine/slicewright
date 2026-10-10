# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for GUI scripts, run in a real (or xvfb) window: ``-b`` cannot create GPU shaders.

Two styles live here, and neither leaves a window open:

- preview scripts (``Blender --factory-startup --python gui_x.py -- --out DIR``): :func:`run` /
  :func:`run_steps` drive the work from a timer, record :func:`check` results to ``results.json``
  and exit through :func:`finish`;
- smoke scripts (``... --python script.py -- <out_dir>``): :func:`drive` runs a generator of waits,
  :func:`capture` saves the viewport, and a failed step quits with exit code 1.

Not importable outside Blender.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

import numpy as np

import bpy
import gpu

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


# --------------------------------------------------------------------------- rendering helpers

def buf_to_np(buf):
    """Raw memory of a ``gpu.types.Buffer`` as a flat numpy array (zero-copy).

    Multi-dimensional Buffers report reversed strides (03 7.1): the memory is plain row-major
    interleaved, so the transposed view walks it linearly."""
    import numpy as np
    a = np.asarray(memoryview(buf))
    return a.T.reshape(-1) if a.ndim > 1 else a


def read_rgba(fb, w: int, h: int):
    return buf_to_np(fb.read_color(0, 0, w, h, 4, 0, 'UBYTE')).reshape(h, w, 4).copy()


def write_png(path: str, rgba, flip: bool = True) -> None:
    from fake_engine.result import png_bytes
    import numpy as np
    a = np.ascontiguousarray(rgba[::-1] if flip else rgba)
    with open(path, "wb") as f:
        f.write(png_bytes(a))


def offscreen(w: int, h: int, draw, clear=(0.04, 0.04, 0.06, 1.0)):
    """Bind an offscreen buffer, clear it, call ``draw()`` and return the RGBA pixels."""
    import gpu
    off = gpu.types.GPUOffScreen(w, h)
    try:
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=clear, depth=1.0)
            draw()
            return read_rgba(fb, w, h)
    finally:
        off.free()


def look_at(eye, target, up=(0, 0, 1)):
    from mathutils import Matrix, Vector
    eye, target, up = Vector(eye), Vector(target), Vector(up)
    f = (target - eye).normalized()
    s = f.cross(up).normalized()
    u = s.cross(f)
    return Matrix(((s.x, s.y, s.z, -s.dot(eye)), (u.x, u.y, u.z, -u.dot(eye)),
                   (-f.x, -f.y, -f.z, f.dot(eye)), (0, 0, 0, 1)))


def perspective(fov_deg: float, aspect: float, near: float, far: float):
    import math
    from mathutils import Matrix
    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    return Matrix(((f / aspect, 0, 0, 0), (0, f, 0, 0),
                   (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)), (0, 0, -1, 0)))


def ortho(cx: float, cy: float, half_w: float, half_h: float, near=-1000.0, far=1000.0):
    from mathutils import Matrix
    return Matrix(((1 / half_w, 0, 0, -cx / half_w), (0, 1 / half_h, 0, -cy / half_h),
                   (0, 0, -2 / (far - near), -(far + near) / (far - near)), (0, 0, 0, 1)))


def make_result(n_moves: int, name: str = "large"):
    """``from_gcode`` of a generated ``n_moves`` print (cached next to the output)."""
    sys.path.insert(0, os.path.join(TESTS_DIR, "fixtures", "gcode"))
    import make_large
    from fake_engine.gcode import from_gcode
    path = os.path.join(OUT, f"{name}_{n_moves}.gcode")
    t0 = time.perf_counter()
    make_large.write(path, n_moves)
    t1 = time.perf_counter()
    result = from_gcode(path)
    log(f"generated {n_moves} moves in {t1 - t0:.1f}s, from_gcode {time.perf_counter() - t1:.1f}s")
    os.remove(path)
    return result


def run_steps(steps, delay: float = 1.0) -> None:
    """Like :func:`run`, but ``steps()`` is a generator: each ``yield seconds`` hands control back
    to Blender's event loop (so real timers, redraws and the add-on's tick timer run) for that long."""
    import bpy
    gen = steps()
    t0 = time.perf_counter()

    def go():
        try:
            return next(gen)
        except StopIteration:
            pass
        except Exception:  # noqa: BLE001
            RESULTS["errors"].append(traceback.format_exc())
            log("ERROR", RESULTS["errors"][-1])
        RESULTS["info"]["seconds"] = round(time.perf_counter() - t0, 2)
        finish()

    bpy.app.timers.register(go, first_interval=delay)


class ViewportCapture:
    """Screenshots of the 3D viewport region through a ``POST_PIXEL`` handler (``screen.screenshot``
    is black on GL under xvfb, and ``draw_view3d`` offscreen does not run ``POST_VIEW`` handlers)."""

    def __init__(self) -> None:
        import bpy
        self.path = None
        self.last = None
        self.handle = bpy.types.SpaceView3D.draw_handler_add(self._cb, (), 'WINDOW', 'POST_PIXEL')

    def _cb(self) -> None:
        if self.path is None:
            return
        import bpy
        import gpu
        x, y, w, h = gpu.state.viewport_get()
        fb = gpu.state.active_framebuffer_get()
        self.last = buf_to_np(fb.read_color(x, y, w, h, 4, 0, 'UBYTE')).reshape(h, w, 4).copy()
        write_png(self.path, self.last)
        self.path = None

    def shot(self, path: str) -> bool:
        """Draw one frame synchronously and save the region; False if the handler did not run."""
        import bpy
        self.path = path
        win, area, region, _space = view3d()
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        ok, self.path = self.path is None, None
        return ok

    def close(self) -> None:
        import bpy
        bpy.types.SpaceView3D.draw_handler_remove(self.handle, 'WINDOW')


def view3d():
    """(window, area, region, space) of the first 3D viewport."""
    import bpy
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    return win, area, next(r for r in area.regions if r.type == 'WINDOW'), area.spaces.active


def frame_part(distance: float = 190.0, z: float = 2.0, azimuth: float = 0.55, elevation: float = 1.0) -> None:
    """Point the viewport at the origin from above-front and hide the startup objects/overlays."""
    import bpy
    from mathutils import Euler, Vector
    for ob in list(bpy.context.scene.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    win, area, region, space = view3d()
    r3d = space.region_3d
    r3d.view_perspective = 'PERSP'
    r3d.view_location = Vector((0, 0, z))
    r3d.view_distance = distance
    r3d.view_rotation = Euler((elevation, 0.0, azimuth), 'XYZ').to_quaternion()
    space.overlay.show_overlays = False
    space.show_gizmo = False
    space.shading.type = 'SOLID'
    space.shading.background_type = 'VIEWPORT'
    space.shading.background_color = (0.04, 0.04, 0.06)
    area.tag_redraw()


# --------------------------------------------------------------------------- smoke-script helpers

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
    img[..., 3] = 255      # the region framebuffer can carry alpha < 1; opaque PNGs read better
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
