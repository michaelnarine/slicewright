# SPDX-License-Identifier: GPL-3.0-or-later
"""Text wrapping for sidebar labels (03 section 6.5a). No ``bpy``.

Blender labels never wrap: a long message is cut in the middle ("...d_exclude_area)."). The panels therefore break
long text into several labels themselves, by an approximate budget of characters per line. The budget comes from the
width of the region the panel draws in; where that is unknown (background mode, a test) it is :data:`DEFAULT_CHARS`.

The numbers come from a measurement on a real window (the screenshot tool): a sidebar 280 UI units wide (561 px at a
scale of 2) fits about 44 characters of the default font between its margins, and a label with an icon about 4 fewer.
The estimate is then lowered by 5 % because capitals and digits are wider than the average letter.
"""
from __future__ import annotations

import textwrap

DEFAULT_CHARS = 38          # used when the region width is not known
MIN_CHARS, MAX_CHARS = 16, 90
SIDE_UNITS = 54.0           # the panel margins, the scroll bar and the tab strip, in UI units
CHAR_UNITS = 5.1            # the average advance of one character of the UI font, in UI units
SAFETY = 0.95
ICON_CHARS = 4              # what an icon in front of a label costs


def char_budget(region_width: float | None, scale: float = 1.0, *, icon: bool = False, indent: int = 0,
                default: int = DEFAULT_CHARS) -> int:
    """How many characters fit on one label line.

    ``region_width`` is the region's width in pixels and ``scale`` its pixels per UI unit (the display's resolution
    scale times the user's UI scale); ``None`` or a non-positive value gives ``default``. ``icon`` reserves room for
    an icon, ``indent`` for a nested box. Always between :data:`MIN_CHARS` and :data:`MAX_CHARS`."""
    if not region_width or region_width <= 0 or not scale or scale <= 0:
        n = default
    else:
        n = int((region_width / scale - SIDE_UNITS) / CHAR_UNITS * SAFETY)
    n -= (ICON_CHARS if icon else 0) + indent
    return max(MIN_CHARS, min(MAX_CHARS, n))


def wrap(text: str, width: int = DEFAULT_CHARS) -> list[str]:
    """``text`` as lines of at most ``width`` characters: words are kept whole, a word longer than a line (a setting
    key) is broken, explicit newlines start a new line. The text is never shortened: the lines joined with spaces
    hold every word of it. Empty text gives no lines."""
    width = max(int(width), 4)
    lines: list[str] = []
    for paragraph in (text or "").splitlines():
        if not paragraph.strip():
            continue
        lines += textwrap.wrap(paragraph, width=width, break_long_words=True, break_on_hyphens=False)
    return lines
