# SPDX-License-Identifier: GPL-3.0-or-later
"""The Slice panel at the top of the "Slicer" tab (03 sections 1.1 and 6.1).

A primary Slice button; while a job runs it is replaced by a progress bar (``UILayout.progress``) and a Cancel
button. The lines about the last slice (error, cancel, cache note, out-of-date banner) are drawn in the Mode panel
BELOW the Prepare / Preview row, so they never move the button or the switch. The state comes from
``Scene.slicewright.job_*``, so a redraw never needs the runner.
"""
from __future__ import annotations

import bpy

from ...engine import jobs
from ...names import PACKAGE_ID, TAB_NAME, panel_order
from ..labels import draw_wrapped


STALE_TEXT = "Preview out of date"


def draw_slice_button(layout, sw) -> bool:
    """The Slice button, or while a job runs its progress bar and Cancel. Returns True when it drew the progress."""
    if sw.job_state in jobs.ACTIVE_STATES:
        row = layout.row(align=True)
        row.progress(factor=sw.job_progress, type="BAR", text=sw.job_message or "Slicing…")
        row.operator(f"{PACKAGE_ID}.slice_cancel", text="", icon="X")
        return True
    row = layout.row()
    row.scale_y = 1.6
    row.operator(f"{PACKAGE_ID}.slice", text="Slice", icon="PLAY")
    return False


def draw_status(layout, sw, note: str = "") -> None:
    """What the last slice did: the error, a cancel, a cache note, the out-of-date banner. Drawn BELOW the mode row
    (03 section 6.1), so these lines never move the Slice button or the Prepare / Preview switch."""
    if sw.job_state in jobs.ACTIVE_STATES:
        return
    if sw.job_state == jobs.FAILED:
        col = layout.box().column(align=True)
        col.alert = True
        draw_wrapped(col, sw.job_message, "ERROR", indent=2)
    elif sw.job_state == jobs.CANCELLED:
        layout.label(text="Cancelled", icon="INFO")
    elif sw.job_state == jobs.DONE and sw.job_message not in ("", "Done"):
        draw_wrapped(layout, sw.job_message, "CHECKMARK")        # "Done (from cache)", "Restored from cache"
    if sw.stale:                       # also after a failed or cancelled re-slice: the preview shown is still old
        draw_wrapped(layout, STALE_TEXT, "INFO")
    if note:                           # e.g. the cached slice of a reopened file is gone (03 section 6.4)
        draw_wrapped(layout, note, "INFO")


def draw_slice(layout, sw, note: str = "") -> None:
    """Button and status together (for a caller with one layout; the tab draws them in two panels)."""
    if not draw_slice_button(layout, sw):
        draw_status(layout, sw, note)


class SLICEWRIGHT_PT_slice(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_slice"
    bl_label = "Slice"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME
    bl_order = panel_order("slice")
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        draw_slice_button(self.layout, context.scene.slicewright)


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
    bl_order = panel_order("mode")
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        from .. import slicing
        draw_mode(self.layout, context.scene)
        draw_status(self.layout, context.scene.slicewright, slicing.note(context.scene))


classes = (SLICEWRIGHT_PT_slice, SLICEWRIGHT_PT_mode)
