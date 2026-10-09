# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene properties (03 section 2.1). Registered only when the engine is usable."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, FloatProperty, PointerProperty, StringProperty

from ..core.bed import DEFAULT_PRINTABLE_AREA
from ..names import PACKAGE_ID
from . import registry

REQUIRES_ENGINE = True


class SLICEWRIGHT_PG_Scene(bpy.types.PropertyGroup):
    mode: EnumProperty(
        name="Mode", description="Prepare the plate or preview the sliced toolpaths",
        items=[("PREPARE", "Prepare", "Arrange and configure the plate"),
               ("PREVIEW", "Preview", "Inspect the sliced toolpaths")],
        default="PREPARE")
    plate_collection: PointerProperty(
        name="Plate collection", type=bpy.types.Collection,
        description="Objects in this collection (and its children) are sliced")
    # The bed keys, read through ``blender.bed_source``. Placeholders until the printer
    # presets (plan M3) compose them from the selected printer.
    printable_area: StringProperty(
        name="Printable area", description="Bed polygon in mm as XxY points, like Orca's printable_area",
        default=DEFAULT_PRINTABLE_AREA)
    bed_exclude_area: StringProperty(
        name="Bed exclude area", description="Polygon in mm the print may not enter (bed_exclude_area)",
        default="")
    printable_height: FloatProperty(
        name="Printable height", description="Maximum print height in mm",
        default=250.0, min=1.0, soft_max=2000.0, unit="NONE")


classes = (SLICEWRIGHT_PG_Scene,)


def register() -> None:
    registry.register_classes(classes)
    setattr(bpy.types.Scene, PACKAGE_ID, PointerProperty(type=SLICEWRIGHT_PG_Scene))


def unregister() -> None:
    if hasattr(bpy.types.Scene, PACKAGE_ID):
        delattr(bpy.types.Scene, PACKAGE_ID)
    registry.unregister_classes(classes)
