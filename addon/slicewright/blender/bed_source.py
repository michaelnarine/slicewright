# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the bed comes from: the one place that knows how to read it for a scene.

The bed is composed from the printer preset in use: ``current_bed`` reads ``printable_area``,
``bed_exclude_area`` and ``printable_height`` from the printer edit buffer
(``Scene.slicewright.printer_edits``), which holds the selected preset's resolved config plus any
unsaved edits (03 section 2.3). There is no separate bed override: to change the bed, edit the
printer settings. With no printer selected the buffer holds the engine's schema defaults
(Orca's: a 200 x 200 mm bed, 100 mm tall, nothing excluded).
The drawing, checks and operators only ever call ``current_bed``.
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
    """The scene's bed, or None when there is no config buffer or ``printable_area`` is unusable."""
    props = getattr(scene, "slicewright", None)
    cfg = getattr(props, "printer_edits", None)
    area = getattr(cfg, "printable_area", None)
    if area is None:
        return None
    return _parse(str(area), str(getattr(cfg, "bed_exclude_area", "") or ""),
                  float(getattr(cfg, "printable_height", 100.0)))
