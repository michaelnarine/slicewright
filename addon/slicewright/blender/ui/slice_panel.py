# SPDX-License-Identifier: GPL-3.0-or-later
"""The Slice panel at the top of the "Slicer" tab (03 sections 1.1 and 6.1).

A primary Slice button; while a job runs it is replaced by a progress bar (``UILayout.progress``) and a Cancel
button. The state comes from ``Scene.slicewright.job_*``, so a redraw never needs the runner.
"""
from __future__ import annotations

import bpy

from ...engine import jobs
from ...names import PACKAGE_ID, TAB_NAME


def draw_slice(layout, sw) -> None:
    if sw.job_state in jobs.ACTIVE_STATES:
        row = layout.row(align=True)
        row.progress(factor=sw.job_progress, type="BAR", text=sw.job_message or "Slicing…")
        row.operator(f"{PACKAGE_ID}.slice_cancel", text="", icon="X")
        return
    row = layout.row()
    row.scale_y = 1.6
    row.operator(f"{PACKAGE_ID}.slice", text="Slice", icon="PLAY")
    if sw.job_state == jobs.FAILED:
        box = layout.box()
        box.alert = True
        box.label(text=sw.job_message, icon="ERROR")
    elif sw.job_state == jobs.CANCELLED:
        layout.label(text="Cancelled", icon="INFO")
    elif sw.stale:
        layout.label(text="Preview out of date", icon="INFO")


class SLICEWRIGHT_PT_slice(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_slice"
    bl_label = "Slice"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        draw_slice(self.layout, context.scene.slicewright)


classes = (SLICEWRIGHT_PT_slice,)
