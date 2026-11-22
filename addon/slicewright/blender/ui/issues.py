# SPDX-License-Identifier: GPL-3.0-or-later
"""The Issues panel (03 section 6.5): one merged list with severity icons and counts.

Each issue is a small box: its sentence in plain language (``core.messages``), wrapped to the sidebar width, whose first
line carries the severity icon, and below it the actions that fit: select the object, show the setting, and (for a
sentence we reworded) an info button whose tooltip and report are the engine's own text. The ignored profile keys are a closed line under the list.
"""
from __future__ import annotations

import bpy

from ...core import issues as core_issues
from ...core import messages
from ...names import PACKAGE_ID, TAB_NAME, panel_order
from .. import issues
from ..labels import draw_wrapped, lines_for

MAX_ROWS = 6                      # issues shown before "N more"; the rest are one toggle away


def header_counts(c: core_issues.Counts) -> list[tuple[str, str]]:
    """``(text, icon)`` per non-empty level for the panel header, errors first."""
    return [(str(n), core_issues.icon(level)) for n, level in
            ((c.errors, core_issues.ERROR), (c.warnings, core_issues.WARNING), (c.infos, core_issues.INFO)) if n]


class SLICEWRIGHT_UL_ignored_keys(bpy.types.UIList):
    """The profile keys the engine ignored: a short list that scrolls on its own."""
    bl_idname = f"{PACKAGE_ID.upper()}_UL_ignored_keys"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name)


IGNORED_ROWS = 5
IGNORED_NOTE = "The engine skips these settings from the profile; they do not change the slice."


def draw_ignored(layout, wm, scene) -> None:
    """One quiet line, "N profile settings ignored", with the keys behind a toggle (closed by default)."""
    keys = issues.ignored_keys_for_scene(scene)
    if not keys:
        return
    opened = getattr(wm, issues.WM_IGNORED_OPEN)
    line = layout.row()
    line.alignment = "LEFT"
    line.prop(wm, issues.WM_IGNORED_OPEN, text=core_issues.ignored_label(len(keys)), emboss=False,
              icon="TRIA_DOWN" if opened else "TRIA_RIGHT")
    if opened:
        draw_wrapped(layout, IGNORED_NOTE)
        layout.template_list(SLICEWRIGHT_UL_ignored_keys.bl_idname, "", wm, issues.WM_IGNORED, wm,
                             issues.WM_IGNORED_INDEX, rows=min(len(keys), IGNORED_ROWS), maxrows=IGNORED_ROWS)


def draw_issue(layout, index: int, issue: dict) -> None:
    """One issue as a box: icon and sentence as labels, then the buttons that fit (select the object, show the setting,
    the engine's own text when the sentence was reworded)."""
    level = issue.get("level", "")
    col = layout.box().column(align=True)
    col.alert = level == core_issues.ERROR
    draw_wrapped(col, messages.plain(issue), core_issues.icon(level), indent=2)
    obj, key = core_issues.targets(issue)
    reworded = messages.plain(issue) != str(issue.get("message") or "")
    if not (obj or key or reworded):
        return
    row = col.row(align=True)
    row.alert = False
    if obj:
        row.operator(f"{PACKAGE_ID}.issue_goto", text="Select object", icon="RESTRICT_SELECT_OFF").index = index
        if issue.get("code") == "mesh_open_edges":
            row.operator(f"{PACKAGE_ID}.select_non_manifold", text="Open edges", icon="EDGESEL").object_name = obj
    elif key:
        row.operator(f"{PACKAGE_ID}.issue_goto", text="Show setting", icon="PREFERENCES").index = index
    if reworded:
        row.operator(f"{PACKAGE_ID}.issue_details", text="", icon="INFO").index = index


def draw_issues(layout, wm, scene) -> None:
    rows = issues.visible_for_scene(scene)
    if not rows:
        layout.label(text="No issues")
    else:
        show_all = getattr(wm, issues.WM_ALL)
        shown = rows if show_all else rows[:MAX_ROWS]
        col = layout.column(align=True)
        for index, issue in enumerate(shown):
            draw_issue(col, index, issue)
        if len(rows) > MAX_ROWS:
            layout.prop(wm, issues.WM_ALL, emboss=False, icon="TRIA_DOWN" if show_all else "TRIA_RIGHT",
                        text="Show fewer" if show_all else f"Show all {len(rows)} ({len(rows) - MAX_ROWS} more)")
    draw_ignored(layout, wm, scene)


class SLICEWRIGHT_PT_issues(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_issues"
    bl_label = "Issues"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME
    bl_order = panel_order("issues")

    def draw_header(self, context):
        row = self.layout.row(align=True)
        for text, icon in header_counts(core_issues.counts(issues.visible_for_scene(context.scene))):
            row.label(text=text, icon=icon)

    def draw(self, context):
        draw_issues(self.layout, context.window_manager, context.scene)


classes = (SLICEWRIGHT_UL_ignored_keys, SLICEWRIGHT_PT_issues)
