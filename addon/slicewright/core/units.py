# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit conversions (03 section 1.4). No ``bpy``.

World XY in millimetres is G-code XY. ``mm_per_BU = scale_length * 1000``: Blender's
5.1 defaults are ``scale_length = 1.0`` (one BU is a metre) and ``length_unit = 'METERS'``.
"""
from __future__ import annotations

MM_PER_METRE = 1000.0
MIN_BED_BU = 0.5        # below this the bed is too small to navigate comfortably
MAX_BED_BU = 1000.0     # above this clip distances and precision start to hurt
MM_SCENE_SCALE = 0.001  # "Use millimetre scene": one BU is one millimetre


def mm_per_bu(scale_length: float) -> float:
    """Millimetres in one Blender unit for a scene's ``unit_settings.scale_length``."""
    scale = float(scale_length)
    if not scale > 0.0:
        raise ValueError(f"scale_length must be positive, got {scale_length!r}")
    return scale * MM_PER_METRE


def bed_extent_bu(bed_extent_mm: float, mm_per_bu_: float) -> float:
    return float(bed_extent_mm) / mm_per_bu_


def bed_scale_notice(bed_extent_mm: float, mm_per_bu_: float) -> str | None:
    """The 03 section 1.4 notice: the bed is under 0.5 BU or over 1000 BU across, else None."""
    bu = bed_extent_bu(bed_extent_mm, mm_per_bu_)
    if bu < MIN_BED_BU:
        return (f"The bed is only {bu:.3g} Blender units across; "
                "consider the millimetre scene so the viewport behaves.")
    if bu > MAX_BED_BU:
        return (f"The bed is {bu:.4g} Blender units across; "
                "consider the millimetre scene so clipping and precision behave.")
    return None
