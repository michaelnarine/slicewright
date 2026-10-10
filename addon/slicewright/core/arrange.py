# SPDX-License-Identifier: GPL-3.0-or-later
"""Arrange helpers (03 section 4.6): stand-in meshes for the engine and the placement bookkeeping. No ``bpy``."""
from __future__ import annotations

import numpy as np

from .geometry import convex_hull


def hull_prism(vertices_mm: np.ndarray):
    """A closed, outward-wound prism over the XY convex hull of ``vertices_mm`` ((n, 3), bed frame),
    spanning the vertices' Z range. Returns ``(vertices float32 (2k, 3), triangles int32 (4k - 4, 3))``,
    or None when the hull is degenerate (fewer than three points) or has no height.

    The engine only needs a footprint to arrange, so a prism keeps the arrange job tiny however
    detailed the real mesh is.
    """
    v = np.asarray(vertices_mm, np.float64).reshape(-1, 3)
    if len(v) == 0:
        return None
    hull = convex_hull(v[:, :2])
    k = len(hull)
    z0, z1 = float(v[:, 2].min()), float(v[:, 2].max())
    if k < 3:
        return None
    if z1 - z0 < 1e-6:
        z1 = z0 + 1e-3             # a flat sheet still has a footprint; give it a sliver of height
    bottom = np.column_stack([hull, np.full(k, z0)])
    top = np.column_stack([hull, np.full(k, z1)])
    verts = np.concatenate([bottom, top]).astype(np.float32)
    tris = []
    for i in range(1, k - 1):
        tris.append((0, i + 1, i))                         # bottom faces down: reversed fan
        tris.append((k, k + i, k + i + 1))                 # top faces up
    for i in range(k):
        j = (i + 1) % k
        tris.append((i, j, k + j))
        tris.append((i, k + j, k + i))
    return verts, np.asarray(tris, np.int32)


def placement_matrices(placements: list[dict], matrices_old: dict[int, np.ndarray], mm_per_bu: float):
    """``{index: M_world_new}`` for each placement, per 04 section 4.5: ``S^-1 . T . S . M_world_old``."""
    from .transform import bed_transform_to_world
    return {p["index"]: bed_transform_to_world(p["transform"], matrices_old[p["index"]], mm_per_bu)
            for p in placements}
