# SPDX-License-Identifier: GPL-3.0-or-later
"""Override / reset of one setting in an override buffer (03 section 2.3, role 3)."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, StringProperty

from ...core import config_codec as cc
from ...names import PACKAGE_ID
from .. import config_pg
from ..ui import settings


class SLICEWRIGHT_OT_settings_override(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.settings_override"
    bl_label = "Override Setting"
    bl_description = "Override this setting for the active filament slot, or go back to the preset's value"
    bl_options = {"UNDO", "INTERNAL"}

    key: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    action: EnumProperty(items=[("SET", "Override", ""), ("RESET", "Reset", "")], default="SET",
                         options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        pg = context.scene.slicewright
        cfg, override, preset_id = settings.buffer_for(pg, pg.settings_role)
        spec = config_pg.specs().get(self.key)
        if cfg is None or not override or spec is None:
            return {"CANCELLED"}
        if self.action == "RESET":
            config_pg.clear(cfg, self.key)
            return {"FINISHED"}
        text = settings.preset_value(pg.settings_role, preset_id, self.key)
        try:
            value = cc.parse_value(spec, text)
        except ValueError:
            value = spec.default                    # the preset has no usable value: start from the default
        setattr(cfg, self.key, value)               # setting a property is what marks it as overridden
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_settings_override,)
