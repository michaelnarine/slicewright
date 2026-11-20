# SPDX-License-Identifier: GPL-3.0-or-later
"""Plate thumbnails for the G-code header (03 section 6.2, 04 section 4.1). No ``bpy``.

The sizes come from the printer's ``thumbnails`` key (``"48x48/PNG, 300x300/PNG"``): each entry is a size and the
format the engine encodes it in. The add-on renders one RGBA image per distinct size and hands them to
``SliceJob.set_thumbnails``; the engine picks the image of exactly each size, encodes it and embeds it. This module
is the part that needs no GPU: parsing the key, framing the plate from a fixed isometric view, shading its triangles
(flat, one directional light, filament colours), turning a GPU read-back into the engine's image layout and
assembling the list. ``blender/thumbnails.py`` draws the triangles into a ``GPUOffScreen``.
"""
from __future__ import annotations

import re
from typing import Callable, NamedTuple, Sequence

import numpy as np

MAX_SIDE = 4096                     # larger entries are ignored: no printer reads them, and the buffer is w*h*4 bytes
_SPEC_RE = re.compile(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*(?:/\s*([A-Za-z0-9]+))?\s*$")
DEFAULT_COLOUR = (0.8, 0.8, 0.8)

# The fixed isometric view: from the front right and above, looking at the middle of the plate.
VIEW_DIRECTION = (1.0, -1.2, 0.9)   # from the target towards the eye, world (bed) frame
LIGHT_DIRECTION = (0.45, -0.35, 0.85)
AMBIENT = 0.38
MARGIN = 1.08                       # the framed plate leaves this much room around it


class Skipped(Exception):
    """Thumbnails cannot be made here and that is expected (background mode, no GPU): the message says why."""


class ThumbSpec(NamedTuple):
    width: int
    height: int
    format: str                     # upper case, as the config spells it ("PNG", "JPG", "QOI"); "" when omitted


def parse_specs(text: str | None) -> list[ThumbSpec]:
    """The entries of a ``thumbnails`` value, in order; malformed, zero-sized or oversized ones are dropped."""
    specs = []
    for part in re.split(r"[,;]", text or ""):
        m = _SPEC_RE.match(part)
        if not m:
            continue
        w, h = int(m.group(1)), int(m.group(2))
        if 0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE:
            specs.append(ThumbSpec(w, h, (m.group(3) or "").upper()))
    return specs


def distinct_sizes(specs: Sequence[ThumbSpec]) -> list[tuple[int, int]]:
    """``(width, height)`` of each distinct size, first occurrence first (one image serves every format of a size)."""
    seen: list[tuple[int, int]] = []
    for s in specs:
        if (s.width, s.height) not in seen:
            seen.append((s.width, s.height))
    return seen


def parse_colour(value, default=DEFAULT_COLOUR) -> tuple[float, float, float]:
    """``#RRGGBB`` (or a one-item list of it, as ``filament_colour`` is) as floats; ``default`` if unreadable."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    m = re.match(r"^\s*#?([0-9a-fA-F]{6})", str(value or ""))
    if not m:
        return default
    h = m.group(1)
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))      # type: ignore[return-value]


# -- framing ------------------------------------------------------------------------------------------------

def bounds(vertex_arrays: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray] | None:
    """``(min, max)`` corners over the vertex arrays, or ``None`` when there are no vertices."""
    arrays = [np.asarray(v, np.float64).reshape(-1, 3) for v in vertex_arrays if len(v)]
    if not arrays:
        return None
    pts = np.concatenate(arrays)
    return pts.min(axis=0), pts.max(axis=0)


def look_at(eye, target, up=(0.0, 0.0, 1.0)) -> np.ndarray:
    """The 4x4 world-to-view matrix of a camera at ``eye`` looking at ``target`` (looks down -Z, like Blender's)."""
    eye, target, up = (np.asarray(v, np.float64) for v in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def ortho(left: float, right: float, bottom: float, top: float, near: float, far: float) -> np.ndarray:
    """The OpenGL orthographic projection matrix (view space looks down -Z; ``near`` and ``far`` are distances)."""
    m = np.eye(4)
    m[0, 0], m[0, 3] = 2.0 / (right - left), -(right + left) / (right - left)
    m[1, 1], m[1, 3] = 2.0 / (top - bottom), -(top + bottom) / (top - bottom)
    m[2, 2], m[2, 3] = -2.0 / (far - near), -(far + near) / (far - near)
    return m


def iso_matrices(lo, hi, aspect: float) -> tuple[np.ndarray, np.ndarray]:
    """``(view, projection)`` framing the box ``lo``..``hi`` from the fixed isometric direction, centred, with the
    whole box inside the image for an image of width/height ``aspect``."""
    lo, hi = np.asarray(lo, np.float64), np.asarray(hi, np.float64)
    centre = (lo + hi) / 2.0
    radius = max(float(np.linalg.norm(hi - lo)) / 2.0, 1e-3)
    direction = np.asarray(VIEW_DIRECTION, np.float64)
    direction /= np.linalg.norm(direction)
    view = look_at(centre + direction * (radius * 3.0), centre)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    cam = (view[:3, :3] @ corners.T).T + view[:3, 3]
    (xmin, ymin, _), (xmax, ymax, _) = cam.min(axis=0), cam.max(axis=0)
    half_w, half_h = max((xmax - xmin) / 2.0, 1e-3) * MARGIN, max((ymax - ymin) / 2.0, 1e-3) * MARGIN
    if half_w / half_h < aspect:                 # the plate is narrower than the image: widen the frame
        half_w = half_h * aspect
    else:
        half_h = half_w / aspect
    cx, cy = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
    near = -float(cam[:, 2].max()) - radius * 0.5
    far = -float(cam[:, 2].min()) + radius * 0.5
    proj = ortho(cx - half_w, cx + half_w, cy - half_h, cy + half_h, near, far)
    return view, proj


# -- shading ------------------------------------------------------------------------------------------------

def shaded_triangles(meshes: Sequence[tuple[np.ndarray, np.ndarray, Sequence[float]]]
                     ) -> tuple[np.ndarray, np.ndarray]:
    """Flat-shaded, non-indexed triangles of ``meshes`` (``(vertices, triangles, rgb)`` each).

    Returns ``(positions (3T, 3) float32, colours (3T, 4) float32)``; a face is lit by its normal against one
    directional light plus an ambient term, so every face of a mesh has its own shade."""
    light = np.asarray(LIGHT_DIRECTION, np.float64)
    light /= np.linalg.norm(light)
    positions, colours = [], []
    for vertices, triangles, rgb in meshes:
        v = np.asarray(vertices, np.float64).reshape(-1, 3)
        t = np.asarray(triangles, np.int64).reshape(-1, 3)
        if not len(t):
            continue
        tri = v[t]                                                   # (T, 3, 3)
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        length = np.linalg.norm(n, axis=1, keepdims=True)
        n = np.divide(n, length, out=np.zeros_like(n), where=length > 0)
        shade = AMBIENT + (1.0 - AMBIENT) * np.clip(n @ light, 0.0, 1.0)
        rgba = np.empty((len(t), 4), np.float64)
        rgba[:, :3] = np.clip(np.asarray(rgb, np.float64)[None, :3] * shade[:, None], 0.0, 1.0)
        rgba[:, 3] = 1.0
        positions.append(tri.reshape(-1, 3))
        colours.append(np.repeat(rgba, 3, axis=0))
    if not positions:
        return np.zeros((0, 3), np.float32), np.zeros((0, 4), np.float32)
    return np.concatenate(positions).astype(np.float32), np.concatenate(colours).astype(np.float32)


# -- images -------------------------------------------------------------------------------------------------

def engine_image(pixels: np.ndarray, width: int, height: int) -> np.ndarray:
    """A GPU read-back (OpenGL order: row 0 is the **bottom**) as the engine's image: uint8 ``(H, W, 4)`` RGBA with
    row 0 at the **top** (04 section 4.1), contiguous and owned."""
    a = np.asarray(pixels)
    if a.size != width * height * 4:
        raise ValueError(f"expected {width}x{height} RGBA pixels, got {a.size} values")
    a = a.reshape(height, width, 4)
    if a.dtype != np.uint8:
        a = np.clip(np.rint(a * 255.0), 0, 255).astype(np.uint8) if a.dtype.kind == "f" else a.astype(np.uint8)
    return np.ascontiguousarray(a[::-1])


def make_images(sizes: Sequence[tuple[int, int]], render: Callable[[int, int], np.ndarray]) -> list[np.ndarray]:
    """One engine image per size, drawn by ``render(width, height)`` (which returns a bottom-up read-back)."""
    return [engine_image(render(w, h), w, h) for w, h in sizes]
