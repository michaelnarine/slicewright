# SPDX-License-Identifier: GPL-3.0-or-later
"""The Bake panel under the Export panel (03 section 7.11): what to bake, the cost, and the button.

The button is greyed, with the reason under it, unless there is a current slice and no slice or bake is running. While a bake
runs the panel shows a progress bar and Cancel. The panel starts collapsed.
"""
from __future__ import annotations

import bpy

from ...core import bake_plan as bp
from ...core import preview_legend as legend
from ...names import PACKAGE_ID, TAB_NAME, panel_order
from .. import bake_data, bake_pg, baking, registry
from ..preview import props as preview_props
from ..preview import runtime
from ..labels import draw_wrapped


def draw_bake(layout, context) -> None:
    scene = context.scene
    st = bake_pg.of(context)
    if st is None:
        return
    running = baking.progress(scene)
    if running is not None:
        row = layout.row(align=True)
        row.progress(factor=running[0], type="BAR", text=running[1])
        row.operator(f"{PACKAGE_ID}.bake_cancel", text="", icon="X")
        return
    ok, why = baking.availability(scene)
    rt = runtime.get(scene)
    col = layout.column(align=True)
    col.prop(st, "kind", expand=True)
    col = layout.column(align=True)
    col.enabled = ok
    col.prop(st, "use_preview")
    if rt is not None and st.use_preview:
        p = preview_props.of(scene)
        s = p.scrub()
        col.label(text=f"Layers {s.lo} to {s.hi}" + (", scrubbed" if s.pos is not None else "") + " (Preview)")
    elif rt is not None:
        col.prop(st, "layer_lo")
        col.prop(st, "layer_hi")
        stats = rt.result.stats
        rows = legend.legend_rows(stats, registry.state.status.module.enums()["role"])
        box = col.box()
        for r in rows:
            box.prop(st, "roles", index=r.role_id, text=r.label)
    col.prop(st, "travel")
    if st.kind == bp.MESH:
        col.prop(st, "sides")
        col.prop(st, "part_segments")
    row = layout.row()
    row.scale_y = 1.3
    label = "Bake to Curves" if st.kind == bp.CURVES else "Bake to Mesh"
    row.operator(f"{PACKAGE_ID}.bake", text=label, icon="CURVE_DATA" if st.kind == bp.CURVES else "MESH_DATA")
    if not ok and why:
        draw_wrapped(layout, why, "INFO")
    elif ok:
        text, level = baking.panel_estimate(scene, st)
        if text:
            box = layout.column(align=True)
            box.alert = level != "ok"
            draw_wrapped(box, text, "INFO" if level == "ok" else "ERROR")
    last = baking.message(scene)
    if last is not None:
        level, text = last
        col = layout.column(align=True)
        col.alert = level == "ERROR"
        draw_wrapped(col, text, "ERROR" if level == "ERROR" else "CHECKMARK")
    if bake_data.tagged_objects(scene.name):
        layout.operator(f"{PACKAGE_ID}.bake_keep", icon="PINNED")


class SLICEWRIGHT_PT_bake(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_bake"
    bl_label = "Bake"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME
    bl_order = panel_order("bake")
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_bake(self.layout, context)


classes = (SLICEWRIGHT_PT_bake,)
