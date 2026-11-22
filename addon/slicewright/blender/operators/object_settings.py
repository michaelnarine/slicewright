# SPDX-License-Identifier: GPL-3.0-or-later
"""Add and remove a per-object setting override (03 section 1.2, step 5)."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, StringProperty

from ...core import descriptions
from ...core import object_overrides as core
from ...names import PACKAGE_ID
from .. import config_pg, object_settings

_items: list[tuple[str, str, str]] = []      # kept alive: Blender holds raw pointers to the strings


def _choices(self, context):
    """The picker's keys for the active object at the current settings detail (a fresh list on every call)."""
    from ... import prefs
    ob = bpy.context.active_object
    found = []
    if ob is not None:
        schema = config_pg.schema()
        p = prefs.get()
        for key in object_settings.offer(ob, p.settings_mode, p.show_develop):
            found.append((key, core.pick_label(schema, key), descriptions.corrected(key, schema[key].get("tooltip") or "")))
    _items[:] = found or [("", "Nothing to add", "")]
    return _items


def _has_target(context) -> bool:
    ob = context.active_object
    return ob is not None and hasattr(ob, PACKAGE_ID) and bool(config_pg.specs())


class SLICEWRIGHT_OT_object_override_add(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.object_override_add"
    bl_label = "Add Override"
    bl_description = ("Override a setting for the active object only. Offers the settings that can differ per object, "
                      "at the detail level chosen in the preferences")
    bl_options = {"UNDO", "INTERNAL"}
    bl_property = "choice"

    choice: EnumProperty(name="Setting", items=_choices)

    @classmethod
    def poll(cls, context):
        return _has_target(context)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.choice or not object_settings.add_override(context.active_object, context.scene.slicewright,
                                                               self.choice):
            return {"CANCELLED"}
        return {"FINISHED"}


class SLICEWRIGHT_OT_object_override_remove(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.object_override_remove"
    bl_label = "Remove Override"
    bl_description = "Stop overriding this setting for the active object (an empty key removes every override)"
    bl_options = {"UNDO", "INTERNAL"}

    key: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _has_target(context)

    def execute(self, context):
        object_settings.remove_override(context.active_object, self.key or None)
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_object_override_add, SLICEWRIGHT_OT_object_override_remove)
