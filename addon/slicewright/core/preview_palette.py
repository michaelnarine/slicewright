# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview colours (03 Appendix A) and the std140 palette array the shaders read.

The values are Orca's preview colours, kept as facts so users see familiar colours; nothing is
translated from libvgcode. No ``bpy`` here: the renderer uploads :func:`palette_array` as a UBO.
"""
from __future__ import annotations

from typing import Mapping

import numpy as np

# Engine role name (``enums()["role"]``) -> (legend label, colour).
ROLE_STYLE = {
    "None": ("None", 0xE6B3B3),
    "Perimeter": ("Inner wall", 0xFFE64D),
    "ExternalPerimeter": ("Outer wall", 0xFF7D38),
    "OverhangPerimeter": ("Overhang wall", 0x1F1FFF),
    "InternalInfill": ("Sparse infill", 0xB03029),
    "SolidInfill": ("Internal solid infill", 0x9654CC),
    "TopSolidInfill": ("Top surface", 0xF04040),
    "BottomSurface": ("Bottom surface", 0x665CC7),
    "Ironing": ("Ironing", 0xFF8C69),
    "BridgeInfill": ("Bridge", 0x4D80BA),
    "InternalBridgeInfill": ("Internal bridge", 0x4D80BA),
    "GapFill": ("Gap infill", 0xFFFFFF),
    "Skirt": ("Skirt", 0x00876E),
    "Brim": ("Brim", 0x003B6E),
    "SupportMaterial": ("Support", 0x00FF00),
    "SupportMaterialInterface": ("Support interface", 0x008000),
    "SupportTransition": ("Support transition", 0x004000),
    "WipeTower": ("Prime tower", 0xB3E3AB),
    "Custom": ("Custom", 0x5ED194),
    "Mixed": ("Mixed", 0x808080),
}

# Engine move-type name -> (legend label, colour) for the optional layers.
OPTION_STYLE = {
    "Travel": ("Travel", 0x38489B),
    "Wipe": ("Wipe", 0xFFFF00),
    "Retract": ("Retract", 0xCD22D6),
    "Unretract": ("Unretract", 0x49ADCF),
    "Seam": ("Seam", 0xE6E6E6),
    "Tool_change": ("Tool change", 0xC1BE63),
    "Color_change": ("Colour change", 0xDA948B),
    "Pause_print": ("Pause", 0x52F083),
    "Custom_gcode": ("Custom G-code", 0xE2D243),
}

RANGE_COLORS = (0x0B2C7A, 0x135985, 0x1C8891, 0x04D60F, 0xAAF200, 0xFCF903,
                0xF5CE0A, 0xE38820, 0xD16830, 0xC2523C, 0x942616)

# Distinct defaults for filament / nozzle / colour slots until the real filament colours are wired.
SLOT_COLORS = (0xFF7D38, 0x1F77B4, 0x2CA02C, 0xD62728, 0x9467BD, 0x8C564B, 0xE377C2, 0x7F7F7F,
               0xBCBD22, 0x17BECF, 0xFFBB78, 0xAEC7E8, 0x98DF8A, 0xFF9896, 0xC5B0D5, 0xC49C94)

# Palette rows (vec4 each), matching ``struct Palette`` in the shaders.
ROLE_ROW, OPTION_ROW, RANGE_ROW, MINMAX_ROW, SLOT_ROW, N_ROWS = 0, 32, 48, 59, 60, 76


def rgb(hexcolor: int) -> tuple[float, float, float]:
    return ((hexcolor >> 16 & 255) / 255.0, (hexcolor >> 8 & 255) / 255.0, (hexcolor & 255) / 255.0)


def palette_array(role_ids: Mapping[str, int], type_ids: Mapping[str, int],
                  range_min: float = 0.0, range_max: float = 1.0,
                  slot_colors: tuple = SLOT_COLORS) -> np.ndarray:
    """(N_ROWS, 4) float32: role colours by role id, option colours by move-type id, the range
    ramp, the active view's min/max, and slot colours. Unknown ids stay mid grey."""
    pal = np.full((N_ROWS, 4), (0.5, 0.5, 0.5, 1.0), np.float32)
    for name, rid in role_ids.items():
        if name in ROLE_STYLE and 0 <= rid < 32:
            pal[ROLE_ROW + rid, :3] = rgb(ROLE_STYLE[name][1])
    for name, tid in type_ids.items():
        if name in OPTION_STYLE and 0 <= tid < 16:
            pal[OPTION_ROW + tid, :3] = rgb(OPTION_STYLE[name][1])
    for i, c in enumerate(RANGE_COLORS):
        pal[RANGE_ROW + i, :3] = rgb(c)
    pal[MINMAX_ROW] = (range_min, range_max, 0.0, 0.0)
    for i in range(16):
        pal[SLOT_ROW + i, :3] = rgb(slot_colors[i % len(slot_colors)])
    return pal


def range_color(t: float) -> tuple[float, float, float]:
    """The ramp colour at ``t`` in [0, 1], interpolated the way the shader does (legend chips)."""
    x = min(max(t, 0.0), 1.0) * (len(RANGE_COLORS) - 1)
    i0 = int(x)
    i1 = min(i0 + 1, len(RANGE_COLORS) - 1)
    a, b = rgb(RANGE_COLORS[i0]), rgb(RANGE_COLORS[i1])
    f = x - i0
    return tuple(a[k] + (b[k] - a[k]) * f for k in range(3))
