# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene properties (03 section 2.1). Registered only when the engine is usable.

No ``from __future__ import annotations`` here: Blender evaluates stringified property annotations
without this module's globals, so callbacks and property functions must be real objects.
"""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)

from ..core.brush import KINDS as BRUSH_KINDS
from ..engine import jobs
from ..names import PACKAGE_ID
from . import config_pg, picker, registry, staleness
from .preview import props as preview_props
from .ui import settings

REQUIRES_ENGINE = True


def _guarded(fn):
    """Property update callbacks do nothing while ``picker`` is setting properties itself."""
    def update(self, context):
        staleness.inputs_changed(self, context)       # the preset (or slot) a slice composes from changed
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
                               default=(0.8, 0.8, 0.8), description="Display colour of this filament",
                               update=staleness.inputs_changed)
    ams_slot: IntProperty(name="AMS slot", default=-1, min=-1, max=15, update=staleness.inputs_changed,
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

    preview: PointerProperty(type=preview_props.SLICEWRIGHT_PG_preview)

    # Slice job runtime state (03 sections 2.1 and 6). Blender's SKIP_SAVE does not keep a property
    # out of the .blend, so these are plain properties that ``reset_runtime`` clears on load_post:
    # a job never survives a file load. ``last_cache_key`` is the one that IS saved (it reconnects the
    # preview after a reload).
    job_state: EnumProperty(
        name="Slice state", options={"HIDDEN"}, description="State of the slicing job",
        items=[(k, label, "") for k, label in jobs.JOB_STATES], default=jobs.IDLE)
    job_progress: FloatProperty(name="Slice progress", options={"HIDDEN"}, default=0.0, min=0.0, max=1.0,
                                subtype="FACTOR", description="Progress of the slicing job, 0 to 1")
    job_message: StringProperty(name="Slice status", options={"HIDDEN"},
                                description="Short status of the slicing job")
    stale: BoolProperty(name="Out of date", options={"HIDDEN"}, default=False,
                        description="The plate or settings changed since the preview was sliced")
    last_cache_key: StringProperty(name="Last slice key", options={"HIDDEN"},
                                   description="Input key of the last slice; reconnects the cached result")

    plate_collection: PointerProperty(
        name="Plate collection", type=bpy.types.Collection,
        description="Objects in this collection (and its children) are sliced", update=staleness.plate_changed)
    show_paint_overlay: BoolProperty(
        name="Show paint", description="Draw painted faces over the model (Prepare mode)", default=True)
    brush_kind: EnumProperty(
        name="Paint", description="What the object-mode brush paints",
        items=[(k[0], k[1], "") for k in BRUSH_KINDS], default="SUPPORT_ENFORCE")
    brush_radius: FloatProperty(
        name="Radius", description="Brush radius in millimetres", default=5.0, min=0.1, soft_max=100.0,
        unit="NONE")
    brush_smart: BoolProperty(
        name="Smart fill", description="Only paint faces connected to the hit face without a sharp crease",
        default=False)
    brush_angle: FloatProperty(
        name="Crease angle", description="Smart fill stops where neighbouring faces differ by more than this (degrees)",
        default=20.0, min=1.0, max=89.0, unit="NONE")
    paint_filament: IntProperty(
        name="Slot", description="Filament slot to assign to the selected faces",
        default=1, min=1, max=16)


class SLICEWRIGHT_PG_Object(bpy.types.PropertyGroup):
    """03 section 2.2. Paint lives on the mesh (M4), so linked duplicates share it."""
    filament: IntProperty(name="Filament", min=0, max=16, default=0, update=staleness.inputs_changed,
                          description="Filament slot for this object; 0 inherits the plate default")
    role: EnumProperty(name="Role", items=[("PART", "Part", "Printed part")], default="PART",
                       update=staleness.inputs_changed)


classes = (SLICEWRIGHT_PG_FilamentSlot, SLICEWRIGHT_PG_Scene, SLICEWRIGHT_PG_Object)
# The preview group is registered first: ``Scene.slicewright.preview`` points at it.
all_classes = preview_props.classes + classes


def reset_runtime(scene) -> None:
    """Back to the idle job state (file load: no job survives it). Keeps ``last_cache_key``."""
    sw = getattr(scene, PACKAGE_ID, None)
    if sw is None:
        return
    for name in ("job_state", "job_progress", "job_message", "stale"):
        sw.property_unset(name)


def _on_load_post() -> None:
    for scene in bpy.data.scenes:
        reset_runtime(scene)


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
    registry.register_classes(all_classes)
    if _on_load_post not in registry.state.load_post_hooks:
        registry.state.load_post_hooks.append(_on_load_post)
    setattr(bpy.types.Scene, PACKAGE_ID, PointerProperty(type=SLICEWRIGHT_PG_Scene))
    setattr(bpy.types.Object, PACKAGE_ID, PointerProperty(type=SLICEWRIGHT_PG_Object))


def unregister() -> None:
    for owner in (bpy.types.Scene, bpy.types.Object):
        if hasattr(owner, PACKAGE_ID):
            delattr(owner, PACKAGE_ID)
    while _on_load_post in registry.state.load_post_hooks:
        registry.state.load_post_hooks.remove(_on_load_post)
    registry.unregister_classes(all_classes)
