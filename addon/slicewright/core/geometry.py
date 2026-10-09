# SPDX-License-Identifier: GPL-3.0-or-later
"""2D geometry for the bed and plate checks (03 sections 1.3 and 4.3). No ``bpy``.

Everything here is in millimetres, in the G-code frame.
"""
from __future__ import annotations

import numpy as np


def parse_points(text: str) -> np.ndarray:
    """Parse an Orca ``points`` value (``"0x0,256x0,256x256,0x256"``) into an (n, 2) float64 array.

    An empty string is an empty (0, 2) array. Raises ``ValueError`` on anything else malformed.
    """
    text = text.strip()
    if not text:
        return np.zeros((0, 2), np.float64)
    pts = []
    for part in text.split(","):
        coords = part.strip().strip('"').lower().split("x")
        if len(coords) != 2:
            raise ValueError(f"not an XxY point: {part!r}")
        pts.append((float(coords[0]), float(coords[1])))
    return np.asarray(pts, np.float64)


def polygon_bounds(poly: np.ndarray) -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) of a non-empty polygon."""
    lo, hi = poly.min(axis=0), poly.max(axis=0)
    return float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])


def hatch_segments(poly: np.ndarray, spacing: float, angle_deg: float = 0.0) -> np.ndarray:
    """Parallel lines every ``spacing`` mm clipped to ``poly``, as an (m, 2, 2) array of segments.

    Lines run along ``angle_deg`` and sit on multiples of ``spacing`` measured from the origin, so
    a 10 mm grid is anchored at the G-code origin. Works for any simple (non-self-intersecting)
    polygon, convex or not.
    """
    if len(poly) < 3 or spacing <= 0:
        return np.zeros((0, 2, 2), np.float64)
    t = np.radians(angle_deg)
    c, s = np.cos(t), np.sin(t)
    fwd = np.array([[c, s], [-s, c]])        # world -> frame whose x axis is the line direction
    q = poly @ fwd.T
    nxt = np.roll(q, -1, axis=0)
    y0, y1 = q[:, 1], nxt[:, 1]
    k_lo = int(np.ceil(y0.min() / spacing))
    k_hi = int(np.floor(y0.max() / spacing))
    out = []
    for k in range(k_lo, k_hi + 1):
        y = k * spacing
        # Half-open rule so a line through a vertex is counted once per edge pair.
        crosses = (y0 <= y) != (y1 <= y)
        if not crosses.any():
            continue
        a, b = q[crosses], nxt[crosses]
        x = a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
        x.sort()
        for i in range(0, len(x) - 1, 2):
            if x[i + 1] - x[i] > 1e-9:
                out.append(((x[i], y), (x[i + 1], y)))
    if not out:
        return np.zeros((0, 2, 2), np.float64)
    seg = np.asarray(out, np.float64)
    return seg @ fwd           # frame -> world (fwd is a rotation: inverse is the transpose)


def convex_hull(points: np.ndarray) -> np.ndarray:
    """Convex hull of (n, 2) points, counter-clockwise, without repeated points ((k, 2); k <= 2 if degenerate).

    Exact. A vectorised pass first discards points strictly inside the octagon of extreme
    points (Akl-Toussaint), so a million-vertex mesh costs a few numpy passes plus a small
    monotone-chain sort.
    """
    pts = np.asarray(points, np.float64).reshape(-1, 2)
    if len(pts) > 64:
        pts = _akl_toussaint(pts)
    pts = np.unique(pts, axis=0)
    if len(pts) <= 2:
        return pts
    order = np.lexsort((pts[:, 1], pts[:, 0]))
    pts = pts[order]

    def half(seq):
        out: list = []
        for p in seq:
            while len(out) >= 2 and _cross(out[-2], out[-1], p) <= 0:
                out.pop()
            out.append(p)
        return out
    lower, upper = half(pts), half(pts[::-1])
    return np.asarray(lower[:-1] + upper[:-1], np.float64)


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _akl_toussaint(pts: np.ndarray) -> np.ndarray:
    s, d = pts[:, 0] + pts[:, 1], pts[:, 0] - pts[:, 1]
    ext = np.array([pts[np.argmin(pts[:, 0])], pts[np.argmin(s)], pts[np.argmin(pts[:, 1])],
                    pts[np.argmax(d)], pts[np.argmax(pts[:, 0])], pts[np.argmax(s)],
                    pts[np.argmax(pts[:, 1])], pts[np.argmin(d)]])
    inside = np.ones(len(pts), bool)
    # ``ext`` runs counter-clockwise; a point strictly left of every octagon edge is interior.
    for i in range(len(ext)):
        a, b = ext[i], ext[(i + 1) % len(ext)]
        if np.allclose(a, b):
            continue
        cross = (b[0] - a[0]) * (pts[:, 1] - a[1]) - (b[1] - a[1]) * (pts[:, 0] - a[0])
        inside &= cross > 1e-12
    return pts[~inside]


def points_in_polygon(pts: np.ndarray, poly: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Boolean per point: inside or on the boundary (within ``eps``) of a simple polygon."""
    pts = np.asarray(pts, np.float64).reshape(-1, 2)
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), bool)
    on_edge = np.zeros(len(pts), bool)
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        # even-odd ray cast towards +x
        cond = (a[1] > y) != (b[1] > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
        inside ^= cond & (x < xi)
        # distance to the segment
        ab = b - a
        t = np.clip(((pts - a) @ ab) / max(float(ab @ ab), 1e-300), 0.0, 1.0)
        d = np.linalg.norm(pts - (a + t[:, None] * ab), axis=1)
        on_edge |= d <= eps
    return inside | on_edge


def convex_overlap(a: np.ndarray, b: np.ndarray, eps: float = 1e-6) -> bool:
    """Do two convex polygons overlap by more than ``eps`` (touching edges do not count)? Separating axes."""
    if len(a) < 3 or len(b) < 3:
        return False
    for poly in (a, b):
        for i in range(len(poly)):
            e = poly[(i + 1) % len(poly)] - poly[i]
            axis = np.array([-e[1], e[0]])
            norm = np.linalg.norm(axis)
            if norm == 0:
                continue
            axis /= norm
            pa, pb = a @ axis, b @ axis
            if pa.max() <= pb.min() + eps or pb.max() <= pa.min() + eps:
                return False
    return True
