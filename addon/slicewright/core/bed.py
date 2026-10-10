# SPDX-License-Identifier: GPL-3.0-or-later
"""The procedural bed (03 section 1.3): geometry only, in millimetres, no ``bpy``.

Drawn from ``printable_area``, ``bed_exclude_area`` and ``printable_height``; no vendor
assets, and the ``bed_model`` / ``bed_texture`` keys are ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .geometry import hatch_segments, parse_points, polygon_bounds

GRID_MM = 10.0
HATCH_MM = 4.0
AXIS_MM = 20.0
DEFAULT_PRINTABLE_AREA = "0x0,256x0,256x256,0x256"


@dataclass(frozen=True)
class Bed:
    """A parsed bed. ``exclude`` is a tuple of polygons; ``bed_exclude_area`` is read as one polygon."""
    area: np.ndarray                                    # (n, 2) printable_area
    exclude: tuple = ()                                 # tuple of (m, 2) arrays
    height: float = 250.0                               # printable_height, mm
    key: tuple = field(default=(), compare=False)       # cache key: the source strings

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return polygon_bounds(self.area)

    @property
    def extent_mm(self) -> float:
        x0, y0, x1, y1 = self.bounds
        return max(x1 - x0, y1 - y0)

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bounds
        return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def parse_bed(printable_area: str, bed_exclude_area: str, printable_height: float) -> Bed:
    """Raises ``ValueError`` for an unusable ``printable_area`` (fewer than three points)."""
    area = parse_points(printable_area)
    if len(area) < 3:
        raise ValueError("printable_area needs at least three points")
    ex = parse_points(bed_exclude_area)
    # One polygon, like the fake engine and Orca's single Points option; a list of areas is a
    # spec question (see the M4 report).
    exclude = (ex,) if len(ex) >= 3 else ()
    return Bed(area, exclude, float(printable_height),
               (printable_area, bed_exclude_area, float(printable_height)))


def _loop(poly: np.ndarray, z: float = 0.0) -> np.ndarray:
    """A closed polygon as (2n, 3) line-list vertices at height ``z``."""
    n = len(poly)
    a = np.column_stack([poly, np.full(n, z)])
    b = np.roll(a, -1, axis=0)
    return np.stack([a, b], axis=1).reshape(-1, 3)


def _lift(seg: np.ndarray, z: float = 0.0) -> np.ndarray:
    pts = seg.reshape(-1, 2)
    return np.column_stack([pts, np.full(len(pts), z)])


def _volume_lines(bed: Bed) -> np.ndarray:
    """The build volume's wireframe: the top loop at ``printable_height`` plus a vertical per corner."""
    top = _loop(bed.area, bed.height)
    n = len(bed.area)
    bottom = np.column_stack([bed.area, np.zeros(n)])
    up = np.column_stack([bed.area, np.full(n, bed.height)])
    verticals = np.stack([bottom, up], axis=1).reshape(-1, 3)
    return np.concatenate([top, verticals])


def build_geometry(bed: Bed) -> dict[str, np.ndarray]:
    """Line lists (float32, shape (2k, 3), mm) keyed by what they are drawn as.

    ``grid``, ``outline``, ``exclude_outline``, ``exclude_hatch``, ``volume`` and
    ``axis_x`` / ``axis_y`` / ``axis_z``.
    """
    grid = np.concatenate([hatch_segments(bed.area, GRID_MM, 0.0),
                           hatch_segments(bed.area, GRID_MM, 90.0)])
    out = {
        "grid": _lift(grid),
        "outline": _loop(bed.area),
        "volume": _volume_lines(bed),
        "axis_x": np.array([[0, 0, 0], [AXIS_MM, 0, 0]], np.float64),
        "axis_y": np.array([[0, 0, 0], [0, AXIS_MM, 0]], np.float64),
        "axis_z": np.array([[0, 0, 0], [0, 0, AXIS_MM]], np.float64),
    }
    ex_outline, ex_hatch = [], []
    for poly in bed.exclude:
        ex_outline.append(_loop(poly))
        ex_hatch.append(_lift(hatch_segments(poly, HATCH_MM, 45.0)))
    out["exclude_outline"] = np.concatenate(ex_outline) if ex_outline else np.zeros((0, 3))
    out["exclude_hatch"] = np.concatenate(ex_hatch) if ex_hatch else np.zeros((0, 3))
    return {k: np.ascontiguousarray(v, np.float32) for k, v in out.items()}
