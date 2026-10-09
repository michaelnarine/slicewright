# SPDX-License-Identifier: GPL-3.0-or-later
"""Paint attribute names and values (03 section 5.1) and pure helpers. No ``bpy``."""
from __future__ import annotations

import numpy as np

ATTR_SUPPORT = "slicewright_support"
ATTR_SEAM = "slicewright_seam"
ATTR_FILAMENT = "slicewright_filament"
ATTRIBUTES = (ATTR_SUPPORT, ATTR_SEAM, ATTR_FILAMENT)

NONE, ENFORCE, BLOCK = 0, 1, 2          # support and seam values
MAX_FILAMENT = 16                       # Orca's paint state limit (04 section 2.4)

DEFAULT_OVERHANG_ANGLE = 30.0           # degrees, until support_threshold_angle is wired in (plan M3)


def overhang_mask(normals: np.ndarray, angle_deg: float) -> np.ndarray:
    """Which unit ``normals`` ((n, 3), world space) face downward: ``normal . (0, 0, -1) > cos(angle)``.

    Per 03 section 5.2, with ``angle_deg`` taken from ``support_threshold_angle``.
    """
    n = np.asarray(normals, np.float64).reshape(-1, 3)
    return (-n[:, 2]) > np.cos(np.radians(angle_deg))


def world_normals(normals: np.ndarray, matrix_world) -> np.ndarray:
    """Object-space normals -> unit world-space normals (inverse transpose, so non-uniform scale is right)."""
    m = np.asarray(matrix_world, np.float64)[:3, :3]
    n = np.asarray(normals, np.float64).reshape(-1, 3) @ np.linalg.inv(m)
    length = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.where(length == 0, 1.0, length)
