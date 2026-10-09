# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene properties (03 section 2.1). Registered only when the engine is usable.

No ``from __future__ import annotations`` here: Blender evaluates stringified property annotations
without this module's globals, so callbacks and property functions must be real objects.
"""
import bpy
from bpy.props import (CollectionProperty, EnumProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)

from ..names import PACKAGE_ID
from . import picker, registry

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


classes = (SLICEWRIGHT_PG_FilamentSlot, SLICEWRIGHT_PG_Scene)


def register() -> None:
    registry.register_classes(classes)
    setattr(bpy.types.Scene, PACKAGE_ID, PointerProperty(type=SLICEWRIGHT_PG_Scene))


def unregister() -> None:
    if hasattr(bpy.types.Scene, PACKAGE_ID):
        delattr(bpy.types.Scene, PACKAGE_ID)
    registry.unregister_classes(classes)
