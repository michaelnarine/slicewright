# SPDX-License-Identifier: GPL-3.0-or-later
"""Override / reset of one setting in an override buffer (03 section 2.3, role 3)."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, StringProperty

from ...core import config_codec as cc
from ...core import settings_view as sv
from ...names import PACKAGE_ID
from .. import config_pg
from ..ui import settings


class SLICEWRIGHT_OT_settings_override(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.settings_override"
    bl_label = "Override Setting"
    bl_description = "Override this setting for the active filament slot, or go back to the preset's value"

    @classmethod
    def description(cls, context, properties):
        if properties.action == "RESET":
            pg = context.scene.slicewright
            _cfg, override, preset_id = settings.buffer_for(pg, pg.settings_role)
            shown = settings.display_value(properties.key, settings.preset_value(pg.settings_role, preset_id, properties.key))
            return "Back to the preset's value" + (f" ({shown})" if shown else "")
        return cls.bl_description
    bl_options = {"UNDO", "INTERNAL"}

    key: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    action: EnumProperty(items=[("SET", "Override", ""), ("RESET", "Reset", "")], default="SET",
                         options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        pg = context.scene.slicewright
        cfg, override, preset_id = settings.buffer_for(pg, pg.settings_role)
        spec = config_pg.specs().get(self.key)
        if cfg is None or spec is None or (self.action == "SET" and not override):
            return {"CANCELLED"}
        if self.action == "RESET" and override:
            config_pg.clear(cfg, self.key)
            return {"FINISHED"}
        text = settings.preset_value(pg.settings_role, preset_id, self.key)
        try:
            value = cc.parse_value(spec, text)
        except ValueError:
            value = spec.default                    # the preset has no usable value: start from the default
        setattr(cfg, self.key, value)               # an override: setting it marks it overridden; a buffer: back to the preset
        return {"FINISHED"}


class SLICEWRIGHT_OT_settings_section(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.settings_section"
    bl_label = "Expand or Collapse"
    bl_description = "Expand or collapse this part of the Settings panel (an empty id collapses everything)"
    bl_options = {"INTERNAL"}

    section: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        return "Collapse every section" if not properties.section else cls.bl_description

    def execute(self, context):
        wm = context.window_manager
        text = getattr(wm, settings.WM_OPEN, "")
        setattr(wm, settings.WM_OPEN, sv.toggled(text, self.section) if self.section else "")
        return {"FINISHED"}


class SLICEWRIGHT_OT_settings_favourite(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.settings_favourite"
    bl_label = "Pin Setting"
    bl_description = "Pin this setting to the Favourites section at the top of the Settings panel, or unpin it"
    bl_options = {"INTERNAL"}

    key: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        if self.key not in config_pg.schema():
            return {"CANCELLED"}
        pg = context.scene.slicewright
        pg.favourites = sv.toggled(pg.favourites, self.key)
        wm = context.window_manager
        if sv.contains(pg.favourites, self.key):                # pinning shows the section so the pin is seen
            setattr(wm, settings.WM_OPEN, sv.added(getattr(wm, settings.WM_OPEN, ""), [sv.FAVOURITES_ID]))
        return {"FINISHED"}


class SLICEWRIGHT_OT_settings_filter_clear(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.settings_filter_clear"
    bl_label = "Clear Search"
    bl_description = "Clear the settings search"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        context.scene.slicewright.settings_filter = ""
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_settings_override, SLICEWRIGHT_OT_settings_section, SLICEWRIGHT_OT_settings_favourite,
           SLICEWRIGHT_OT_settings_filter_clear)
