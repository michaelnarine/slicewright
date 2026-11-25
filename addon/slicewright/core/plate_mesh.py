# SPDX-License-Identifier: GPL-3.0-or-later
"""The build-plate slab (03 section 1.3): a flat, slightly thick surface under Z = 0 following ``printable_area``. No ``bpy``.

Pure numpy / Python: an ear-clipping triangulation for any simple polygon (convex or not, either winding, with a
repeated closing point or collinear points) and the slab built from it. Geometry only, in millimetres; the draw
handler (``blender/bed_draw.py``) uploads it. The top face sits ``EPS_MM`` below Z = 0 so it never z-fights with the
grid and the first layer, which are drawn at Z = 0.
"""
from __future__ import annotations

import numpy as np

THICKNESS_MM = 4.0
EPS_MM = 0.15


def signed_area(poly: np.ndarray) -> float:
    """Shoelace area of ``poly`` (n, 2): positive for counter-clockwise."""
    p = np.asarray(poly, np.float64).reshape(-1, 2)
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _clean(poly: np.ndarray) -> list[int]:
    """Indices of ``poly`` without consecutive duplicates (and without a closing copy of the first point)."""
    p = np.asarray(poly, np.float64).reshape(-1, 2)
    keep: list[int] = []
    for i in range(len(p)):
        if not keep or not np.allclose(p[i], p[keep[-1]]):
            keep.append(i)
    while len(keep) > 1 and np.allclose(p[keep[0]], p[keep[-1]]):
        keep.pop()
    return keep


def _cross(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _in_triangle(p, a, b, c, tol: float) -> bool:
    """``p`` inside or on the boundary of the counter-clockwise triangle ``abc``."""
    return _cross(a, b, p) >= -tol and _cross(b, c, p) >= -tol and _cross(c, a, p) >= -tol


def triangulate(poly) -> np.ndarray:
    """Ear-clipping triangulation of a simple polygon: ``(k, 3)`` int array of indices into ``poly``, every
    triangle counter-clockwise. Collinear vertices and a repeated closing point are dropped; fewer than three usable
    vertices give an empty ``(0, 3)`` array. The triangle areas add up to the polygon's area."""
    p = np.asarray(poly, np.float64).reshape(-1, 2)
    idx = _clean(p)
    if len(idx) < 3:
        return np.zeros((0, 3), np.int64)
    if signed_area(p[idx]) < 0:
        idx.reverse()
    scale = float(np.ptp(p, axis=0).max()) or 1.0
    tol = 1e-12 * scale * scale
    out: list[tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3 and guard < 10 * len(p) ** 2 + 10:
        guard += 1
        n = len(idx)
        ear = None
        for k in range(n):
            ia, ib, ic = idx[k - 1], idx[k], idx[(k + 1) % n]
            a, b, c = p[ia], p[ib], p[ic]
            cr = _cross(a, b, c)
            if abs(cr) <= tol:                                   # collinear: the vertex adds nothing
                ear = (k, None)
                break
            if cr < 0:                                           # reflex
                continue
            if any(_in_triangle(p[j], a, b, c, tol) and not (np.allclose(p[j], a) or np.allclose(p[j], b)
                                                              or np.allclose(p[j], c))
                   for j in idx if j not in (ia, ib, ic)):
                continue
            ear = (k, (ia, ib, ic))
            break
        if ear is None:                                          # numerically stuck: clip the sharpest convex corner
            best = max(range(n), key=lambda k: _cross(p[idx[k - 1]], p[idx[k]], p[idx[(k + 1) % n]]))
            ear = (best, (idx[best - 1], idx[best], idx[(best + 1) % n]))
        k, tri = ear
        if tri is not None:
            out.append(tri)
        del idx[k]
    if len(idx) == 3 and abs(_cross(p[idx[0]], p[idx[1]], p[idx[2]])) > tol:
        out.append((idx[0], idx[1], idx[2]))
    return np.asarray(out, np.int64).reshape(-1, 3)


def slab(poly, thickness: float = THICKNESS_MM, eps: float = EPS_MM) -> dict[str, np.ndarray]:
    """The plate slab as flat triangle lists (float32, shape ``(3k, 3)``, mm): ``top`` at ``Z = -eps`` (facing up),
    ``bottom`` at ``Z = -eps - thickness`` (facing down) and ``sides`` (outward quads as two triangles per edge).
    Empty arrays for a polygon with fewer than three usable points."""
    p = np.asarray(poly, np.float64).reshape(-1, 2)
    tris = triangulate(p)
    empty = np.zeros((0, 3), np.float32)
    if not len(tris):
        return {"top": empty, "bottom": empty, "sides": empty}
    z_top, z_bot = -float(eps), -float(eps) - float(thickness)

    def lift(xy, z):
        return np.column_stack([xy, np.full(len(xy), z)])

    top = lift(p[tris.ravel()], z_top)
    bottom = lift(p[tris[:, ::-1].ravel()], z_bot)
    loop = p[_clean(p)]
    if signed_area(loop) < 0:
        loop = loop[::-1]
    a, b = loop, np.roll(loop, -1, axis=0)                       # counter-clockwise edges: outward is to the right
    quad = np.stack([lift(a, z_bot), lift(b, z_bot), lift(b, z_top),
                     lift(a, z_bot), lift(b, z_top), lift(a, z_top)], axis=1).reshape(-1, 3)
    return {name: np.ascontiguousarray(v, np.float32) for name, v in (("top", top), ("bottom", bottom), ("sides", quad))}
