# SPDX-License-Identifier: GPL-3.0-or-later
"""The paint overlay: painted faces only, as a ``POST_VIEW`` handler (03 section 5.3).

Colours: support enforcer green, blocker red, seam enforce cyan, seam block magenta, filament
regions in slot colours. A clip-space depth bias in the vertex shader replaces polygon offset,
which ``gpu.state`` lacks. Data rebuilds on geometry or transform updates, debounced to 100 ms.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

from ...core import logs
from ...core import paint as core_paint
from ...core.checks import Throttle
from .. import meshdata, plate_collection, registry
from . import attributes

DEBOUNCE_S = 0.1
DEPTH_BIAS = 1e-4

VERT_SRC = """
void main() {
  v_color = color;
  gl_Position = mvp * vec4(pos, 1.0);
  gl_Position.z -= %s * gl_Position.w;
}
""" % repr(DEPTH_BIAS)
FRAG_SRC = "void main() { fragColor = v_color; }"


@dataclass
class Entry:
    """Painted triangles of one evaluated instance, in object space, plus its world matrix."""
    name: str
    matrix: np.ndarray                      # (4, 4) float64
    positions: np.ndarray                   # (3k, 3) float32
    colors: np.ndarray                      # (3k, 4) float32
    batch: object = field(default=None, repr=False)

    @property
    def triangle_count(self) -> int:
        return len(self.positions) // 3


throttle = Throttle(DEBOUNCE_S, time.monotonic)
entries: list[Entry] = []
_dirty = {"value": True}
_state = {"handle": None, "shader": None, "reported": False}


def build_entries(context) -> list[Entry]:
    """CPU side: painted triangles for every plate instance (needs no GPU context)."""
    out: list[Entry] = []
    for owner, inst in meshdata.plate_instances(context.evaluated_depsgraph_get(), context.scene):
        ob = inst.object
        me = ob.to_mesh()
        try:
            support = attributes.read(me, core_paint.ATTR_SUPPORT)
            seam = attributes.read(me, core_paint.ATTR_SEAM)
            filament = attributes.read(me, core_paint.ATTR_FILAMENT)
            if support is None and seam is None and filament is None:
                continue
            n = len(me.loop_triangles)
            if n == 0:
                continue
            tri = np.empty(n * 3, np.int32)
            me.loop_triangles.foreach_get("vertices", tri)
            poly = np.empty(n, np.int32)
            me.loop_triangles.foreach_get("polygon_index", poly)
            co = np.empty(len(me.vertices) * 3, np.float32)
            me.vertices.foreach_get("co", co)
            pos, col = core_paint.triangle_overlay(co, tri, poly,
                                                   core_paint.face_colors(support, seam, filament))
            if len(pos):
                out.append(Entry(owner.name_full, np.array(inst.matrix_world, np.float64), pos, col))
        finally:
            ob.to_mesh_clear()
    return out


def _shader():
    if _state["shader"] is None:
        iface = gpu.types.GPUStageInterfaceInfo("slicewright_paint_iface")
        iface.smooth("VEC4", "v_color")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant("MAT4", "mvp")
        info.vertex_in(0, "VEC3", "pos")
        info.vertex_in(1, "VEC4", "color")
        info.vertex_out(iface)
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source(VERT_SRC)
        info.fragment_source(FRAG_SRC)
        _state["shader"] = gpu.shader.create_from_info(info)
    return _state["shader"]


def _upload(items: list[Entry]) -> None:
    shader = _shader()
    for e in items:
        e.batch = batch_for_shader(shader, "TRIS", {"pos": e.positions, "color": e.colors})


def mark_dirty() -> None:
    _dirty["value"] = True


def reset() -> None:
    entries.clear()
    _dirty["value"] = True
    _state.update(shader=None, reported=False)


def _tag_redraw() -> None:
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else ()):
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _deferred_redraw():
    _tag_redraw()
    return None


def refresh(context, upload: bool = True) -> bool:
    """Rebuild now if dirty and the debounce allows; otherwise arm a redraw timer. True if rebuilt."""
    if not _dirty["value"]:
        return False
    if not throttle.ready():
        registry.register_timer(_deferred_redraw, first_interval=max(throttle.remaining(), 0.01))
        return False
    items = build_entries(context)
    if upload:
        _upload(items)
    entries[:] = items
    _dirty["value"] = False
    throttle.mark()
    return True


def _visible(scene) -> bool:
    props = getattr(scene, "slicewright", None)
    return props is not None and props.show_paint_overlay and props.mode == "PREPARE"


def draw() -> None:
    """``POST_VIEW``: draw the painted triangles with alpha blending and a depth bias."""
    if bpy.app.background:
        return
    try:
        context = bpy.context
        if not _visible(context.scene) or plate_collection.plate_collection(context.scene) is None:
            return
        refresh(context)
        if not entries:
            return
        shader = _shader()
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(False)
        shader.bind()
        try:
            for e in entries:
                gpu.matrix.push()
                gpu.matrix.multiply_matrix(Matrix(e.matrix.tolist()))
                shader.uniform_float("mvp", gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
                e.batch.draw(shader)
                gpu.matrix.pop()
        finally:
            gpu.state.blend_set("NONE")
            gpu.state.depth_mask_set(True)
            gpu.state.depth_test_set("NONE")
    except Exception:  # noqa: BLE001 - a draw handler must never raise
        if not _state["reported"]:
            _state["reported"] = True
            logs.get_logger("paint_overlay").exception("paint overlay failed (reported once)")


@persistent
def _on_depsgraph_update(scene, depsgraph=None) -> None:
    if depsgraph is None:
        mark_dirty()
        return
    for update in depsgraph.updates:
        if update.is_updated_geometry or update.is_updated_transform:
            mark_dirty()
            _tag_redraw()
            return


@persistent
def _on_load_post(*_args) -> None:
    reset()


def register() -> None:
    reset()
    for handlers, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
                         (bpy.app.handlers.load_post, _on_load_post)):
        if fn not in handlers:
            handlers.append(fn)
    if _state["handle"] is None:
        _state["handle"] = bpy.types.SpaceView3D.draw_handler_add(draw, (), "WINDOW", "POST_VIEW")


def unregister() -> None:
    for handlers, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
                         (bpy.app.handlers.load_post, _on_load_post)):
        while fn in handlers:
            handlers.remove(fn)
    if _state["handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_state["handle"], "WINDOW")
        _state["handle"] = None
    reset()


def is_registered() -> bool:
    return _state["handle"] is not None
