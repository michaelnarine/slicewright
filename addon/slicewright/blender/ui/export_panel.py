# SPDX-License-Identifier: GPL-3.0-or-later
"""The Export panel under the Slice and Issues panels (03 section 8.1): one button per file format.

A button is greyed out unless there is a slice to export (the operators' ``poll``); the line under them says why, or
that the preview is out of date (the export would hold the older slice), or what the last export did.
"""
from __future__ import annotations

import bpy

from ...names import PACKAGE_ID, TAB_NAME
from .. import exporting

STALE_HINT = "Preview out of date: exports the older slice"


def draw_export(layout, scene) -> None:
    sw = scene.slicewright
    running = exporting.progress(scene)
    if running is not None:                    # a re-slice for the .gcode.3mf: a bar and Cancel instead of the buttons
        row = layout.row(align=True)
        row.progress(factor=running[0], type="BAR", text=running[1])
        row.operator(f"{PACKAGE_ID}.export_cancel", text="", icon="X")
    else:
        col = layout.column(align=True)
        col.operator(f"{PACKAGE_ID}.export_gcode", icon="EXPORT")
        col.operator(f"{PACKAGE_ID}.export_gcode_3mf", icon="EXPORT")
        ok, why = exporting.availability(scene, exporting.GCODE)
        if ok:                                  # G-code is fine, the .gcode.3mf may have its own reason not to be
            ok3, why = exporting.availability(scene, exporting.THREEMF)
            ok = ok3
        if not ok and why:
            layout.label(text=why, icon="INFO")
        elif sw.stale:
            layout.label(text=STALE_HINT, icon="INFO")
    last = exporting.message(scene)
    if last is not None:
        level, text = last
        row = layout.row()
        row.alert = level == "ERROR"
        row.label(text=text, icon="ERROR" if level == "ERROR" else "CHECKMARK")


class SLICEWRIGHT_PT_export(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_export"
    bl_label = "Export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME

    def draw(self, context):
        draw_export(self.layout, context.scene)


classes = (SLICEWRIGHT_PT_export,)
