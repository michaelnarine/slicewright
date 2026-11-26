# SPDX-License-Identifier: GPL-3.0-or-later
"""Drawing one enum setting as segmented buttons or a dropdown, by the one rule in ``core/choice_layout.py``.

Only ``UILayout`` calls, so tests drive it with a recording layout.
"""
from __future__ import annotations

import bpy

from ...core import choice_layout as cl


def available_chars() -> float:
    """Characters of UI text that fit the region being drawn (the default sidebar width without a region)."""
    region = getattr(bpy.context, "region", None)
    try:
        scale = bpy.context.preferences.system.dpi / 72.0      # ui_scale times the display's pixel size
    except AttributeError:
        scale = 1.0
    return cl.width_chars(getattr(region, "width", None), scale)


def option_labels(data, key: str) -> list[str]:
    prop = data.bl_rna.properties.get(key)
    if prop is None or prop.type != "ENUM":
        return []
    return [item.name for item in prop.enum_items]


def style_of(data, key: str, width: float | None = None) -> str:
    """``"segmented"``, ``"dropdown"`` for an enum property, ``""`` for any other property."""
    prop = data.bl_rna.properties.get(key)
    if prop is None or prop.type != "ENUM" or prop.is_enum_flag:
        return ""
    return cl.choose(option_labels(data, key), available_chars() if width is None else width)


def draw_segmented(layout, data, key: str, label: str, header=None, lead=None) -> None:
    """The label above, the options as one row of buttons below it (the full width, not the 45 % field column).
    ``header(row)``, when given, adds controls (a remove or reset button) at the right end of the label line and
    ``lead(row)`` at the left end (a favourite star)."""
    col = layout.column(align=True)
    head = col.row(align=True)
    if lead is not None:
        lead(head)
    head.label(text=f"{label}:")
    if header is not None:
        header(head)
    col.row(align=True).prop(data, key, expand=True)      # a row: in a column the buttons stack


def draw_prop(layout, data, key: str, text: str | None = None) -> None:
    """``layout.prop`` that draws an enum as segmented buttons when the rule says so (other properties as usual)."""
    if style_of(data, key) == "segmented":
        draw_segmented(layout, data, key, text if text is not None else data.bl_rna.properties[key].name)
    elif text is None:
        layout.prop(data, key)
    else:
        layout.prop(data, key, text=text)
