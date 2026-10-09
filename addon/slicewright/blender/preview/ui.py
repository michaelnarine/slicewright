# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview panel in the N-panel "Slicer" tab: view, scrubbing, visibility (03 sections 1.1 and 7.5)."""
from __future__ import annotations

import bpy

from ...names import OP_PREFIX, PACKAGE_ID, TAB_NAME
from . import props as preview_props
from . import runtime


def _step(layout, text, target, delta, icon=None):
    op = layout.operator(f"{PACKAGE_ID}.preview_step", text=text, icon=icon or 'NONE')
    op.target, op.delta = target, delta


def draw_status(layout, ctl) -> None:
    """Upload progress, budget notices and errors."""
    if ctl.error is not None:
        layout.alert = True
        layout.label(text=f"Preview failed: {ctl.error}", icon='ERROR')
        layout.alert = False
        return
    if not ctl.done:
        layout.progress(factor=ctl.progress, type='BAR', text="Uploading preview")
    for msg in ctl.messages:
        col = layout.column(align=True)
        lines = _wrap(msg, 44)
        col.label(text=lines[0], icon='INFO')
        for line in lines[1:]:
            col.label(text=line)


def _wrap(text: str, width: int) -> list:
    import textwrap
    return textwrap.wrap(text, width) or [text]


class SLICEWRIGHT_PT_preview(bpy.types.Panel):
    bl_idname = f"{OP_PREFIX}_PT_preview"
    bl_label = "Preview"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME

    @classmethod
    def poll(cls, context):
        return runtime.get(context.scene) is not None

    def draw(self, context):
        layout = self.layout
        rt = runtime.get(context.scene)
        p = getattr(context.scene, preview_props.PROP_NAME)
        draw_status(layout, rt.controller)
        layout.prop(p, "view_type", text="")
        if p.view_type not in ("feature", "filament", "nozzle", "color"):
            row = layout.row(align=True)
            row.prop(p, "range_fixed", text="Fixed range")
            sub = row.row(align=True)
            sub.active = p.range_fixed
            sub.prop(p, "range_min", text="")
            sub.prop(p, "range_max", text="")
        box = layout.box()
        n = len(rt.layers["z"])
        row = box.row(align=True)
        row.prop(p, "layer_hi", text=f"Top layer (of {n})")
        _step(row, "", "TOP", -1, 'TRIA_DOWN')
        _step(row, "", "TOP", 1, 'TRIA_UP')
        row = box.row(align=True)
        row.prop(p, "layer_lo", text="Bottom layer")
        _step(row, "", "BOTTOM", -1, 'TRIA_DOWN')
        _step(row, "", "BOTTOM", 1, 'TRIA_UP')
        row = box.row(align=True)
        row.prop(p, "scrub_moves", text="Scrub moves")
        sub = row.row(align=True)
        sub.active = p.scrub_moves
        sub.prop(p, "move_pos", text="")
        _step(sub, "", "MOVE", -1, 'TRIA_LEFT')
        _step(sub, "", "MOVE", 1, 'TRIA_RIGHT')
        col = layout.column(align=True)
        col.prop(p, "show_travel")
        col.prop(p, "show_retracts")
        col.prop(p, "show_seams")
        col.prop(p, "show_toolchanges")
        col.prop(p, "grey_below")
        layout.prop(p, "quality", expand=True)


classes = (SLICEWRIGHT_PT_preview,)
