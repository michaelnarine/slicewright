# SPDX-License-Identifier: GPL-3.0-or-later
"""The Plate panel in the "Slicer" tab (03 sections 1.1 and 1.4)."""
from __future__ import annotations

import bpy

from ...core import units as core_units
from ...names import OP_PREFIX, TAB_NAME
from .. import bed_source, volume
from ..units import scene_mm_per_bu
from ..labels import draw_wrapped


class SLICEWRIGHT_PT_plate(bpy.types.Panel):
    bl_idname = f"{OP_PREFIX}_PT_plate"
    bl_label = "Plate"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        props = scene.slicewright
        bed = bed_source.current_bed(scene)
        mm_bu = scene_mm_per_bu(scene)
        col = layout.column(align=True)
        if bed is None:
            col.alert = True
            col.label(text="printable_area is not usable", icon="ERROR")
            col.label(text="Fix it in the printer settings")
        else:
            x0, y0, x1, y1 = bed.bounds
            col.label(text=f"Bed {x1 - x0:g} x {y1 - y0:g} mm, up to {bed.height:g} mm")
            if not props.printer_id:
                col.label(text="No printer selected: engine default bed", icon="INFO")
            col.label(text=f"1 Blender unit = {mm_bu:g} mm")
            notice = core_units.bed_scale_notice(bed.extent_mm, mm_bu)
            if notice:
                box = layout.box()
                box.label(text="Scale notice", icon="INFO")
                col = box.column(align=True)
                draw_wrapped(col, notice, indent=2)
                box.operator(f"{OP_PREFIX.lower()}.use_mm_scene", icon="DRIVER_DISTANCE")
        col = layout.column(align=True)
        col.operator(f"{OP_PREFIX.lower()}.plate_add", icon="ADD")
        col.operator(f"{OP_PREFIX.lower()}.plate_remove", icon="REMOVE")
        row = layout.row(align=True)
        row.operator(f"{OP_PREFIX.lower()}.drop_to_bed", icon="TRIA_DOWN_BAR")
        row.operator(f"{OP_PREFIX.lower()}.center", icon="PIVOT_BOUNDBOX")
        layout.operator(f"{OP_PREFIX.lower()}.arrange", icon="MOD_ARRAY")
        flagged = volume.flagged if volume.shown(scene) else {}
        if flagged:
            box = layout.box()
            box.alert = True
            box.label(text="Outside the build volume", icon="ERROR")
            for name, info in flagged.items():
                draw_wrapped(box, f"{name.split(' (')[0]}: {', '.join(r.replace('_', ' ') for r in info['reasons'])}", indent=2)
        layout.operator(f"{OP_PREFIX.lower()}.check_plate", icon="CHECKMARK")


classes = (SLICEWRIGHT_PT_plate,)
