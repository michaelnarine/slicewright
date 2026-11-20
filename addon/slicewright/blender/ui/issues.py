# SPDX-License-Identifier: GPL-3.0-or-later
"""The Issues panel under the Slice panel (03 section 6.5): one merged list with severity icons and counts.

The UIList reads the window manager mirror kept by ``blender/issues.py``; each row is a flat button that
runs ``issue_goto`` (select the object, open the setting).
"""
from __future__ import annotations

import bpy

from ...core import issues as core_issues
from ...names import PACKAGE_ID, TAB_NAME
from .. import issues

MAX_ROWS = 8


def header_counts(c: core_issues.Counts) -> list[tuple[str, str]]:
    """``(text, icon)`` per non-empty level for the panel header, errors first."""
    return [(str(n), core_issues.icon(level)) for n, level in
            ((c.errors, core_issues.ERROR), (c.warnings, core_issues.WARNING), (c.infos, core_issues.INFO)) if n]


class SLICEWRIGHT_UL_issues(bpy.types.UIList):
    bl_idname = f"{PACKAGE_ID.upper()}_UL_issues"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.alert = item.level == core_issues.ERROR
        op = row.operator(f"{PACKAGE_ID}.issue_goto", text=item.message, icon=core_issues.icon(item.level),
                          emboss=False)
        op.index = index
        if item.code == "mesh_open_edges" and item.object_name:
            sel = row.operator(f"{PACKAGE_ID}.select_non_manifold", text="", icon="RESTRICT_SELECT_OFF")
            sel.object_name = item.object_name


def draw_issues(layout, wm, scene) -> None:
    rows = issues.for_scene(scene)
    if not rows:
        layout.label(text="No issues")
        return
    layout.template_list(SLICEWRIGHT_UL_issues.bl_idname, "", wm, issues.WM_LIST, wm, issues.WM_INDEX,
                         rows=min(len(rows), MAX_ROWS), maxrows=MAX_ROWS)
    index = getattr(wm, issues.WM_INDEX, -1)
    detail = rows[index].get("detail") if 0 <= index < len(rows) else ""
    if detail:                          # the active row's longer text, e.g. the keys behind "N settings ... aren't known"
        box = layout.box()
        col = box.column(align=True)
        col.scale_y = 0.8
        for line in core_issues.wrap_detail(detail):
            col.label(text=line)


class SLICEWRIGHT_PT_issues(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_issues"
    bl_label = "Issues"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME

    def draw_header(self, context):
        row = self.layout.row(align=True)
        for text, icon in header_counts(core_issues.counts(issues.for_scene(context.scene))):
            row.label(text=text, icon=icon)

    def draw(self, context):
        draw_issues(self.layout, context.window_manager, context.scene)


classes = (SLICEWRIGHT_UL_issues, SLICEWRIGHT_PT_issues)
