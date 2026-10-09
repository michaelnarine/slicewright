# SPDX-License-Identifier: GPL-3.0-or-later
"""Plate operators: units now, the plate collection and placement in the next layers."""
from __future__ import annotations

import bpy

from ...names import OP_PREFIX
from .. import units


class SLICEWRIGHT_OT_use_mm_scene(bpy.types.Operator):
    bl_idname = f"{OP_PREFIX.lower()}.use_mm_scene"
    bl_label = "Use millimetre scene"
    bl_description = ("Set the scene to millimetres (unit scale 0.001, one Blender unit per mm) "
                      "and adjust the 3D views' grid and clip distances")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        units.use_millimetre_scene(context.scene, context.screen)
        self.report({"INFO"}, "Scene set to millimetres")
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_use_mm_scene,)
