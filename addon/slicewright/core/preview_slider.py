# SPDX-License-Identifier: GPL-3.0-or-later
"""The Preview's layer range slider and move slider: geometry, hit testing, values and drag rules (03 section 7.4a).

Pure Python (no ``bpy``). All lengths are region pixels with the origin at the bottom left, as the viewport region
reports them; the caller passes ``scale`` (pixels per UI unit: ``system.ui_scale``) and the insets that other regions
(the sidebar, the toolbar, an overlapping header) take from the viewport.

* The **layer slider** is vertical, at the right edge: two thumbs (``lo`` below, ``hi`` above), layer 0 at the bottom.
* The **move slider** is horizontal along the bottom: the last visible move inside the top layer. ``None`` (the whole
  layer, as :mod:`preview_nav` has it) sits at the right end.

Nothing here knows about Blender properties: the functions take and return :class:`preview_nav.Scrub` values, which the
operator writes to the preview settings, so the panel's fields and the renderer stay the source of truth.

For non-planar results (ZAA, relief) a layer is the engine's layer index, not one Z plane; a better scrubbing control
for those is a later task (the items here still work, they just walk the layer table).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .preview_nav import Scrub

# Hit kinds
LO, HI, BAR, TRACK = "LO", "HI", "BAR", "TRACK"                 # the layer slider
MOVE_THUMB, MOVE_TRACK = "MOVE_THUMB", "MOVE_TRACK"             # the move slider
LAYER_KINDS = (LO, HI, BAR, TRACK)
MOVE_KINDS = (MOVE_THUMB, MOVE_TRACK)

# Sizes in UI units (multiplied by the scale)
MARGIN_RIGHT = 20.0           # track centre from the right edge of the free viewport
THUMB_R = 8.0
TRACK_W = 4.0
HIT_HALF = 14.0               # half width of the clickable column / half height of the clickable move row
RESERVE_TOP = 130.0           # the viewport's navigation gizmo lives at the top right
RESERVE_BOTTOM_LAYER = 84.0   # the layer track starts above the move slider row
MOVE_Y = 34.0                 # move slider row, from the bottom of the free viewport
MIN_TRACK = 60.0              # shorter than this: not drawn, not clickable
MOVE_MIN, MOVE_MAX = 200.0, 520.0
STEP_SHIFT_LAYERS = 10        # Shift: faster (the arrow keys of preview_step use the same numbers)
STEP_SHIFT_MOVES = 100


@dataclass(frozen=True)
class Insets:
    left: float = 0.0
    right: float = 0.0
    top: float = 0.0
    bottom: float = 0.0


@dataclass(frozen=True)
class Geometry:
    scale: float
    track_x: float = 0.0
    track_y0: float = 0.0            # bottom end of the layer track (layer 0)
    track_y1: float = 0.0            # top end (the last layer)
    move_x0: float = 0.0
    move_x1: float = 0.0
    move_y: float = 0.0
    show_layers: bool = False
    show_move: bool = False

    @property
    def thumb_r(self) -> float:
        return THUMB_R * self.scale

    @property
    def hit_half(self) -> float:
        return HIT_HALF * self.scale


def insets_from_rects(region: tuple, others) -> Insets:
    """Insets taken from the viewport rectangle ``region`` (x, y, w, h in window pixels) by overlapping regions
    ``others`` (same form): a wide one is a header or footer (top or bottom by its centre), a tall one the sidebar or
    toolbar (right or left). A region that only touches or sits outside (no overlap) takes nothing, and a hidden one
    (about one pixel) is ignored."""
    rx, ry, rw, rh = region
    left = right = top = bottom = 0.0
    for x, y, w, h in others:
        if w <= 2 or h <= 2:
            continue
        ox = min(x + w, rx + rw) - max(x, rx)
        oy = min(y + h, ry + rh) - max(y, ry)
        if ox <= 0 or oy <= 0:
            continue
        if w >= h:                                  # header / footer / tool header / asset shelf
            if y + h / 2 >= ry + rh / 2:
                top = max(top, ry + rh - y)
            else:
                bottom = max(bottom, y + h - ry)
        else:                                       # sidebar / toolbar
            if x + w / 2 >= rx + rw / 2:
                right = max(right, rx + rw - x)
            else:
                left = max(left, x + w - rx)
    return Insets(left, right, top, bottom)


def make_geometry(width: float, height: float, scale: float, insets: Insets = Insets()) -> Geometry:
    """Place both sliders in a ``width`` x ``height`` viewport region."""
    s = max(scale, 0.25)
    free_l, free_r = insets.left, width - insets.right
    free_b, free_t = insets.bottom, height - insets.top
    track_x = free_r - MARGIN_RIGHT * s
    y0 = free_b + RESERVE_BOTTOM_LAYER * s
    y1 = free_t - RESERVE_TOP * s
    show_layers = (y1 - y0) >= MIN_TRACK * s and track_x - free_l > 120.0 * s
    # The move slider sits left of the layer track's column, centred in what is left.
    avail = (track_x - 40.0 * s) - free_l
    length = min(max(avail * 0.55, MOVE_MIN * s), MOVE_MAX * s)
    show_move = avail >= MOVE_MIN * s * 0.8 and (free_t - free_b) > 160.0 * s
    length = min(length, avail)
    x0 = free_l + (avail - length) / 2.0 + 16.0 * s
    return Geometry(s, track_x, y0, max(y1, y0), x0, x0 + length, free_b + MOVE_Y * s, show_layers, show_move)


# -- values and pixels ---------------------------------------------------------------------------------------------

def layer_to_y(g: Geometry, i: float, n: int) -> float:
    if n <= 1:
        return g.track_y1
    return g.track_y0 + min(max(i, 0.0), n - 1) / (n - 1) * (g.track_y1 - g.track_y0)


def y_to_layer(g: Geometry, y: float, n: int) -> int:
    """The layer nearest to pixel ``y`` (clamped to ``0..n-1``)."""
    if n <= 1 or g.track_y1 <= g.track_y0:
        return 0
    t = (y - g.track_y0) / (g.track_y1 - g.track_y0)
    return min(max(int(round(t * (n - 1))), 0), n - 1)


def move_to_x(g: Geometry, pos: int | None, last: int) -> float:
    """Pixel of move offset ``pos`` (``None``: the whole layer, the right end) in a layer whose last move is ``last``."""
    if last <= 0 or pos is None:
        return g.move_x1
    return g.move_x0 + min(max(pos, 0), last) / last * (g.move_x1 - g.move_x0)


def x_to_move(g: Geometry, x: float, last: int) -> int | None:
    """The move offset for pixel ``x``; the right end (and any layer with one move) is ``None``, the whole layer."""
    if last <= 0 or g.move_x1 <= g.move_x0:
        return None
    t = (x - g.move_x0) / (g.move_x1 - g.move_x0)
    v = min(max(int(round(t * last)), 0), last)
    return None if v >= last else v


# -- hit testing ---------------------------------------------------------------------------------------------------

def hit_test(g: Geometry, x: float, y: float, s: Scrub, n: int, move_last: int) -> str | None:
    """What is under the pointer: a thumb, the bar between the thumbs, the bare track, or the move slider's parts.
    Thumbs win over the bar and the track. When both thumbs sit at one point the pointer's side picks one (above or
    level: the top thumb), so either can be pulled away."""
    r = g.thumb_r + 2.0 * g.scale
    if g.show_layers and n > 0:
        ylo, yhi = layer_to_y(g, s.lo, n), layer_to_y(g, s.hi, n)
        dx = x - g.track_x
        near_lo = math.hypot(dx, y - ylo) <= r
        near_hi = math.hypot(dx, y - yhi) <= r
        if near_hi and near_lo:
            return LO if y < (ylo + yhi) / 2 or (ylo == yhi and y < ylo) else HI
        if near_hi:
            return HI
        if near_lo:
            return LO
        if abs(dx) <= g.hit_half and g.track_y0 - r <= y <= g.track_y1 + r:
            return BAR if ylo < y < yhi else TRACK
    if g.show_move and n > 0:
        mx = move_to_x(g, s.pos, move_last)
        if math.hypot(x - mx, y - g.move_y) <= r:
            return MOVE_THUMB
        if abs(y - g.move_y) <= g.hit_half and g.move_x0 - r <= x <= g.move_x1 + r:
            return MOVE_TRACK
    return None


# -- the two-thumb rules -------------------------------------------------------------------------------------------

def set_thumb(s: Scrub, which: str, value: int, n: int, follow: bool = False) -> Scrub:
    """Move one thumb to layer ``value`` (clamped). ``lo <= hi`` always: by default a thumb stops at the other; with
    ``follow`` it pushes the other along (the bottom thumb dragged above the top pulls the top up, the top dragged below
    the bottom pushes the bottom down). A changed top layer drops the in-layer position, like ``preview_nav``."""
    if n <= 0:
        return Scrub(0, 0, None)
    v = min(max(int(value), 0), n - 1)
    lo, hi = min(max(s.lo, 0), n - 1), min(max(s.hi, 0), n - 1)
    lo = min(lo, hi)
    if which == HI:
        if v < lo:
            lo, hi = (v, v) if follow else (lo, lo)
        else:
            hi = v
    else:
        if v > hi:
            lo, hi = (v, v) if follow else (hi, hi)
        else:
            lo = v
    return replace(s, lo=lo, hi=hi, pos=s.pos if hi == s.hi else None)


def shift_range(s: Scrub, delta: int, n: int) -> Scrub:
    """Move the whole range by ``delta`` layers, keeping its span; it stops at either end."""
    if n <= 0:
        return Scrub(0, 0, None)
    span = max(s.hi - s.lo, 0)
    lo = min(max(s.lo + delta, 0), n - 1 - span) if span <= n - 1 else 0
    hi = min(lo + span, n - 1)
    return replace(s, lo=lo, hi=hi, pos=s.pos if hi == s.hi else None)


def set_move(s: Scrub, pos: int | None) -> Scrub:
    return replace(s, pos=pos)


def step_amount(shift: bool, kind: str = "layer") -> int:
    if kind == "move":
        return STEP_SHIFT_MOVES if shift else 1
    return STEP_SHIFT_LAYERS if shift else 1


def nearest_thumb(g: Geometry, y: float, s: Scrub, n: int) -> str:
    """The thumb nearest to pixel ``y`` (a tie, or one point, picks the top thumb): the mouse wheel steps this one."""
    ylo, yhi = layer_to_y(g, s.lo, n), layer_to_y(g, s.hi, n)
    return LO if abs(y - ylo) < abs(y - yhi) else HI


def step_layers(s: Scrub, which: str, delta: int, n: int, follow: bool = False) -> Scrub:
    """Step one thumb by ``delta`` layers (wheel, arrows)."""
    return set_thumb(s, which, (s.lo if which == LO else s.hi) + delta, n, follow)


def step_in_layer(s: Scrub, delta: int, last: int) -> Scrub:
    """Step the move position like ``preview_nav.step_move`` (the first step from the whole layer starts at its last move;
    onto or past the last move returns to the whole layer), for a top layer whose last move is ``last``."""
    if last <= 0:
        return replace(s, pos=None)
    pos = (last if s.pos is None else s.pos) + delta
    return replace(s, pos=None if pos >= last else max(pos, 0))


# -- dragging ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Drag:
    kind: str                 # LO, HI, BAR or MOVE_THUMB
    start: Scrub              # what to put back when the drag is cancelled
    grab: int = 0             # BAR: the layer under the pointer minus the bottom layer when it was grabbed


def begin_drag(g: Geometry, kind: str, x: float, y: float, s: Scrub, n: int, move_last: int,
               follow: bool = False) -> tuple[Drag | None, Scrub]:
    """Start a drag from a press on ``kind``. A press on the bare track jumps the nearer thumb there (the top one when
    above the range or level) and goes on dragging it; on the move track it jumps the move thumb."""
    if kind in (LO, HI):
        return Drag(kind, s), s
    if kind == BAR:
        return Drag(BAR, s, y_to_layer(g, y, n) - s.lo), s
    if kind == TRACK:
        layer = y_to_layer(g, y, n)
        which = HI if layer >= s.hi else LO if layer <= s.lo else (LO if layer - s.lo < s.hi - layer else HI)
        return Drag(which, s), set_thumb(s, which, layer, n, follow)
    if kind in MOVE_KINDS:
        return Drag(MOVE_THUMB, s), set_move(s, x_to_move(g, x, move_last))
    return None, s


def drag_to(g: Geometry, d: Drag, x: float, y: float, s: Scrub, n: int, move_last: int, follow: bool = False) -> Scrub:
    """The scrub for the pointer at (x, y) during drag ``d``; ``s`` is the current value."""
    if d.kind in (LO, HI):
        return set_thumb(s, d.kind, y_to_layer(g, y, n), n, follow)
    if d.kind == BAR:
        return shift_range(d.start, y_to_layer(g, y, n) - d.grab - d.start.lo, n)
    if d.kind == MOVE_THUMB:
        return set_move(s, x_to_move(g, x, move_last))
    return s


# -- text ----------------------------------------------------------------------------------------------------------

def format_z(z: float) -> str:
    return f"{z:.2f}"


def thumb_text(i: int, n: int, z: float) -> tuple[str, str]:
    """(layer line, height line) of a thumb: layers are numbered from 1 as in Bambu Studio and Orca."""
    return f"{i + 1}", f"{format_z(z)} mm"


def total_text(n: int) -> str:
    return f"{n} layer" + ("" if n == 1 else "s")


def move_text(pos: int | None, last: int) -> str:
    return f"{(last if pos is None else min(pos, last)) + 1} / {last + 1}"


# -- shapes for the draw code --------------------------------------------------------------------------------------

def circle_points(cx: float, cy: float, r: float, segments: int = 24) -> list[tuple[float, float]]:
    return [(cx + r * math.cos(2 * math.pi * k / segments), cy + r * math.sin(2 * math.pi * k / segments))
            for k in range(segments)]


def rounded_rect_points(x0: float, y0: float, x1: float, y1: float, radius: float,
                        corner_segments: int = 6) -> list[tuple[float, float]]:
    """Outline of a rounded rectangle, counter-clockwise (a triangle fan from its centre fills it)."""
    r = max(min(radius, (x1 - x0) / 2, (y1 - y0) / 2), 0.0)
    pts = []
    for cx, cy, a0 in ((x1 - r, y1 - r, 0.0), (x0 + r, y1 - r, 90.0), (x0 + r, y0 + r, 180.0), (x1 - r, y0 + r, 270.0)):
        for k in range(corner_segments + 1):
            a = math.radians(a0 + 90.0 * k / corner_segments)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


# Colours as sRGB ``0xRRGGBB`` (converted to scene-linear by ``preview_palette.rgb_linear`` where drawn) and alpha.
COLORS = {
    "backing": (0x15171C, 0.58), "track": (0x9AA0AA, 0.85), "range": (0x3FA0FF, 1.0), "thumb": (0xF2F4F8, 1.0),
    "thumb_ring": (0x3FA0FF, 1.0), "hover": (0xFFFFFF, 1.0), "active": (0xFFC83D, 1.0), "text": (0xFFFFFF, 1.0),
    "text_dim": (0xC3C8D2, 1.0), "chip": (0x15171C, 0.72),
}
