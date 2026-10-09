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
