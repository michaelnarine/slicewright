# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene properties (03 section 2.1). Registered only when the engine is usable."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, PointerProperty

from ..names import PACKAGE_ID
from . import registry

REQUIRES_ENGINE = True


class SLICEWRIGHT_PG_Scene(bpy.types.PropertyGroup):
    mode: EnumProperty(
        name="Mode", description="Prepare the plate or preview the sliced toolpaths",
        items=[("PREPARE", "Prepare", "Arrange and configure the plate"),
               ("PREVIEW", "Preview", "Inspect the sliced toolpaths")],
        default="PREPARE")


classes = (SLICEWRIGHT_PG_Scene,)


def register() -> None:
    registry.register_classes(classes)
    setattr(bpy.types.Scene, PACKAGE_ID, PointerProperty(type=SLICEWRIGHT_PG_Scene))


def unregister() -> None:
    if hasattr(bpy.types.Scene, PACKAGE_ID):
        delattr(bpy.types.Scene, PACKAGE_ID)
    registry.unregister_classes(classes)
