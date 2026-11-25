# SPDX-License-Identifier: GPL-3.0-or-later
"""The scene's bed-centre offset: the thin ``bpy`` side of ``core.frame`` (03 section 1.3).

Blender world (0, 0) is the centre of ``printable_area``; the engine works in the printer's frame. Every
conversion across that boundary takes its offset from here.
"""
from __future__ import annotations

import numpy as np

from ..core.frame import ZERO, bed_offset
from . import bed_source


def offset_mm(scene) -> np.ndarray:
    """``(cx, cy, 0)`` mm for the scene's current bed; zero when the bed is unusable. bed-frame = world + offset."""
    bed = bed_source.current_bed(scene) if scene is not None else None
    return bed_offset(bed) if bed is not None else ZERO.copy()
