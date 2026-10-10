# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the bed comes from: the one place that knows how to read it for a scene.

Until the printer presets land (plan M3) the three bed keys live on ``Scene.slicewright`` as
plain properties. M3 replaces ``current_bed`` with a read of the composed printer config; the
drawing, checks and operators only ever call ``current_bed``.
"""
from __future__ import annotations

import functools

import bpy

from ..core.bed import Bed, parse_bed


@functools.lru_cache(maxsize=8)
def _parse(printable_area: str, bed_exclude_area: str, printable_height: float) -> Bed | None:
    try:
        return parse_bed(printable_area, bed_exclude_area, printable_height)
    except ValueError:
        return None


def current_bed(scene: bpy.types.Scene) -> Bed | None:
    """The scene's bed, or None when ``printable_area`` is unusable."""
    props = getattr(scene, "slicewright", None)
    if props is None:
        return None
    return _parse(props.printable_area, props.bed_exclude_area, float(props.printable_height))
