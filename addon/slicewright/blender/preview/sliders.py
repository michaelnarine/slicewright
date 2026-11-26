# SPDX-License-Identifier: GPL-3.0-or-later
"""The Preview's layer range slider and move slider, drawn over the 3D view (03 section 7.4a).

The geometry, hit testing and drag rules are pure (``core/preview_slider.py``). This module is what needs Blender:
reading the viewport (its size, the UI scale, the regions that overlap it), the shared hover and drag state, and the
``POST_PIXEL`` draw handler. The mouse is handled by ``slider_modal.py``.

The sliders write the preview settings (``layer_lo``, ``layer_hi``, ``scrub_moves``, ``move_pos``) and read them back
on every redraw, so the Preview panel's fields, the arrow keys and the renderer stay the source of truth.

Drawn only in 3D views of a scene that is in Preview with a result, and only when the Preview option "Sliders" is on.
For non-planar results (ZAA, relief) the layers are the engine's layer index: a better scrubbing control for those is
a later task.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import bpy

from ...core import preview_nav as nav
from ...core import preview_palette as palette
from ...core import preview_slider as ps
from . import props as preview_props
from . import runtime

log = logging.getLogger("slicewright.preview")
_handle = None
_error_logged = False

# Regions that can overlap the viewport and take room from the sliders (the redo panel and the like do not).
INSET_REGIONS = {"UI", "TOOLS", "HEADER", "TOOL_HEADER", "FOOTER", "ASSET_SHELF", "ASSET_SHELF_HEADER"}
# Measured in a real window (ui_shots step preview_sliders): POST_PIXEL colours reach the screen as written, with no view
# transform, unlike the POST_VIEW toolpaths (commit c499a29), so the sRGB values are used as they are. Writing them
# through ``rgb_linear`` here came out darker (0x3FA0FF read back as about 0x145AFF).
LINEAR_COLORS = False


class UiState:
    """Hover and drag, shared by the modal operator (writes) and the draw handler (reads)."""
    def __init__(self) -> None:
        self.area = 0                          # pointer of the area the hover or drag is in
        self.hover: str | None = None
        self.drag: ps.Drag | None = None

    def reset(self) -> None:
        self.area, self.hover, self.drag = 0, None, None


ui = UiState()


@dataclass
class View:
    geometry: ps.Geometry
    scrub: nav.Scrub
    n: int
    move_last: int
    props: object
    rt: object


def shown(scene, space) -> bool:
    """Whether the sliders belong over this 3D view: a Preview result of the scene, mode Preview, option on."""
    if space is None or space.type != "VIEW_3D":
        return False
    rt = runtime.get(scene)
    sw = getattr(scene, "slicewright", None)
    if rt is None or sw is None or sw.mode != "PREVIEW" or nav.layer_count(rt.layers) == 0:
        return False
    return bool(preview_props.of(scene).show_sliders)


def geometry(context) -> ps.Geometry | None:
    area, region = context.area, context.region
    if area is None or region is None or region.type != "WINDOW":
        return None
    others = [(r.x, r.y, r.width, r.height) for r in area.regions if r.type in INSET_REGIONS]
    insets = ps.insets_from_rects((region.x, region.y, region.width, region.height), others)
    return ps.make_geometry(region.width, region.height, context.preferences.system.ui_scale, insets)


def view_state(context) -> View | None:
    """What the draw handler and the mouse handler both need, or ``None`` when no slider belongs here."""
    scene = context.scene
    if not shown(scene, context.space_data):
        return None
    g = geometry(context)
    if g is None or not (g.show_layers or g.show_move):
        return None
    rt = runtime.get(scene)
    p = preview_props.of(scene)
    scrub = nav.clamp(p.scrub(), rt.layers)
    return View(g, scrub, nav.layer_count(rt.layers), nav.layer_len(rt.layers, scrub.hi) - 1, p, rt)


def write(view: View, scrub: nav.Scrub) -> bool:
    """Put ``scrub`` into the preview settings (clamped; nothing written when unchanged). True when it changed."""
    scrub = nav.clamp(scrub, view.rt.layers)
    if scrub == view.scrub:
        return False
    view.props.set_scrub(scrub)
    return True


# -- drawing ---------------------------------------------------------------------------------------------------------

def _rgba(name: str, alpha: float = 1.0) -> tuple:
    hexc, a = ps.COLORS[name]
    rgb = palette.rgb_linear(hexc) if LINEAR_COLORS else palette.rgb(hexc)
    return (*rgb, a * alpha)


class _Painter:
    def __init__(self) -> None:
        import gpu
        self.gpu = gpu
        self.shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    def fan(self, pts, color) -> None:
        from gpu_extras.batch import batch_for_shader
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        batch = batch_for_shader(self.shader, "TRI_FAN", {"pos": [(cx, cy)] + list(pts) + [pts[0]]})
        self.shader.uniform_float("color", color)
        batch.draw(self.shader)

    def pill(self, x0, y0, x1, y1, color, radius=None) -> None:
        r = (min(x1 - x0, y1 - y0) / 2) if radius is None else radius
        self.fan(ps.rounded_rect_points(x0, y0, x1, y1, r), color)

    def circle(self, cx, cy, r, color) -> None:
        self.fan(ps.circle_points(cx, cy, r, 28), color)


def _text(painter, s: str, x: float, y: float, size: float, color: tuple, align: str = "left") -> tuple[float, float]:
    import blf
    blf.size(0, size)
    w, h = blf.dimensions(0, s)
    if align == "right":
        x -= w
    elif align == "center":
        x -= w / 2
    blf.position(0, x, y, 0)
    blf.color(0, *color)
    blf.draw(0, s)
    return w, h


def _thumb(painter, cx, cy, r, scale, mode) -> None:
    ring = _rgba({"drag": "active", "hover": "hover"}.get(mode, "thumb_ring"))
    painter.circle(cx, cy, r + 1.0 * scale, _rgba("backing", 1.0))
    painter.circle(cx, cy, r, ring)
    painter.circle(cx, cy, r - 2.6 * scale, _rgba("thumb"))


def _chip_text(painter, lines, right_x, anchor_y, scale, grow_up: bool) -> None:
    """A dark chip holding ``lines`` (text, size, colour name), its right edge at ``right_x``; it grows upward from
    ``anchor_y`` (the top thumb's label) or downward (the bottom thumb's)."""
    import blf
    pad = 7.0 * scale
    gap = 4.0 * scale
    dims = []
    for text, size, _ in lines:
        blf.size(0, size * scale)
        dims.append(blf.dimensions(0, text))
    w = max(d[0] for d in dims) + 2 * pad
    h = sum(d[1] for d in dims) + gap * (len(lines) - 1) + 2 * pad
    y0 = anchor_y if grow_up else anchor_y - h
    painter.pill(right_x - w, y0, right_x, y0 + h, _rgba("chip"), 5.0 * scale)
    y = y0 + h - pad
    for (text, size, color), d in zip(lines, dims):
        y -= d[1]
        _text(painter, text, right_x - pad - d[0], y, size * scale, _rgba(color))
        y -= gap


def _mode(view: View, kinds) -> str:
    if ui.area == _area_ptr() and ui.drag is not None:
        return "drag" if ui.drag.kind in kinds else "idle"
    if ui.area == _area_ptr() and ui.hover in kinds:
        return "hover"
    return "idle"


def _area_ptr() -> int:
    try:
        return bpy.context.area.as_pointer()
    except Exception:  # noqa: BLE001
        return 0


def _draw_layers(painter, view: View) -> None:
    g, s, n = view.geometry, view.scrub, view.n
    sc, r = g.scale, g.thumb_r
    ylo, yhi = ps.layer_to_y(g, s.lo, n), ps.layer_to_y(g, s.hi, n)
    x = g.track_x
    painter.pill(x - 15 * sc, g.track_y0 - r - 12 * sc, x + 15 * sc, g.track_y1 + r + 12 * sc, _rgba("backing"))
    half = ps.TRACK_W * sc / 2
    painter.pill(x - half, g.track_y0, x + half, g.track_y1, _rgba("track"))
    painter.pill(x - half - 0.5 * sc, ylo, x + half + 0.5 * sc, yhi, _rgba("range"))
    z = view.rt.layers["z"]
    tx = x - r - 8 * sc
    for which, y, grow_up in ((ps.LO, ylo, False), (ps.HI, yhi, True)):
        i = s.lo if which == ps.LO else s.hi
        line1, line2 = ps.thumb_text(i, n, float(z[i]))
        anchor = y - 4 * sc if grow_up else y + 4 * sc
        _chip_text(painter, [(line1, 14.0, "text"), (line2, 11.0, "text_dim")], tx, anchor, sc, grow_up)
    for which, y in ((ps.LO, ylo), (ps.HI, yhi)):
        _thumb(painter, x, y, r, sc, _mode(view, (which, ps.BAR)))
    _text(painter, ps.total_text(n), x + r, g.track_y1 + r + 26 * sc, 11.0 * sc, _rgba("text_dim"), "right")


def _draw_move(painter, view: View) -> None:
    g, s = view.geometry, view.scrub
    sc, r = g.scale, g.thumb_r
    x0, x1, y = g.move_x0, g.move_x1, g.move_y
    mx = ps.move_to_x(g, s.pos, view.move_last)
    painter.pill(x0 - r - 12 * sc, y - 15 * sc, x1 + r + 12 * sc, y + 15 * sc, _rgba("backing"))
    half = ps.TRACK_W * sc / 2
    painter.pill(x0, y - half, x1, y + half, _rgba("track"))
    painter.pill(x0, y - half - 0.5 * sc, max(mx, x0 + 1), y + half + 0.5 * sc, _rgba("range"))
    _thumb(painter, mx, y, r, sc, _mode(view, (ps.MOVE_THUMB, ps.MOVE_TRACK)))
    label = ps.move_text(s.pos, view.move_last)
    import blf
    blf.size(0, 11.0 * sc)
    w, h = blf.dimensions(0, label)
    cx = min(max(mx, x0 + w / 2 + 8 * sc), x1 - w / 2 - 8 * sc)
    ly = y + 22 * sc
    painter.pill(cx - w / 2 - 6 * sc, ly - 5 * sc, cx + w / 2 + 6 * sc, ly + h + 5 * sc, _rgba("chip"), 5 * sc)
    _text(painter, label, cx, ly, 11.0 * sc, _rgba("text"), "center")


def draw() -> None:
    global _error_logged
    try:
        view = view_state(bpy.context)
        if view is None:
            return
        painter = _Painter()
        gpu = painter.gpu
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        try:
            if view.geometry.show_layers:
                _draw_layers(painter, view)
            if view.geometry.show_move:
                _draw_move(painter, view)
        finally:
            gpu.state.blend_set("NONE")
    except Exception:  # noqa: BLE001 - never raise out of a draw handler
        if not _error_logged:
            _error_logged = True
            log.exception("slider draw failed (reported once)")


def register() -> None:
    global _handle, _error_logged
    _error_logged = False
    ui.reset()
    _handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), "WINDOW", "POST_PIXEL")


def unregister() -> None:
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    ui.reset()
