# SPDX-License-Identifier: GPL-3.0-or-later
"""Live out-of-volume checks and red outlines in Prepare mode (03 section 4.3).

A ``depsgraph_update_post`` handler marks the result dirty and recomputes at most every
200 ms; if an update arrives inside that window a one-shot timer runs it afterwards, so the
final state is never missed. Results and boxes live in this module's runtime state, not in RNA.
"""
from __future__ import annotations

import time

import numpy as np

import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader

from ..core import logs
from ..core.checks import Throttle, out_of_volume
from . import bed_source, meshdata, registry
from .units import scene_mm_per_bu

INTERVAL_S = 0.2
RED = (0.95, 0.15, 0.15, 1.0)
_EDGES = ((0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6), (6, 4), (0, 4), (1, 5), (2, 6), (3, 7))

throttle = Throttle(INTERVAL_S, time.monotonic)
# owner name_full -> {"reasons": [...], "box": (2, 3) float32 min/max mm}
flagged: dict[str, dict] = {}
_dirty = {"value": True}
_draw_handle = None
_reported = False


def reset() -> None:
    flagged.clear()
    _dirty["value"] = True


def compute(context) -> dict[str, dict]:
    """Recompute ``flagged`` for the plate now (no throttling). Returns it."""
    scene = context.scene
    bed = bed_source.current_bed(scene)
    flagged.clear()
    if bed is not None:
        for name, verts in meshdata.plate_vertices_mm(context).items():
            reasons = out_of_volume(verts, bed)
            if reasons:
                flagged[name] = {"reasons": reasons, "box": np.stack([verts.min(axis=0), verts.max(axis=0)])}
    _dirty["value"] = False
    throttle.mark()
    return flagged


def recompute_now(context) -> dict[str, dict]:
    """Recompute immediately (operators call this after moving objects) and redraw."""
    result = compute(context)
    _tag_redraw()
    return result


def refresh(context=None) -> bool:
    """Recompute if dirty and the throttle allows; otherwise arm a timer. True if it recomputed."""
    context = context or bpy.context
    scene = context.scene
    if scene is None or not hasattr(scene, "slicewright") or scene.slicewright.mode != "PREPARE":
        return False
    if not _dirty["value"]:
        return False
    if not throttle.ready():
        registry.register_timer(_deferred, first_interval=max(throttle.remaining(), 0.01))
        return False
    compute(context)
    _tag_redraw()
    return True


def _deferred() -> None:
    refresh()
    return None


def _tag_redraw() -> None:
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else ()):
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


@persistent
def _on_depsgraph_update(scene, depsgraph=None) -> None:
    _dirty["value"] = True
    try:
        refresh()
    except Exception:  # noqa: BLE001 - never break the user's edit
        logs.get_logger("volume").exception("out-of-volume check failed")


@persistent
def _on_load_post(*_args) -> None:
    reset()


def _box_lines(box: np.ndarray) -> np.ndarray:
    lo, hi = box
    corners = np.array([[x, y, z] for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])],
                       np.float32)
    return corners[np.array(_EDGES).ravel()]


def draw() -> None:
    """``POST_VIEW``: red wireframe boxes around out-of-volume plate objects."""
    global _reported
    if bpy.app.background or not flagged:
        return
    try:
        scene = bpy.context.scene
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        pts = np.concatenate([_box_lines(v["box"]) for v in flagged.values()])
        batch = batch_for_shader(shader, "LINES", {"pos": pts})
        gpu.state.depth_test_set("NONE")
        gpu.matrix.push()
        gpu.matrix.scale_uniform(1.0 / scene_mm_per_bu(scene))
        try:
            shader.bind()
            shader.uniform_float("color", RED)
            batch.draw(shader)
        finally:
            gpu.matrix.pop()
    except Exception:  # noqa: BLE001
        if not _reported:
            _reported = True
            logs.get_logger("volume").exception("out-of-volume drawing failed (reported once)")


def register() -> None:
    global _draw_handle, _reported
    _reported = False
    reset()
    for handlers, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
                         (bpy.app.handlers.load_post, _on_load_post)):
        if fn not in handlers:
            handlers.append(fn)
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), "WINDOW", "POST_VIEW")


def unregister() -> None:
    global _draw_handle
    for handlers, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
                         (bpy.app.handlers.load_post, _on_load_post)):
        while fn in handlers:
            handlers.remove(fn)
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None
    reset()


def is_registered() -> bool:
    return _draw_handle is not None
