# SPDX-License-Identifier: GPL-3.0-or-later
"""The procedural bed as a ``POST_VIEW`` draw handler (03 section 1.3).

Geometry comes from ``core.bed`` in millimetres; the handler scales the model-view matrix by
1 / mm_per_BU, so a change of scene units never rebuilds a batch. Batches are built lazily on
the first draw (a GPU context is needed) and rebuilt when the bed values change.
"""
from __future__ import annotations

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from .. import prefs
from ..core import logs, preview_palette
from ..core.bed import build_geometry, build_plate
from . import bed_source
from .units import scene_mm_per_bu

# (geometry key, RGBA). Colours are plain data.
STYLE = (
    ("grid", (0.55, 0.55, 0.55, 0.35)),
    ("outline", (0.9, 0.9, 0.9, 1.0)),
    ("volume", (0.45, 0.65, 0.9, 0.45)),
    ("exclude_hatch", (0.85, 0.15, 0.15, 0.55)),
    ("exclude_outline", (0.95, 0.2, 0.2, 1.0)),
    ("axis_x", (0.95, 0.2, 0.2, 1.0)),
    ("axis_y", (0.3, 0.85, 0.3, 1.0)),
    ("axis_z", (0.3, 0.45, 0.95, 1.0)),
)

# The build plate (sRGB hex, uploaded scene-linear like the preview palette: the viewport's view transform encodes it).
PLATE_TOP, PLATE_SIDE, PLATE_BOTTOM = 0x55585E, 0x3A3C40, 0x2A2C2F

_handle = None
_cache: dict = {"key": None, "batches": [], "plate": [], "shader": None}
_reported = False


def _build_batches(bed) -> list:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    geometry = build_geometry(bed)
    batches = []
    for name, color in STYLE:
        pts = geometry[name]
        if len(pts):
            batches.append((batch_for_shader(shader, "LINES", {"pos": pts}), color))
    plate = build_plate(bed)
    plate_batches = []
    for name, hexcolor in (("top", PLATE_TOP), ("sides", PLATE_SIDE), ("bottom", PLATE_BOTTOM)):
        if len(plate[name]):
            plate_batches.append((batch_for_shader(shader, "TRIS", {"pos": plate[name]}),
                                  (*preview_palette.rgb_linear(hexcolor), 1.0)))
    _cache.update(key=bed.key, batches=batches, plate=plate_batches, shader=shader)
    return batches


def draw() -> None:
    """The ``POST_VIEW`` callback. Logs a failure once per session and never raises."""
    global _reported
    if bpy.app.background:      # no GPU context to draw into
        return
    try:
        scene = bpy.context.scene
        bed = bed_source.current_bed(scene) if scene is not None else None
        if bed is None:
            return
        batches = _cache["batches"] if _cache["key"] == bed.key else _build_batches(bed)
        shader = _cache["shader"]
        gpu.matrix.push()
        gpu.matrix.scale_uniform(1.0 / scene_mm_per_bu(scene))
        try:
            shader.bind()
            if prefs.show_plate_mesh():
                # Opaque, depth tested and written, drawn first: it lies below Z = 0, so objects and toolpaths (above it)
                # are never hidden by it, while anything under the plate is. Back faces culled: one layer, any winding is
                # outward from the polygon (core.plate_mesh).
                gpu.state.blend_set("NONE")
                gpu.state.depth_test_set("LESS_EQUAL")
                gpu.state.depth_mask_set(True)
                gpu.state.face_culling_set("BACK")
                for batch, color in _cache["plate"]:
                    shader.uniform_float("color", color)
                    batch.draw(shader)
                gpu.state.face_culling_set("NONE")
            gpu.state.blend_set("ALPHA")
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(False)
            for batch, color in batches:
                shader.uniform_float("color", color)
                batch.draw(shader)
        finally:
            gpu.matrix.pop()
            gpu.state.face_culling_set("NONE")
            gpu.state.blend_set("NONE")
            gpu.state.depth_mask_set(True)
            gpu.state.depth_test_set("NONE")
    except Exception:  # noqa: BLE001 - a draw handler must never raise
        if not _reported:
            _reported = True
            logs.get_logger("bed_draw").exception("bed drawing failed (reported once)")


def register() -> None:
    global _handle, _reported
    _reported = False
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), "WINDOW", "POST_VIEW")


def unregister() -> None:
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    _cache.update(key=None, batches=[], plate=[], shader=None)


def is_registered() -> bool:
    return _handle is not None
