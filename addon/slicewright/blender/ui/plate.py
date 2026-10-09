# SPDX-License-Identifier: GPL-3.0-or-later
"""The Plate panel in the "Slicer" tab (03 sections 1.1 and 1.4)."""
from __future__ import annotations

import bpy

from ...core import units as core_units
from ...names import OP_PREFIX, TAB_NAME
from .. import bed_source
from ..units import scene_mm_per_bu


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
        else:
            x0, y0, x1, y1 = bed.bounds
            col.label(text=f"Bed {x1 - x0:g} x {y1 - y0:g} mm, up to {bed.height:g} mm")
            col.label(text=f"1 Blender unit = {mm_bu:g} mm")
            notice = core_units.bed_scale_notice(bed.extent_mm, mm_bu)
            if notice:
                box = layout.box()
                box.label(text="Scale notice", icon="INFO")
                col = box.column(align=True)
                for line in _wrap(notice):
                    col.label(text=line)
                box.operator(f"{OP_PREFIX.lower()}.use_mm_scene", icon="DRIVER_DISTANCE")
        sub = layout.column(align=True)
        sub.prop(props, "printable_area")
        sub.prop(props, "bed_exclude_area")
        sub.prop(props, "printable_height")


def _wrap(text: str, width: int = 44) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width)


classes = (SLICEWRIGHT_PT_plate,)
