# SPDX-License-Identifier: GPL-3.0-or-later
"""The Slice panel at the top of the "Slicer" tab (03 sections 1.1 and 6.1).

A primary Slice button; while a job runs it is replaced by a progress bar (``UILayout.progress``) and a Cancel
button. The state comes from ``Scene.slicewright.job_*``, so a redraw never needs the runner.
"""
from __future__ import annotations

import bpy

from ...engine import jobs
from ...names import PACKAGE_ID, TAB_NAME


STALE_TEXT = "Preview out of date"


def draw_slice(layout, sw, note: str = "") -> None:
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
    elif sw.job_state == jobs.DONE and sw.job_message not in ("", "Done"):
        layout.label(text=sw.job_message, icon="CHECKMARK")        # "Done (from cache)", "Restored from cache"
    if sw.stale:                       # also after a failed or cancelled re-slice: the preview shown is still old
        layout.label(text=STALE_TEXT, icon="INFO")
    if note:                           # e.g. the cached slice of a reopened file is gone (03 section 6.4)
        layout.label(text=note, icon="INFO")


class SLICEWRIGHT_PT_slice(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_slice"
    bl_label = "Slice"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        from .. import slicing
        draw_slice(self.layout, context.scene.slicewright, slicing.note(context.scene))


def draw_mode(layout, scene) -> None:
    from .. import mode
    mode.draw_switch(layout, scene)


class SLICEWRIGHT_PT_mode(bpy.types.Panel):
    """The ``[Prepare | Preview]`` switch under the Slice button (the same row sits in the viewport header)."""
    bl_idname = f"{PACKAGE_ID.upper()}_PT_mode"
    bl_label = "Mode"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        draw_mode(self.layout, context.scene)


classes = (SLICEWRIGHT_PT_slice, SLICEWRIGHT_PT_mode)
