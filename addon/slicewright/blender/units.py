# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene units (03 section 1.4): the bridge from ``unit_settings`` to ``core.units``."""
from __future__ import annotations

import bpy

from ..core import units as core_units


def scene_mm_per_bu(scene: bpy.types.Scene) -> float:
    """Millimetres per Blender unit for ``scene``."""
    return core_units.mm_per_bu(scene.unit_settings.scale_length)


def use_millimetre_scene(scene: bpy.types.Scene, screen: bpy.types.Screen | None = None) -> None:
    """Make one BU one millimetre: unit scale and length unit, plus every 3D view's clip and grid.

    Clip distances are in BU, so with 1 BU = 1 mm the defaults (0.01 .. 1000 m) would clip a
    250 mm bed: they become 1 .. 10000. The overlay grid scale is reset to 1.
    """
    scene.unit_settings.scale_length = core_units.MM_SCENE_SCALE
    scene.unit_settings.length_unit = "MILLIMETERS"
    screens = [screen] if screen is not None else list(bpy.data.screens)
    for scr in screens:
        for area in scr.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.clip_start = 1.0
                    space.clip_end = 10000.0
                    space.overlay.grid_scale = 1.0
