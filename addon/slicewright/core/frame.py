# SPDX-License-Identifier: GPL-3.0-or-later
"""The bed frame and the Blender world frame (03 section 1.3). No ``bpy``.

The engine works in the printer's frame: millimetres, the G-code origin at (0, 0), ``printable_area``
wherever the printer puts it (a corner bed 0..256, a delta bed centred on 0). Blender's world (0, 0) is the
*centre* of ``printable_area``: the add-on adds ``bed_offset`` to everything it hands the engine and subtracts
it from everything that comes back, so the G-code stays correct. This module is the only place that offset is
defined or applied. A bed centred on 0 (delta printers) has a zero offset, so nothing changes for it.

    engine (bed frame) mm = world mm + offset        world mm = engine mm - offset
"""
from __future__ import annotations

import numpy as np

from .geometry import polygon_bounds

ZERO = np.zeros(3, np.float64)


def bed_offset(area) -> np.ndarray:
    """The offset ``(cx, cy, 0)`` mm: the centre of the bounding box of ``area`` (a ``Bed``, an (n, 2) polygon or
    a ``printable_area`` string such as ``"0x0,256x0,256x256,0x256"``). Zero for an unusable area."""
    if hasattr(area, "area"):
        area = area.area
    elif isinstance(area, str):
        from .geometry import parse_points
        area = parse_points(area)
    poly = np.asarray(area, np.float64).reshape(-1, 2)
    if len(poly) < 3:
        return ZERO.copy()
    x0, y0, x1, y1 = polygon_bounds(poly)
    return np.array([(x0 + x1) / 2.0, (y0 + y1) / 2.0, 0.0], np.float64)


def _off(offset) -> np.ndarray:
    o = np.zeros(3, np.float64) if offset is None else np.asarray(offset, np.float64).ravel()
    return np.concatenate([o, np.zeros(3 - len(o))]) if len(o) < 3 else o[:3]


def _shifted(points, offset, sign: float) -> np.ndarray:
    p = np.asarray(points)
    dt = p.dtype if p.dtype.kind == "f" else np.float64
    return (p.astype(np.float64) + sign * _off(offset)[: p.shape[-1]]).astype(dt)


def world_to_bed(points, offset) -> np.ndarray:
    """World-frame mm points (any shape ending in 2 or 3 coordinates) -> bed frame; float32 stays float32."""
    return _shifted(points, offset, 1.0)


def bed_to_world(points, offset) -> np.ndarray:
    """Bed-frame mm points -> world frame (the inverse of :func:`world_to_bed`)."""
    return _shifted(points, offset, -1.0)


def to_bed_mm(co, matrix_world, mm_per_bu: float, offset) -> np.ndarray:
    """Object-space vertices -> bed-frame mm (float32 (n, 3)): world mm plus ``offset``. Computed in float64."""
    m = np.asarray(matrix_world, np.float64)
    c = np.asarray(co).reshape(-1, 3).astype(np.float64, copy=False)
    return ((c @ m[:3, :3].T + m[:3, 3]) * mm_per_bu + _off(offset)).astype(np.float32)


def placement_to_world(placement, offset) -> np.ndarray:
    """An engine placement ``T`` (4x4, bed-frame mm) as the same motion in world-frame mm: ``Tr(-o) . T . Tr(o)``."""
    t_in = np.eye(4)
    t_in[:3, 3] = _off(offset)
    t_out = np.eye(4)
    t_out[:3, 3] = -_off(offset)
    return t_out @ np.asarray(placement, np.float64) @ t_in
