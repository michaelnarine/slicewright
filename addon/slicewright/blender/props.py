# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene properties (03 section 2.1). Registered only when the engine is usable.

No ``from __future__ import annotations`` here: Blender evaluates stringified property annotations
without this module's globals, so callbacks and property functions must be real objects.
"""
import bpy
from bpy.props import (CollectionProperty, EnumProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)

from ..names import PACKAGE_ID
from . import config_pg, picker, registry
from .ui import settings

REQUIRES_ENGINE = True


def _guarded(fn):
    """Property update callbacks do nothing while ``picker`` is setting properties itself."""
    def update(self, context):
        if not picker.busy():
            fn(self)
    return update


def _search(fn):
    return lambda self, context, text: fn(self, text)


class SLICEWRIGHT_PG_FilamentSlot(bpy.types.PropertyGroup):
    """One filament slot; the order in ``Scene.slicewright.filaments`` gives filament 1..N."""
    preset_id: StringProperty(
        name="Filament", description="System filament preset (sys:<vendor>/<name>) or a user preset",
        search=lambda self, ctx, text: picker.search_filaments(ctx.scene.slicewright, text),
        update=_guarded(picker.on_slot_preset))
    color: FloatVectorProperty(name="Colour", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0,
                               default=(0.8, 0.8, 0.8), description="Display colour of this filament")
    ams_slot: IntProperty(name="AMS slot", default=-1, min=-1, max=15,
                          description="Multi-material unit slot; -1 chooses automatically")


class SLICEWRIGHT_PG_Scene(bpy.types.PropertyGroup):
    mode: EnumProperty(
        name="Mode", description="Prepare the plate or preview the sliced toolpaths",
        items=[("PREPARE", "Prepare", "Arrange and configure the plate"),
               ("PREVIEW", "Preview", "Inspect the sliced toolpaths")],
        default="PREPARE")
    printer_id: StringProperty(
        name="Printer", description="Printer preset: sys:<vendor>/<name> or user:<name>",
        search=lambda self, ctx, text: picker.search_printers(text),
        update=_guarded(picker.on_printer_id))
    process_id: StringProperty(
        name="Process", description="Print settings preset compatible with the printer",
        search=_search(picker.search_processes), update=_guarded(picker.on_process_id))
    pick_vendor: StringProperty(
        name="Vendor", description="Printer vendor",
        search=lambda self, ctx, text: picker.search_vendors(text),
        update=_guarded(picker.on_vendor))
    pick_model: StringProperty(
        name="Model", description="Printer model of the chosen vendor",
        search=_search(picker.search_models), update=_guarded(picker.on_model))
    pick_nozzle: EnumProperty(
        name="Nozzle", description="Nozzle diameter offered by the model",
        items=lambda self, ctx: picker.nozzle_items(self), update=_guarded(picker.on_nozzle))
    filaments: CollectionProperty(type=SLICEWRIGHT_PG_FilamentSlot)
    embedded_presets: StringProperty(
        name="Embedded presets", options={"HIDDEN"},
        description="Portable copy of the presets in use, written when the file is saved (JSON)")
    settings_role: EnumProperty(name="Settings", items=settings.ROLE_ITEMS, default="process",
                                description="Which preset's settings to show")
    settings_page: EnumProperty(name="Page", items=lambda self, ctx: settings.page_items(self.settings_role),
                                description="Settings page")
    settings_slot: IntProperty(name="Slot", min=0, max=15, default=0,
                               description="Filament slot whose settings are shown")
    settings_filter: StringProperty(name="Filter", description="Show settings matching these words")


class SLICEWRIGHT_PG_Object(bpy.types.PropertyGroup):
    """03 section 2.2. Paint lives on the mesh (M4), so linked duplicates share it."""
    filament: IntProperty(name="Filament", min=0, max=16, default=0,
                          description="Filament slot for this object; 0 inherits the plate default")
    role: EnumProperty(name="Role", items=[("PART", "Part", "Printed part")], default="PART")


classes = (SLICEWRIGHT_PG_FilamentSlot, SLICEWRIGHT_PG_Scene, SLICEWRIGHT_PG_Object)


def _add_config_pointers() -> None:
    """The ConfigPG class is generated from the engine schema at register time, so the pointers to it
    are added to the (not yet registered) classes' annotations here (03 section 2.3)."""
    config = config_pg.config_class()
    for cls, names in ((SLICEWRIGHT_PG_Scene, ("printer_edits", "process_edits")),
                       (SLICEWRIGHT_PG_FilamentSlot, ("overrides",)),
                       (SLICEWRIGHT_PG_Object, ("overrides",))):
        for name in names:
            cls.__annotations__[name] = PointerProperty(type=config)


def register() -> None:
    _add_config_pointers()
    registry.register_classes(classes)
    setattr(bpy.types.Scene, PACKAGE_ID, PointerProperty(type=SLICEWRIGHT_PG_Scene))
    setattr(bpy.types.Object, PACKAGE_ID, PointerProperty(type=SLICEWRIGHT_PG_Object))


def unregister() -> None:
    for owner in (bpy.types.Scene, bpy.types.Object):
        if hasattr(owner, PACKAGE_ID):
            delattr(owner, PACKAGE_ID)
    registry.unregister_classes(classes)
