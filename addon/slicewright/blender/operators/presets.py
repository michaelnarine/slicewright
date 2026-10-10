# SPDX-License-Identifier: GPL-3.0-or-later
"""User preset operators: save, revert, diff, duplicate, rename, delete (03 section 3.8)."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, StringProperty

from ...core.profiles import user as user_core
from ...core.profiles.source import ProfileError
from ...names import PACKAGE_ID
from .. import library, picker, user_presets
from ..ui import settings

ROLES = settings.ROLE_ITEMS


def _current_role(self, context) -> str:
    return self.role or context.scene.slicewright.settings_role


class _PresetOperator:
    bl_options = {"UNDO", "INTERNAL"}
    role: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return library.get() is not None


class SLICEWRIGHT_OT_preset_save(_PresetOperator, bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.preset_save"
    bl_label = "Save to My Presets"
    bl_description = "Save the edited settings as a user preset that inherits from the current preset"

    name: StringProperty(name="Name", description="Name of the user preset")

    def invoke(self, context, event):
        pg = context.scene.slicewright
        role = _current_role(self, context)
        current = user_presets.preset_id_of(pg, role)
        self.name = (user_presets.name_of(current) if current.startswith("user:")
                     else f"My {user_presets.name_of(current)}")
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        pg = context.scene.slicewright
        try:
            new_id = user_presets.save(pg, _current_role(self, context), self.name)
        except (ValueError, ProfileError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Saved {new_id}")
        return {"FINISHED"}


class SLICEWRIGHT_OT_preset_revert(_PresetOperator, bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.preset_revert"
    bl_label = "Revert"
    bl_description = "Go back to the preset's value (one setting, or every unsaved edit)"

    key: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        user_presets.revert(context.scene.slicewright, _current_role(self, context), self.key or None)
        return {"FINISHED"}


class SLICEWRIGHT_OT_preset_diff(_PresetOperator, bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.preset_diff"
    bl_label = "Unsaved Changes"
    bl_description = "List the settings that differ from the preset, with a revert button each"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        pg = context.scene.slicewright
        role = _current_role(self, context)
        rows = user_presets.changes(pg, role)
        if not rows:
            self.layout.label(text="No unsaved changes", icon="CHECKMARK")
        for key, label, was, now in rows:
            row = self.layout.row(align=True)
            row.label(text=f"{label}: {was} → {now}")
            op = row.operator(f"{PACKAGE_ID}.preset_revert", text="", icon="LOOP_BACK")
            op.role, op.key = role, key

    def execute(self, context):
        return {"FINISHED"}


class SLICEWRIGHT_OT_preset_manage(_PresetOperator, bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.preset_manage"
    bl_label = "Manage User Preset"
    bl_description = "Duplicate, rename or delete the selected user preset"

    action: EnumProperty(items=[("DUPLICATE", "Duplicate", ""), ("RENAME", "Rename", ""),
                                ("DELETE", "Delete", "")], default="DUPLICATE", options={"HIDDEN", "SKIP_SAVE"})
    name: StringProperty(name="Name")

    def invoke(self, context, event):
        current = user_presets.preset_id_of(context.scene.slicewright, _current_role(self, context))
        self.name = user_presets.name_of(current) + (" copy" if self.action == "DUPLICATE" else "")
        if self.action == "DELETE":
            return context.window_manager.invoke_confirm(self, event)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        pg = context.scene.slicewright
        role = _current_role(self, context)
        kind = user_presets.KIND_OF_ROLE[role]
        current = user_presets.preset_id_of(pg, role)
        lib = library.get()
        if not current.startswith("user:"):
            self.report({"ERROR"}, "Only user presets can be changed")
            return {"CANCELLED"}
        old = user_presets.name_of(current)
        try:
            if self.action == "DELETE":
                user_presets.delete(pg, role, current)
            elif self.action == "RENAME":
                lib.store.rename(kind, old, self.name)
                user_presets.set_preset_id(pg, role, lib.store.id_of(user_core.check_name(self.name)))
            else:
                lib.store.duplicate(kind, old, self.name)
                user_presets.set_preset_id(pg, role, lib.store.id_of(user_core.check_name(self.name)))
        except (ValueError, ProfileError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        lib.compat.invalidate()
        return {"FINISHED"}


class SLICEWRIGHT_OT_preset_restore_embedded(_PresetOperator, bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.preset_restore_embedded"
    bl_label = "Save to My Presets"
    bl_description = "The preset is missing on this computer; save the copy embedded in the file as a user preset"

    def execute(self, context):
        pg = context.scene.slicewright
        role = _current_role(self, context)
        kind = user_presets.KIND_OF_ROLE[role]
        pid = user_presets.preset_id_of(pg, role)
        config = user_presets.embedded_config(pg, kind, pid)
        if config is None:
            self.report({"ERROR"}, "No embedded copy of this preset")
            return {"CANCELLED"}
        from ...core.profiles import edits
        from .. import config_pg
        schema = config_pg.schema()
        data = {k: edits.file_value(schema, k, v) for k, v in edits.role_flat(schema, kind, config).items()}
        lib = library.get()
        name = user_presets.name_of(pid)
        lib.store.save(kind, name, None, data)
        lib.compat.invalidate()
        user_presets.set_preset_id(pg, role, lib.store.id_of(name))
        picker.load_edit_buffers(pg)
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_preset_save, SLICEWRIGHT_OT_preset_revert, SLICEWRIGHT_OT_preset_diff,
           SLICEWRIGHT_OT_preset_manage, SLICEWRIGHT_OT_preset_restore_embedded)
