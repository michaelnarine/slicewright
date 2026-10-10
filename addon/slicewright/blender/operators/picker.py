# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators of the printer / process / filament picker (03 section 3.7)."""
from __future__ import annotations

import bpy
from bpy.props import IntProperty

from ...names import PACKAGE_ID
from .. import library, picker


class SLICEWRIGHT_OT_load_library(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.load_library"
    bl_label = "Load Printer Library"
    bl_description = "Build the printer and material index (or retry after an error)"

    def execute(self, context):
        library.request(force=True)
        return {"FINISHED"}


class SLICEWRIGHT_OT_filament_add(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.filament_add"
    bl_label = "Add Filament"
    bl_description = "Add a filament slot"

    @classmethod
    def poll(cls, context):
        return library.get() is not None and len(context.scene.slicewright.filaments) < 16

    def execute(self, context):
        picker.add_filament(context.scene.slicewright)
        return {"FINISHED"}


class SLICEWRIGHT_OT_filament_remove(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.filament_remove"
    bl_label = "Remove Filament"
    bl_description = "Remove this filament slot"

    index: IntProperty(default=-1, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return len(context.scene.slicewright.filaments) > 1

    def execute(self, context):
        filaments = context.scene.slicewright.filaments
        if not 0 <= self.index < len(filaments):
            return {"CANCELLED"}
        filaments.remove(self.index)
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_load_library, SLICEWRIGHT_OT_filament_add, SLICEWRIGHT_OT_filament_remove)
