# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure helpers for the object-mode paint brush (03 section 5.2). No ``bpy``."""
from __future__ import annotations

from collections import deque

import numpy as np

from .paint import ATTR_FILAMENT, ATTR_SEAM, ATTR_SUPPORT, BLOCK, ENFORCE

KINDS = (
    ("SUPPORT_ENFORCE", "Support enforcer", ATTR_SUPPORT, ENFORCE),
    ("SUPPORT_BLOCK", "Support blocker", ATTR_SUPPORT, BLOCK),
    ("SEAM_ENFORCE", "Seam enforcer", ATTR_SEAM, ENFORCE),
    ("SEAM_BLOCK", "Seam blocker", ATTR_SEAM, BLOCK),
    ("FILAMENT", "Filament", ATTR_FILAMENT, None),   # the value is the chosen slot
)
_BY_ID = {k[0]: k for k in KINDS}


def brush_target(kind: str, filament_slot: int = 1) -> tuple[str, int]:
    """The ``(attribute name, painted value)`` for a brush kind."""
    _, _, attr, value = _BY_ID[kind]
    return attr, int(filament_slot if value is None else value)


def face_adjacency(edge_of_loop: np.ndarray, face_of_loop: np.ndarray, n_faces: int) -> list[np.ndarray]:
    """Faces sharing a manifold edge, per face: an array of neighbour face indices.

    ``edge_of_loop[i]`` and ``face_of_loop[i]`` describe loop ``i``. An edge used by exactly two
    faces links them; open and over-shared edges link nothing.
    """
    edge_of_loop = np.asarray(edge_of_loop, np.int64)
    face_of_loop = np.asarray(face_of_loop, np.int64)
    order = np.argsort(edge_of_loop, kind="stable")
    e, f = edge_of_loop[order], face_of_loop[order]
    starts = np.flatnonzero(np.r_[True, e[1:] != e[:-1]])
    counts = np.diff(np.r_[starts, len(e)])
    pairs = starts[counts == 2]
    a, b = f[pairs], f[pairs + 1]
    keep = a != b
    a, b = a[keep], b[keep]
    src = np.concatenate([a, b])
    dst = np.concatenate([b, a])
    out: list[list[int]] = [[] for _ in range(n_faces)]
    for s, d in zip(src.tolist(), dst.tolist()):
        out[s].append(d)
    return [np.asarray(x, np.int64) for x in out]


def grow_region(seed: int, candidates, normals: np.ndarray, adjacency: list[np.ndarray],
                max_angle_deg: float) -> set[int]:
    """Smart fill: faces connected to ``seed`` through ``candidates`` whose normal is within
    ``max_angle_deg`` of the *neighbour it was reached from* (so gentle curves fill, creases stop).
    """
    allowed = set(int(c) for c in candidates)
    if seed not in allowed:
        return set()
    cos_limit = np.cos(np.radians(max_angle_deg))
    seen = {seed}
    queue = deque([seed])
    while queue:
        cur = queue.popleft()
        for nb in adjacency[cur].tolist():
            if nb in seen or nb not in allowed:
                continue
            if float(normals[cur] @ normals[nb]) >= cos_limit:
                seen.add(nb)
                queue.append(nb)
    return seen
