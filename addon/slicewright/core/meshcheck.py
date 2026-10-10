# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh sanity checks on triangle arrays (03 section 4.3). No ``bpy``."""
from __future__ import annotations

import numpy as np

LARGE_MESH_TRIANGLES = 5_000_000


def edge_keys(tri: np.ndarray) -> np.ndarray:
    """One int64 per triangle edge, ``min << 32 | max``, so each undirected edge has one key."""
    t = np.asarray(tri, np.int64).reshape(-1, 3)
    a = np.concatenate([t[:, 0], t[:, 1], t[:, 2]])
    b = np.concatenate([t[:, 1], t[:, 2], t[:, 0]])
    return (np.minimum(a, b) << 32) | np.maximum(a, b)


def non_manifold_edges(tri: np.ndarray) -> np.ndarray:
    """(k, 2) int64 vertex pairs of edges not shared by exactly two triangles (open or over-shared)."""
    if len(tri) == 0:
        return np.zeros((0, 2), np.int64)
    keys, counts = np.unique(edge_keys(tri), return_counts=True)
    bad = keys[counts != 2]
    return np.stack([bad >> 32, bad & 0xFFFFFFFF], axis=1)


def signed_volume(vertices: np.ndarray, tri: np.ndarray) -> float:
    """Signed volume of a triangle mesh (positive for outward winding), in the vertices' units cubed."""
    if len(tri) == 0:
        return 0.0
    v = np.asarray(vertices, np.float64)
    t = np.asarray(tri).reshape(-1, 3)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def is_degenerate(vertices: np.ndarray, tri: np.ndarray) -> bool:
    """No triangles, or zero volume (|signed volume| under 1e-9 of the bounding box's cube)."""
    if len(tri) == 0 or len(vertices) == 0:
        return True
    extent = float(np.ptp(np.asarray(vertices, np.float64), axis=0).max())
    return extent <= 1e-9 or abs(signed_volume(vertices, tri)) <= 1e-9 * extent ** 3
