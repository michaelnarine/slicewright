# SPDX-License-Identifier: GPL-3.0-or-later
"""How a choice setting is drawn: segmented buttons or a dropdown. No ``bpy``.

One rule for every enum the add-on draws (settings pages, per-object overrides): few, short options sit side by side as
an expanded enum (``layout.prop(data, key, expand=True)``); anything else is a dropdown. The width is a parameter so a
wider area (the Slicer workspace) can reuse the rule and get more segmented rows.
"""
from __future__ import annotations

from typing import Sequence

SEGMENTED, DROPDOWN = "segmented", "dropdown"

MAX_OPTIONS = 3             # more options than this: a dropdown
MAX_LABEL_CHARS = 14        # a longer label than this: a dropdown (few options but a lot of text)
BUTTON_PAD_CHARS = 3        # each segment costs its label plus about this much padding and gap
DEFAULT_WIDTH_CHARS = 34    # the default Slicer sidebar, in characters of UI text
SIDEBAR_MARGIN_UNITS = 50   # box and region margins taken off the region width before it is turned into characters
UNITS_PER_CHAR = 7.0        # average width of a UI text character, in UI units (pixels at ui_scale 1)


def choose(labels: Sequence[str], width_chars: float = DEFAULT_WIDTH_CHARS) -> str:
    """``"segmented"`` when ``labels`` are at most ``MAX_OPTIONS``, each at most ``MAX_LABEL_CHARS`` long, and the
    segments fit ``width_chars``; otherwise ``"dropdown"``. Fewer than two options is a dropdown (nothing to switch)."""
    n = len(labels)
    if n < 2 or n > MAX_OPTIONS:
        return DROPDOWN
    if max(len(label) for label in labels) > MAX_LABEL_CHARS:
        return DROPDOWN
    needed = sum(len(label) + BUTTON_PAD_CHARS for label in labels)
    return SEGMENTED if needed <= width_chars else DROPDOWN


def width_chars(region_px: float | None, ui_scale: float = 1.0) -> float:
    """Characters that fit a region ``region_px`` pixels wide (``None`` or unusable: the default sidebar width)."""
    if not region_px or region_px <= 0:
        return DEFAULT_WIDTH_CHARS
    units = region_px / max(ui_scale, 0.1) - SIDEBAR_MARGIN_UNITS
    return max(units / UNITS_PER_CHAR, 0.0)
