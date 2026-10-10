# SPDX-License-Identifier: GPL-3.0-or-later
"""Legend colour chips as ``bpy.utils.previews`` icons, filled through ``image_pixels_float`` (03 7.7)."""
from __future__ import annotations

from ...core import preview_legend as legend

_coll = None


def _collection():
    global _coll
    if _coll is None:
        import bpy.utils.previews
        _coll = bpy.utils.previews.new()
    return _coll


def _icon(key: str, size: tuple, pixels) -> int:
    coll = _collection()
    if key not in coll:
        img = coll.new(key)
        img.image_size = size
        img.image_pixels_float = pixels()
    return coll[key].icon_id


def chip(color: int) -> int:
    """Icon id of a 16x16 chip of ``0xRRGGBB``."""
    return _icon(f"chip_{color:06x}", (16, 16), lambda: legend.chip_pixels(color))


def gradient() -> int:
    """Icon id of the range ramp (64x16)."""
    return _icon("range_gradient", (64, 16), lambda: legend.gradient_pixels(64, 16))


def release() -> None:
    global _coll
    if _coll is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_coll)
        _coll = None
