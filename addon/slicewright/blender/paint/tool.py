# SPDX-License-Identifier: GPL-3.0-or-later
"""The object-mode paint brush: a ``WorkSpaceTool`` starting a modal operator (03 section 5.2).

LMB paints, Shift (held when the stroke starts) erases, Esc or RMB cancels and restores what the
stroke wrote; one stroke is one undo step. A ``POST_PIXEL`` circle shows the brush size while
stroking.
"""
from __future__ import annotations

import math

import gpu
from gpu_extras.batch import batch_for_shader

import bpy
from bpy_extras import view3d_utils
from bpy.props import BoolProperty
from mathutils import Vector

from ...core import brush as core_brush
from ...core import logs
from ...names import OP_PREFIX
from .. import plate_collection
from ..units import scene_mm_per_bu
from . import brush

_PREFIX = OP_PREFIX.lower()
TOOL_ID = f"{_PREFIX}.paint_tool"
MIN_STEP_PX = 4.0           # a new dab needs the pointer to have moved this far


def region_ray(region, rv3d, coord):
    """The world-space ray ``(origin, direction)`` under region pixel ``coord`` of a 3D viewport."""
    return (view3d_utils.region_2d_to_origin_3d(region, rv3d, coord),
            view3d_utils.region_2d_to_vector_3d(region, rv3d, coord))


def dab_at(scene, stroke, region, rv3d, coord) -> float | None:
    """One dab of ``stroke`` under region pixel ``coord``. Returns the brush's on-screen radius in
    pixels, or None when the ray hit nothing. Everything the modal operator does per event, minus the event."""
    origin, direction = region_ray(region, rv3d, coord)
    hit = brush.raycast(scene, origin, direction)
    if hit is None:
        return None
    ob, face, loc, _dist = hit
    stroke.dab_faces(ob, brush.faces_in_brush(ob, face, loc, stroke.radius_bu, stroke.smart, stroke.angle_deg))
    right = rv3d.view_matrix.inverted().to_3x3() @ Vector((1.0, 0.0, 0.0))
    a = view3d_utils.location_3d_to_region_2d(region, rv3d, loc)
    b = view3d_utils.location_3d_to_region_2d(region, rv3d, loc + right * stroke.radius_bu)
    if a is None or b is None:
        return 24.0
    return max(4.0, (a - b).length)


class SLICEWRIGHT_OT_paint_brush(bpy.types.Operator):
    bl_idname = f"{_PREFIX}.paint_brush"
    bl_label = "Paint brush"
    bl_description = "Paint support, seam or filament onto plate objects. Shift erases; Esc cancels the stroke"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    erase: BoolProperty(name="Erase", default=False)

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == "VIEW_3D" and context.mode == "OBJECT"
                and plate_collection.plate_collection(context.scene) is not None)

    # -- stroke ------------------------------------------------------------------------------

    def invoke(self, context, event):
        props = context.scene.slicewright
        attr, value = core_brush.brush_target(props.brush_kind, props.paint_filament)
        self._radius_bu = props.brush_radius / scene_mm_per_bu(context.scene)
        self._stroke = brush.Stroke(attr, value, self._radius_bu, erase=self.erase or event.shift,
                                    smart=props.brush_smart, angle_deg=props.brush_angle)
        self._last = None
        self._mouse = (event.mouse_region_x, event.mouse_region_y)
        self._radius_px = 24.0
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw_circle, (), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        self._dab(context, event)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._stroke.cancel()
            self._finish(context)
            return {"CANCELLED"}
        if event.type == "MOUSEMOVE":
            self._mouse = (event.mouse_region_x, event.mouse_region_y)
            if self._last is None or math.dist(self._last, self._mouse) >= MIN_STEP_PX:
                self._dab(context, event)
            context.area.tag_redraw()
        elif event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._finish(context)
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        self._stroke.cancel()
        self._finish(context)

    def _finish(self, context):
        self._stroke.end()
        if getattr(self, "_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
            self._handle = None
        if context.area is not None:
            context.area.tag_redraw()

    def _dab(self, context, event):
        coord = (event.mouse_region_x, event.mouse_region_y)
        self._last = coord
        radius_px = dab_at(context.scene, self._stroke, context.region, context.region_data, coord)
        if radius_px is not None:
            self._radius_px = radius_px

    def _draw_circle(self):
        try:
            if bpy.app.background:
                return
            cx, cy = self._mouse
            pts = [(cx + math.cos(t) * self._radius_px, cy + math.sin(t) * self._radius_px)
                   for t in (i * math.tau / 48 for i in range(48))]
            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            batch = batch_for_shader(shader, "LINE_LOOP", {"pos": pts})
            gpu.state.blend_set("ALPHA")
            shader.bind()
            shader.uniform_float("color", (1.0, 1.0, 1.0, 0.9) if not self._stroke.erase else (1.0, 0.5, 0.2, 0.9))
            batch.draw(shader)
            gpu.state.blend_set("NONE")
        except Exception:  # noqa: BLE001 - a draw handler must never raise
            logs.get_logger("paint_brush").exception("brush cursor drawing failed")


class SLICEWRIGHT_TOOL_paint(bpy.types.WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = TOOL_ID
    bl_label = "Slicer Paint"
    bl_description = "Paint support, seam and filament regions on plate objects"
    bl_icon = "brush.paint_vertex.replace"
    bl_widget = None
    bl_keymap = ((f"{_PREFIX}.paint_brush", {"type": "LEFTMOUSE", "value": "PRESS"}, {"properties": []}),)

    def draw_settings(context, layout, tool):
        props = context.scene.slicewright
        layout.prop(props, "brush_kind", text="")
        if props.brush_kind == "FILAMENT":
            layout.prop(props, "paint_filament")
        layout.prop(props, "brush_radius")
        layout.prop(props, "brush_smart")
        if props.brush_smart:
            layout.prop(props, "brush_angle")


classes = (SLICEWRIGHT_OT_paint_brush,)


def register() -> None:
    bpy.utils.register_tool(SLICEWRIGHT_TOOL_paint, after={"builtin.cursor"}, separator=True)


def unregister() -> None:
    try:
        bpy.utils.unregister_tool(SLICEWRIGHT_TOOL_paint)
    except (RuntimeError, ValueError, KeyError):
        pass
