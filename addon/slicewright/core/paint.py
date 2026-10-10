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


# Overlay colours (03 section 5.3). RGBA, alpha is the overlay opacity; plain data.
SUPPORT_ENFORCE_RGBA = (0.20, 0.80, 0.30, 0.60)    # green
SUPPORT_BLOCK_RGBA = (0.90, 0.15, 0.15, 0.60)      # red
SEAM_ENFORCE_RGBA = (0.10, 0.80, 0.90, 0.65)       # cyan
SEAM_BLOCK_RGBA = (0.85, 0.20, 0.80, 0.65)         # magenta
# Filament slot colours until the filament slots exist (plan M3): sRGB-ish, distinct, 16 entries.
DEFAULT_SLOT_COLORS = (
    (0.95, 0.60, 0.10), (0.20, 0.45, 0.90), (0.95, 0.85, 0.15), (0.55, 0.30, 0.75),
    (0.10, 0.70, 0.55), (0.90, 0.35, 0.50), (0.60, 0.60, 0.60), (0.45, 0.30, 0.15),
    (0.75, 0.90, 0.25), (0.15, 0.30, 0.55), (0.95, 0.75, 0.65), (0.35, 0.60, 0.25),
    (0.80, 0.50, 0.10), (0.30, 0.80, 0.90), (0.70, 0.15, 0.40), (0.85, 0.85, 0.85))
FILAMENT_ALPHA = 0.55


def face_colors(support=None, seam=None, filament=None, slot_colors=DEFAULT_SLOT_COLORS) -> np.ndarray:
    """Per-face RGBA float32 (n, 4) for the overlay; alpha 0 means "not painted, draw nothing".

    Any of the three int arrays may be None. Where several are set the priority is support, then
    seam, then filament. Filament values above the palette wrap onto it (the engine reports
    ``paint_out_of_range`` for values above the filament count).
    """
    given = [a for a in (support, seam, filament) if a is not None]
    if not given:
        return np.zeros((0, 4), np.float32)
    n = len(given[0])
    out = np.zeros((n, 4), np.float32)
    if filament is not None:
        f = np.asarray(filament)
        palette = np.array([(*c, FILAMENT_ALPHA) for c in slot_colors], np.float32)
        on = f > 0
        out[on] = palette[(f[on] - 1) % len(palette)]
    for values, enforce, block in ((seam, SEAM_ENFORCE_RGBA, SEAM_BLOCK_RGBA),
                                   (support, SUPPORT_ENFORCE_RGBA, SUPPORT_BLOCK_RGBA)):
        if values is None:
            continue
        v = np.asarray(values)
        out[v == ENFORCE] = enforce
        out[v == BLOCK] = block
    return out


def triangle_overlay(co: np.ndarray, tri: np.ndarray, poly: np.ndarray, face_rgba: np.ndarray):
    """Painted-triangle vertex data: ``(positions (3k, 3) float32, colours (3k, 4) float32)``.

    ``co`` (v, 3) vertices, ``tri`` (m, 3) vertex indices, ``poly`` (m,) face index per triangle,
    ``face_rgba`` (faces, 4). Only triangles whose face has alpha > 0 are kept.
    """
    tri_rgba = face_rgba[poly]
    keep = tri_rgba[:, 3] > 0
    if not keep.any():
        return np.zeros((0, 3), np.float32), np.zeros((0, 4), np.float32)
    pos = np.asarray(co, np.float32).reshape(-1, 3)[np.asarray(tri).reshape(-1, 3)[keep]].reshape(-1, 3)
    return np.ascontiguousarray(pos), np.ascontiguousarray(np.repeat(tri_rgba[keep], 3, axis=0))
