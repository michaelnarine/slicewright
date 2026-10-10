# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview settings (03 section 2.1 ``preview``): view, layer range, scrub position, visibility.

Registered as ``Scene.slicewright_preview`` (a separate pointer, so this stage does not touch
``blender/props.py``; moving it under ``Scene.slicewright.preview`` is a two-line change).
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, BoolVectorProperty, EnumProperty, FloatProperty, IntProperty

from ...core import preview_data as pd
from ...core import preview_nav as nav
from . import runtime

PROP_NAME = "slicewright_preview"

VIEW_LABELS = {
    "feature": "Feature type", "speed": "Speed", "actual_speed": "Actual speed", "height": "Layer height",
    "width": "Line width", "flow": "Volumetric flow", "fan": "Fan speed", "temperature": "Temperature",
    "layer_time": "Layer time", "layer_time_log": "Layer time (log)", "filament": "Filament",
    "nozzle": "Nozzle", "color": "Colour print", "pressure_advance": "Pressure advance",
    "acceleration": "Acceleration", "jerk": "Jerk"}
VIEW_ITEMS = [(v, VIEW_LABELS[v], "") for v in pd.VIEW_MODES]


def _redraw(self, context) -> None:
    runtime.tag_redraw()


def _view_changed(self, context) -> None:
    runtime.view_changed(context.scene)
    runtime.tag_redraw()


class SLICEWRIGHT_PG_preview(bpy.types.PropertyGroup):
    view_type: EnumProperty(name="View", items=VIEW_ITEMS, default="feature", update=_view_changed)
    layer_lo: IntProperty(name="Bottom layer", min=0, default=0, update=_redraw,
                          description="First visible layer (0-based)")
    layer_hi: IntProperty(name="Top layer", min=0, default=0, update=_redraw,
                          description="Last visible layer (0-based)")
    scrub_moves: BoolProperty(name="Scrub moves", default=False, update=_redraw,
                              description="Show only the first moves of the top layer")
    move_pos: IntProperty(name="Move", min=0, default=0, update=_redraw,
                          description="Last visible move inside the top layer")
    grey_below: BoolProperty(name="Grey lower layers", default=False, update=_redraw)
    show_travel: BoolProperty(name="Travel", default=False, update=_redraw)
    show_retracts: BoolProperty(name="Retractions", default=False, update=_redraw)
    show_seams: BoolProperty(name="Seams", default=False, update=_redraw)
    show_toolchanges: BoolProperty(name="Tool and colour changes", default=False, update=_redraw)
    quality: EnumProperty(name="Quality", default="TUBES", update=_redraw,
                          items=[("TUBES", "Tubes", "Shaded tubes"),
                                 ("LINES", "Lines", "Flat lines: fastest on big prints")])
    range_fixed: BoolProperty(name="Fixed range", default=False, update=_view_changed,
                              description="Use a fixed min/max instead of the 0.5-99.5 percentile")
    range_min: FloatProperty(name="Min", default=0.0, update=_view_changed)
    range_max: FloatProperty(name="Max", default=100.0, update=_view_changed)
    role_mask: BoolVectorProperty(name="Visible roles", size=32, default=(True,) * 32, update=_redraw)

    def scrub(self) -> nav.Scrub:
        return nav.Scrub(self.layer_lo, self.layer_hi, self.move_pos if self.scrub_moves else None)

    def set_scrub(self, s: nav.Scrub) -> None:
        self.layer_lo, self.layer_hi = s.lo, s.hi
        self.scrub_moves = s.pos is not None
        if s.pos is not None:
            self.move_pos = s.pos


MARKER_PROPS = (("show_retracts", ("Retract", "Unretract")), ("show_seams", ("Seam",)),
                ("show_toolchanges", ("Tool_change", "Color_change", "Pause_print", "Custom_gcode")))


def marker_kinds(props) -> tuple:
    return tuple(kind for attr, kinds in MARKER_PROPS if getattr(props, attr) for kind in kinds)


classes = (SLICEWRIGHT_PG_preview,)


def register() -> None:
    bpy.utils.register_class(SLICEWRIGHT_PG_preview)
    setattr(bpy.types.Scene, PROP_NAME, bpy.props.PointerProperty(type=SLICEWRIGHT_PG_preview))


def unregister() -> None:
    if hasattr(bpy.types.Scene, PROP_NAME):
        delattr(bpy.types.Scene, PROP_NAME)
    try:
        bpy.utils.unregister_class(SLICEWRIGHT_PG_preview)
    except (RuntimeError, ValueError):
        pass
