# SPDX-License-Identifier: GPL-3.0-or-later
"""The "Ghost" look of the Preview option "Model": the plate's source objects drawn faintly behind the toolpaths.

The viewport's own drawing of the objects is switched off (``blender/model_visibility.py``), so this draws their
evaluated triangles itself, in the world frame, as one translucent batch per scene: back faces culled (one layer, so a
box does not look twice as dense), depth tested against the scene but never written, so the ghost cannot hide a
path. The batch is rebuilt when the depsgraph reports a change to an object.
"""
from __future__ import annotations

import numpy as np

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ...core import preview_palette as palette
from .. import meshdata

GHOST_RGB = 0x9FB4CC                 # an sRGB colour, uploaded scene-linear (see core.preview_palette.rgb_linear)
GHOST_ALPHA = 0.16

_cache: dict[str, object] = {}       # scene name -> batch (or None when the plate has no triangles)
_dirty: set[str] = set()


def release() -> None:
    _cache.clear()
    _dirty.clear()


def invalidate(scene=None) -> None:
    if scene is None:
        _dirty.update(_cache)
    else:
        _dirty.add(scene.name)


def world_triangles(depsgraph, scene) -> np.ndarray:
    """Evaluated plate triangles in world coordinates (Blender units), float32 ``(3 * n_tris, 3)``."""
    chunks = []
    for _owner, inst in meshdata.plate_instances(depsgraph, scene):
        me = inst.object.to_mesh()
        try:
            me.calc_loop_triangles()
            nv, nt = len(me.vertices), len(me.loop_triangles)
            if not nt:
                continue
            co = np.empty(nv * 3, np.float32)
            me.vertices.foreach_get("co", co)
            tri = np.empty(nt * 3, np.int32)
            me.loop_triangles.foreach_get("vertices", tri)
        finally:
            inst.object.to_mesh_clear()
        m = np.array(inst.matrix_world, np.float64)
        world = co.reshape(-1, 3).astype(np.float64) @ m[:3, :3].T + m[:3, 3]
        tris = tri.reshape(-1, 3)
        if np.linalg.det(m[:3, :3]) < 0:              # a mirrored object flips the winding: keep the front faces front
            tris = tris[:, ::-1]
        chunks.append(world[tris.ravel()].astype(np.float32))
    return np.concatenate(chunks) if chunks else np.zeros((0, 3), np.float32)


def draw(context) -> None:
    scene = context.scene
    if scene.name not in _cache or scene.name in _dirty:
        pos = world_triangles(context.evaluated_depsgraph_get(), scene)
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        _cache[scene.name] = batch_for_shader(shader, "TRIS", {"pos": pos}) if len(pos) else None
        _dirty.discard(scene.name)
    batch = _cache[scene.name]
    if batch is None:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    blend = gpu.state.blend_get()
    test, mask = gpu.state.depth_test_get(), gpu.state.depth_mask_get()
    gpu.state.blend_set("ALPHA")
    gpu.state.face_culling_set("BACK")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    try:
        shader.bind()
        shader.uniform_float("color", (*palette.rgb_linear(GHOST_RGB), GHOST_ALPHA))
        batch.draw(shader)
    finally:
        gpu.state.blend_set(blend)
        gpu.state.face_culling_set("NONE")
        gpu.state.depth_test_set(test)
        gpu.state.depth_mask_set(mask)
