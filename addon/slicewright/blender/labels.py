# SPDX-License-Identifier: GPL-3.0-or-later
"""Message labels that wrap (03 section 6.5a): Blender labels do not, so a long text is drawn as several labels.

``core.text`` does the arithmetic; this module reads the width of the region being drawn. Outside a draw (background
mode, a test) there is no region and the lines are ``core.text.DEFAULT_CHARS`` wide.
"""
from __future__ import annotations

import bpy

from ..core import text as core_text


def region_scale() -> float:
    """Pixels per UI unit: the display's resolution scale (``system.dpi`` / 72, 2 on a Retina Mac) times the UI scale."""
    try:
        return (bpy.context.preferences.system.dpi / 72.0) * bpy.context.preferences.view.ui_scale
    except AttributeError:
        return 1.0


def budget(*, icon: bool = False, indent: int = 0) -> int:
    """Characters per line for a label drawn now."""
    region = getattr(bpy.context, "region", None)
    width = getattr(region, "width", None) if region is not None else None
    return core_text.char_budget(width, region_scale(), icon=icon, indent=indent)


def lines_for(text: str, *, icon: bool = False, indent: int = 0) -> list[str]:
    return core_text.wrap(text, budget(icon=icon, indent=indent))


def draw_wrapped(layout, text: str, icon: str = "NONE", *, indent: int = 0) -> None:
    """``text`` as one label per line; the icon goes before the first line, later lines are aligned under its text.
    Empty text draws nothing. A text that fits is one plain ``label(text=, icon=)`` call."""
    has_icon = icon not in ("", "NONE")
    lines = lines_for(text, icon=has_icon, indent=indent)
    for n, line in enumerate(lines):
        if n == 0:
            layout.label(text=line, icon=icon) if has_icon else layout.label(text=line)
        else:
            layout.label(text=line, icon="BLANK1") if has_icon else layout.label(text=line)
