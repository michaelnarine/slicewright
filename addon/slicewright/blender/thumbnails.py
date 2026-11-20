# SPDX-License-Identifier: GPL-3.0-or-later
"""Renders the plate thumbnails (03 section 6.2): a ``GPUOffScreen`` per size, the plate's own triangles drawn
from the fixed isometric view of ``core/thumbnails.py``, flat-shaded in the filament colours on a transparent
background, read back into the engine's RGBA layout.

The doc names ``GPUOffScreen.draw_view3d``; that draws whatever the viewport shows (other objects in the scene,
hidden-by-collection quirks) and needs a 3D view region, which a timer or script has not got. The plate is already
extracted as bed-frame mm triangles for the engine, so those are drawn directly: exactly what is sliced, no viewport
needed. Background mode has no GPU: :class:`~slicewright.core.thumbnails.Skipped` says so.
"""
from __future__ import annotations

import logging

import bpy
import numpy as np

from ..core import thumbnails as core

log = logging.getLogger("slicewright.thumbnails")
BACKGROUND_REASON = "Thumbnails are not rendered in background mode (no GPU)"


def plate_meshes(request) -> list:
    """``(vertices, triangles, rgb)`` of each plate object, coloured by its filament (0 inherits slot 1)."""
    out = []
    for obj in request.objects:
        slot = max(0, int(obj.extruder) - 1)
        filament = request.filaments[slot] if slot < len(request.filaments) else {}
        out.append((obj.vertices, obj.triangles, core.parse_colour(filament.get("filament_colour"))))
    return out


def render(width: int, height: int, positions: np.ndarray, colours: np.ndarray, view: np.ndarray,
           proj: np.ndarray) -> np.ndarray:
    """Draw the triangles into an offscreen buffer and return its pixels (bottom row first, RGBA uint8)."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    from mathutils import Matrix

    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
            gpu.state.viewport_set(0, 0, width, height)
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(True)
            shader = gpu.shader.from_builtin("SMOOTH_COLOR")
            batch = batch_for_shader(shader, "TRIS", {"pos": positions, "color": colours})
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_matrix(Matrix(view.tolist()))
                gpu.matrix.load_projection_matrix(Matrix(proj.tolist()))
                shader.bind()
                batch.draw(shader)
            gpu.state.depth_test_set("NONE")
            buf = fb.read_color(0, 0, width, height, 4, 0, "UBYTE")
            raw = np.asarray(memoryview(buf))
            return (raw.T.reshape(-1) if raw.ndim > 1 else raw).copy()   # multi-dimensional Buffers report reversed strides
    finally:
        offscreen.free()


def render_plate(meshes: list, sizes) -> list[np.ndarray]:
    """One engine image per size for ``meshes`` (see :func:`plate_meshes`)."""
    if bpy.app.background:
        raise core.Skipped(BACKGROUND_REASON)
    positions, colours = core.shaded_triangles(meshes)
    box = core.bounds([m[0] for m in meshes])
    if box is None or not len(positions):
        return []
    lo, hi = box

    def draw(w: int, h: int) -> np.ndarray:
        view, proj = core.iso_matrices(lo, hi, w / h)
        return render(w, h, positions, colours, view, proj)

    return core.make_images(sizes, draw)


def source_for(request):
    """The ``SliceRequest.thumbnail_source`` of ``request``: called by the runner with the sizes the printer asks for,
    only when the engine is really going to run."""
    return lambda sizes: render_plate(plate_meshes(request), sizes)
