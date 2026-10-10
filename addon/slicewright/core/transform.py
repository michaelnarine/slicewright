# SPDX-License-Identifier: GPL-3.0-or-later
"""Object-space to bed-frame transforms (03 section 4.1). No ``bpy``.

Transforms are computed in float64 and stored as float32 millimetres.
"""
from __future__ import annotations

import numpy as np


def to_world_mm(co: np.ndarray, matrix_world, mm_per_bu: float) -> np.ndarray:
    """Object-space vertices ((n, 3) or flat) -> world mm as float32 (n, 3).

    ``matrix_world`` is any 4x4 (e.g. a ``mathutils.Matrix``); ``mm_per_bu`` is
    ``scale_length * 1000``.
    """
    m = np.asarray(matrix_world, np.float64)
    c = np.asarray(co).reshape(-1, 3).astype(np.float64, copy=False)
    return ((c @ m[:3, :3].T + m[:3, 3]) * mm_per_bu).astype(np.float32)


def is_mirrored(matrix_world) -> bool:
    """True for a negative-determinant (mirroring) matrix, which flips triangle winding."""
    return float(np.linalg.det(np.asarray(matrix_world, np.float64)[:3, :3])) < 0.0


def oriented_triangles(tri: np.ndarray, matrix_world) -> np.ndarray:
    """(n, 3) int32 triangles with winding corrected so normals stay outward under a mirror."""
    t = np.asarray(tri).reshape(-1, 3)
    return t[:, ::-1].copy() if is_mirrored(matrix_world) else t


def bed_transform_to_world(placement: np.ndarray, matrix_world_old, mm_per_bu: float) -> np.ndarray:
    """04 section 4.5: ``M_world_new = S^-1 . T . S . M_world_old`` with S the BU -> mm scale (4x4, float64)."""
    s = np.diag([mm_per_bu, mm_per_bu, mm_per_bu, 1.0])
    return np.linalg.inv(s) @ np.asarray(placement, np.float64) @ s @ np.asarray(matrix_world_old, np.float64)
