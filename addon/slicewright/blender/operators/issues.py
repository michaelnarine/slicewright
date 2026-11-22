# SPDX-License-Identifier: GPL-3.0-or-later
"""Click-to-act on an Issues row (03 section 6.5): select and frame the object, open the setting.

A row with an ``object_name`` selects that plate object, makes it active and frames it in the 3D view. A row
with an ``opt_key`` points the Settings panel at it: the role (printer, process or filament), the page that lists
the key, and the filter set to the key so only that row shows. Both can apply to one row. A row with neither only
reports its message.
"""
from __future__ import annotations

import bpy
from bpy.props import IntProperty

from ...core import issues as core_issues
from ...core import messages
from ...core import settings_layout as sl
from ...names import PACKAGE_ID
from .. import config_pg, issues, plate_collection
from ..ui import settings


def resolve_object(scene, name: str):
    """The scene object an issue's ``object_name`` refers to, or ``None``.

    The engine names instances ``Bolt#3``; the instance's source is not always selectable (a collection
    instance), so then the plate object that instances it is chosen instead."""
    base = core_issues.base_object_name(name)
    ob = scene.objects.get(base)
    if ob is not None:
        return ob
    source = bpy.data.objects.get(base)
    for plate_ob in plate_collection.plate_objects(scene):
        if plate_ob.name == base:
            return plate_ob
        coll = plate_ob.instance_collection
        if source is not None and coll is not None and source.name in coll.all_objects:
            return plate_ob
    return None


def _view3d_override(context):
    """``(area, region)`` of a 3D view's main region for ``temp_override``, or ``None`` (background mode)."""
    screen = context.screen
    areas = [a for a in (screen.areas if screen else ()) if a.type == "VIEW_3D"]
    if context.area is not None and context.area.type == "VIEW_3D":
        areas.insert(0, context.area)
    for area in areas:
        for region in area.regions:
            if region.type == "WINDOW":
                return area, region
    return None


def select_object(context, ob) -> bool:
    """Select ``ob`` alone, make it active and frame it. Returns False when it is not in the view layer."""
    view_layer = context.view_layer
    if ob.name not in view_layer.objects:
        return False
    if context.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
    for other in view_layer.objects:
        other.select_set(other is ob)
    view_layer.objects.active = ob
    target = _view3d_override(context)
    if target is not None:
        area, region = target
        with context.temp_override(area=area, region=region):
            bpy.ops.view3d.view_selected()
    return True


def focus_setting(context, key: str) -> str:
    """Point the Settings panel at ``key``. Returns a short status line saying where it is, or why it cannot be shown."""
    from ... import prefs
    pg = context.scene.slicewright
    schema = config_pg.schema()
    role = sl.role_of_key(schema, key)
    if role is None:
        return f"{key} is not a known setting"
    page = sl.find_key_page(settings.pages_for(role), key)
    if page is None:
        return f"{key} is not on any settings page"
    pg.settings_role = role
    pg.settings_page = page.name
    p = prefs.get()
    shown = [k for _g, keys in sl.visible_groups(page, schema, p.settings_mode, key, p.show_develop) for k in keys]
    if key in shown:
        pg.settings_filter = key
        return f"{schema[key].get('label', key)}: {role.title()} settings, {page.name} page"
    pg.settings_filter = ""
    return (f"{schema[key].get('label', key)} is on the {role.title()} settings, {page.name} page, "
            f"but hidden at the {p.settings_mode} level")


class SLICEWRIGHT_OT_issue_goto(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.issue_goto"
    bl_label = "Show Issue"
    bl_options = {"INTERNAL"}

    index: IntProperty(options={"HIDDEN", "SKIP_SAVE"}, default=-1, min=-1)

    @classmethod
    def description(cls, context, properties):
        rows = issues.visible()
        if 0 <= properties.index < len(rows):
            obj, key = core_issues.targets(rows[properties.index])
            hints = [h for h in (f"select {obj}" if obj else "", f"open the setting {key}" if key else "") if h]
            row = rows[properties.index]
            text = messages.tooltip(row) + (f"\nClick to {' and '.join(hints)}" if hints else "")
            if row.get("detail"):
                text += "\n" + row["detail"]
            return text
        return "Show the object or setting this issue is about"

    def execute(self, context):
        rows = issues.visible()
        if not 0 <= self.index < len(rows):
            return {"CANCELLED"}
        wm = context.window_manager
        if hasattr(wm, issues.WM_INDEX):
            setattr(wm, issues.WM_INDEX, self.index)
        issue = rows[self.index]
        obj_name, key = core_issues.targets(issue)
        if obj_name:
            ob = resolve_object(context.scene, obj_name)
            if ob is None or not select_object(context, ob):
                self.report({"WARNING"}, f"{obj_name} is not in this view layer")
        if key:
            self.report({"INFO"}, focus_setting(context, key))
        elif not obj_name:
            self.report({"INFO"}, messages.plain(issue))
        return {"FINISHED"}


class SLICEWRIGHT_OT_issue_details(bpy.types.Operator):
    """The engine's own text behind a sentence the panel reworded: the tooltip, and the status bar and Info log on click."""
    bl_idname = f"{PACKAGE_ID}.issue_details"
    bl_label = "Engine Message"
    bl_options = {"INTERNAL"}

    index: IntProperty(options={"HIDDEN", "SKIP_SAVE"}, default=-1, min=-1)

    @classmethod
    def description(cls, context, properties):
        rows = issues.visible()
        if 0 <= properties.index < len(rows):
            return "Engine message: " + str(rows[properties.index].get("message") or "")
        return "Show the engine's own message"

    def execute(self, context):
        rows = issues.visible()
        if not 0 <= self.index < len(rows):
            return {"CANCELLED"}
        self.report({"INFO"}, str(rows[self.index].get("message") or ""))
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_issue_goto, SLICEWRIGHT_OT_issue_details)
