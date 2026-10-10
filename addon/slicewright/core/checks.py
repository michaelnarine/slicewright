# SPDX-License-Identifier: GPL-3.0-or-later
"""Plate checks on world-space millimetre vertices (03 section 4.3). No ``bpy``."""
from __future__ import annotations

import numpy as np

from .bed import Bed
from .geometry import convex_hull, convex_overlap, points_in_polygon

BELOW_BED_MM = -0.01       # min Z below this is "below the bed" (03 section 4.3)
TOL_MM = 1e-3

OUTSIDE_BED = "outside_bed"
IN_EXCLUDE = "in_exclude_area"
TOO_TALL = "too_tall"
BELOW_BED = "below_bed"


def out_of_volume(vertices_mm: np.ndarray, bed: Bed) -> list[str]:
    """Reasons an object's vertices ((n, 3) mm, bed frame) do not fit the build volume; empty if they fit.

    XY convex hull against ``printable_area`` and ``bed_exclude_area`` (exclusions are tested as
    their convex hulls), max Z against ``printable_height``, min Z against -0.01 mm.
    """
    v = np.asarray(vertices_mm, np.float64).reshape(-1, 3)
    if len(v) == 0:
        return []
    reasons = []
    hull = convex_hull(v[:, :2])
    if not points_in_polygon(hull, bed.area, eps=TOL_MM).all():
        reasons.append(OUTSIDE_BED)
    for poly in bed.exclude:
        if convex_overlap(hull, convex_hull(poly), eps=TOL_MM):
            reasons.append(IN_EXCLUDE)
            break
    if float(v[:, 2].max()) > bed.height + TOL_MM:
        reasons.append(TOO_TALL)
    if float(v[:, 2].min()) < BELOW_BED_MM:
        reasons.append(BELOW_BED)
    return reasons


class Throttle:
    """Runs at most once per ``interval`` seconds; ``clock`` is injectable for tests."""

    def __init__(self, interval: float, clock) -> None:
        self.interval, self.clock, self._last = interval, clock, None

    def ready(self) -> bool:
        now = self.clock()
        return self._last is None or now - self._last >= self.interval

    def mark(self) -> None:
        self._last = self.clock()

    def remaining(self) -> float:
        if self._last is None:
            return 0.0
        return max(0.0, self.interval - (self.clock() - self._last))
