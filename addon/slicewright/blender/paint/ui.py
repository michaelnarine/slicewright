# SPDX-License-Identifier: GPL-3.0-or-later
"""The Paint panel in the "Slicer" tab (03 section 1.1)."""
from __future__ import annotations

import bpy

from ...names import OP_PREFIX, TAB_NAME

_PREFIX = OP_PREFIX.lower()


def _assign(layout, text, kind, value, icon="NONE"):
    op = layout.operator(f"{_PREFIX}.paint_assign", text=text, icon=icon)
    op.kind, op.value = kind, value


class SLICEWRIGHT_PT_paint(bpy.types.Panel):
    bl_idname = f"{OP_PREFIX}_PT_paint"
    bl_label = "Paint"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME

    def draw(self, context):
        layout = self.layout
        if context.mode != "EDIT_MESH":
            layout.label(text="Enter Edit Mode and select faces", icon="INFO")
            layout.operator(f"{_PREFIX}.paint_clear_all", icon="TRASH")
            return
        props = context.scene.slicewright
        layout.label(text="Select faces, then assign:")
        layout.operator(f"{_PREFIX}.select_overhangs", icon="FACESEL")
        for label, kind in (("Support", "SUPPORT"), ("Seam", "SEAM")):
            box = layout.box()
            box.label(text=label)
            row = box.row(align=True)
            _assign(row, "Enforce", kind, 1)
            _assign(row, "Block", kind, 2)
            _assign(row, "Clear", kind, 0)
        box = layout.box()
        box.label(text="Filament")
        box.prop(props, "paint_filament")
        row = box.row(align=True)
        op = row.operator(f"{_PREFIX}.paint_assign", text="Assign")
        op.kind, op.value = "FILAMENT", props.paint_filament
        _assign(row, "Clear", "FILAMENT", 0)


classes = (SLICEWRIGHT_PT_paint,)
