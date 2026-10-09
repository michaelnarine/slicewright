# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene preview settings to the renderer's :class:`DrawParams` (03 sections 7.3 to 7.5)."""
from __future__ import annotations

from ...core import preview_data as pd
from ...core import preview_nav as nav
from .props import marker_kinds
from .renderer import DrawParams

DEFAULT_LINES_THRESHOLD = 12_000_000     # visible segments above which tubes switch to lines (03 7.3)


def visible_segments(layers, scrub: nav.Scrub) -> int:
    """Upper bound of the moves in view: from layer ``lo`` to the scrub position."""
    s = nav.clamp(scrub, layers)
    if nav.layer_count(layers) == 0:
        return 0
    first = int(layers["first"][s.lo])
    return nav.top_position(s, layers) - first + 1


def draw_params(props, rt, viewport=(1.0, 1.0), lines_threshold: int = DEFAULT_LINES_THRESHOLD) -> DrawParams:
    """``rt`` is the scene's PreviewRuntime. Clamps the scrub against the layer table."""
    scrub = nav.clamp(props.scrub(), rt.layers)
    lines = props.quality == "LINES" or visible_segments(rt.layers, scrub) > lines_threshold
    nozzle = None
    if scrub.pos is not None:
        nozzle = tuple(float(c) for c in rt.moves["position"][nav.top_position(scrub, rt.layers)])
    return DrawParams(
        lo=scrub.lo, hi=scrub.hi, p=scrub.pos, role_mask=nav.role_mask(props.role_mask),
        grey_below=scrub.hi if props.grey_below else 0, view_mode=pd.shader_mode_for(props.view_type),
        show_travel=props.show_travel, markers=marker_kinds(props), lines_lod=lines,
        viewport=tuple(float(v) for v in viewport), nozzle=nozzle)
