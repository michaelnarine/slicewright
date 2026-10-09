# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit-mode Select + Assign paint (03 section 5.2, the baseline input method)."""
from __future__ import annotations

import bmesh
import numpy as np

import bpy
from bpy.props import EnumProperty, IntProperty

from ...core import paint as core_paint
from ...names import OP_PREFIX
from . import attributes

_PREFIX = OP_PREFIX.lower()

KINDS = (("SUPPORT", "Support", "Support enforcer or blocker", core_paint.ATTR_SUPPORT),
         ("SEAM", "Seam", "Seam enforcer or blocker", core_paint.ATTR_SEAM),
         ("FILAMENT", "Filament", "Filament region", core_paint.ATTR_FILAMENT))
_ATTR_OF = {k[0]: k[3] for k in KINDS}


class _EditMeshOperator:
    @classmethod
    def poll(cls, context):
        ob = context.edit_object
        return ob is not None and ob.type == "MESH" and context.mode == "EDIT_MESH"


class SLICEWRIGHT_OT_paint_assign(_EditMeshOperator, bpy.types.Operator):
    bl_idname = f"{_PREFIX}.paint_assign"
    bl_label = "Assign paint"
    bl_description = ("Write a paint value to the selected faces: support or seam 1 = enforce, 2 = block, "
                      "0 = clear; filament 1..16, 0 = object default")
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="Kind", items=[(k[0], k[1], k[2]) for k in KINDS], default="SUPPORT")
    value: IntProperty(name="Value", default=1, min=0, max=core_paint.MAX_FILAMENT)

    def execute(self, context):
        if self.kind != "FILAMENT" and self.value > core_paint.BLOCK:
            self.report({"ERROR"}, "Support and seam values are 0, 1 or 2")
            return {"CANCELLED"}
        painted = 0
        for ob in context.objects_in_mode_unique_data:
            if ob.type != "MESH":
                continue
            bm = bmesh.from_edit_mesh(ob.data)
            painted += attributes.assign_selected(bm, _ATTR_OF[self.kind], self.value)
            bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        if not painted:
            self.report({"WARNING"}, "No faces selected")
            return {"CANCELLED"}
        return {"FINISHED"}


class SLICEWRIGHT_OT_select_overhangs(_EditMeshOperator, bpy.types.Operator):
    bl_idname = f"{_PREFIX}.select_overhangs"
    bl_label = "Select overhangs"
    bl_description = "Select faces pointing downward within the angle of straight down (world space)"
    bl_options = {"REGISTER", "UNDO"}

    angle: bpy.props.FloatProperty(
        name="Angle", description="Faces whose normal is within this angle of straight down",
        default=core_paint.DEFAULT_OVERHANG_ANGLE, min=0.0, max=89.9, subtype="ANGLE", unit="ROTATION")

    def execute(self, context):
        import math
        total = 0
        for ob in context.objects_in_mode_unique_data:
            bm = bmesh.from_edit_mesh(ob.data)
            bm.faces.ensure_lookup_table()
            if not bm.faces:
                continue
            normals = np.array([f.normal[:] for f in bm.faces], np.float64)
            world = core_paint.world_normals(normals, ob.matrix_world)
            mask = core_paint.overhang_mask(world, math.degrees(self.angle))
            bm.select_mode = {"FACE"}
            for face, hit in zip(bm.faces, mask):
                face.select_set(bool(hit))
            bm.select_flush_mode()
            bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
            total += int(mask.sum())
        self.report({"INFO"}, f"{total} overhanging face(s) selected")
        return {"FINISHED"}


class SLICEWRIGHT_OT_paint_clear_all(bpy.types.Operator):
    bl_idname = f"{_PREFIX}.paint_clear_all"
    bl_label = "Clear all paint"
    bl_description = "Remove the support, seam and filament paint attributes from the active mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == "MESH" and context.mode == "OBJECT"

    def execute(self, context):
        attributes.clear_all(context.active_object.data)
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_paint_assign, SLICEWRIGHT_OT_select_overhangs, SLICEWRIGHT_OT_paint_clear_all)
