# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators of the printer / process / filament picker (03 section 3.7)."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, IntProperty

from ...core import preset_names
from ...names import PACKAGE_ID
from .. import library, picker, staleness


class SLICEWRIGHT_OT_load_library(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.load_library"
    bl_label = "Load Printer Library"
    bl_description = "Build the printer and material index (or retry after an error)"

    def execute(self, context):
        library.request(force=True)
        return {"FINISHED"}


_choice_items: list = []                 # kept alive: Blender holds raw pointers to the strings


def _choices(self, context):
    pg = context.scene.slicewright
    found = picker.preset_choices(pg, self.kind) if library.get() is not None else []
    _choice_items[:] = [(i, name, tip) for i, name, tip in found] or [("NONE", "No compatible presets", "")]
    return _choice_items


class SLICEWRIGHT_OT_pick_preset(bpy.types.Operator):
    """Search popup for a process or filament preset: the list shows readable names, the tooltip of the button and of
    each item carries the full id, and the id that is stored does not change."""
    bl_idname = f"{PACKAGE_ID}.pick_preset"
    bl_label = "Choose Preset"
    bl_options = {"INTERNAL"}
    bl_property = "choice"

    kind: EnumProperty(items=[("process", "Process", ""), ("filament", "Filament", "")], options={"HIDDEN", "SKIP_SAVE"})
    index: IntProperty(default=-1, min=-1, options={"HIDDEN", "SKIP_SAVE"})       # the filament slot
    choice: EnumProperty(name="Preset", items=_choices)

    @classmethod
    def description(cls, context, properties):
        pg = context.scene.slicewright
        if properties.kind == "process":
            return preset_names.tooltip("Process", pg.process_id)
        if 0 <= properties.index < len(pg.filaments):
            return preset_names.tooltip(f"Filament {properties.index + 1}", pg.filaments[properties.index].preset_id)
        return "Choose a filament preset"

    @classmethod
    def poll(cls, context):
        return library.get() is not None

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        pg = context.scene.slicewright
        if self.choice in ("", "NONE"):
            return {"CANCELLED"}
        if self.kind == "process":
            pg.process_id = self.choice                       # the property's own update callback does the rest
        elif 0 <= self.index < len(pg.filaments):
            pg.filaments[self.index].preset_id = self.choice
        else:
            return {"CANCELLED"}
        return {"FINISHED"}


class SLICEWRIGHT_OT_filament_add(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.filament_add"
    bl_label = "Add Filament"
    bl_description = "Add a filament slot"

    @classmethod
    def poll(cls, context):
        return library.get() is not None and len(context.scene.slicewright.filaments) < picker.MAX_SLOTS

    def execute(self, context):
        picker.add_filament(context.scene.slicewright)
        return {"FINISHED"}


class SLICEWRIGHT_OT_filament_add_defaults(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.filament_add_defaults"
    bl_label = "Add Default Materials"
    bl_description = ("Add a slot for each material the printer ships as a default (every slot goes into the G-code "
                      "header). Slots you already have are kept")

    @classmethod
    def poll(cls, context):
        pg = context.scene.slicewright
        return len(pg.filaments) < picker.MAX_SLOTS and bool(picker.missing_default_materials(pg))

    def execute(self, context):
        added = picker.add_default_materials(context.scene.slicewright)
        self.report({"INFO"}, f"Added {added} default material{'' if added == 1 else 's'}")
        return {"FINISHED"}


class SLICEWRIGHT_OT_filament_remove(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.filament_remove"
    bl_label = "Remove Filament"
    bl_description = "Remove this filament slot"

    index: IntProperty(default=-1, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return len(context.scene.slicewright.filaments) > 1

    def execute(self, context):
        filaments = context.scene.slicewright.filaments
        if not 0 <= self.index < len(filaments):
            return {"CANCELLED"}
        filaments.remove(self.index)
        staleness.mark()
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_load_library, SLICEWRIGHT_OT_filament_add, SLICEWRIGHT_OT_filament_add_defaults,
           SLICEWRIGHT_OT_filament_remove, SLICEWRIGHT_OT_pick_preset)
