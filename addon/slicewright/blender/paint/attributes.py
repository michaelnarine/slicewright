# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-face INT paint attributes on the base mesh (03 section 5.1).

INT (32-bit), not INT8: bmesh cannot see INT8 face layers, while INT layers appear as
``bm.faces.layers.int``. Values: support and seam 0 none / 1 enforce / 2 block; filament 0
object default, 1..N slot.
"""
from __future__ import annotations

import bmesh
import numpy as np

import bpy

from ...core.paint import ATTRIBUTES


class PaintAttributeError(RuntimeError):
    """An attribute with the right name exists but is not an INT FACE attribute."""


def ensure_attribute(me: bpy.types.Mesh, name: str) -> bpy.types.Attribute:
    """The INT FACE attribute ``name`` on ``me``, created (zeroed) if missing."""
    attr = me.attributes.get(name)
    if attr is None:
        return me.attributes.new(name, "INT", "FACE")
    if attr.domain != "FACE" or attr.data_type != "INT":
        raise PaintAttributeError(
            f"{name!r} on {me.name!r} is {attr.data_type} on {attr.domain}, expected INT on FACE")
    return attr


def bm_layer(bm: bmesh.types.BMesh, name: str):
    """The bmesh INT face layer ``name`` (created if needed). Works in object and edit mode."""
    layer = bm.faces.layers.int.get(name)
    return layer if layer is not None else bm.faces.layers.int.new(name)


def assign_selected(bm: bmesh.types.BMesh, name: str, value: int) -> int:
    """Write ``value`` to the selected faces of ``bm``; returns how many. Leaves other faces alone."""
    layer = bm_layer(bm, name)
    n = 0
    for face in bm.faces:
        if face.select:
            face[layer] = int(value)
            n += 1
    return n


def read(me: bpy.types.Mesh, name: str) -> np.ndarray | None:
    """Per-face int32 values, or None when the attribute is missing or not INT FACE."""
    attr = me.attributes.get(name)
    if attr is None or attr.domain != "FACE" or attr.data_type != "INT":
        return None
    out = np.empty(len(me.polygons), np.int32)
    attr.data.foreach_get("value", out)
    return out


def write(me: bpy.types.Mesh, name: str, values: np.ndarray) -> None:
    """Write per-face int values with ``foreach_set`` (creating the attribute)."""
    attr = ensure_attribute(me, name)
    attr.data.foreach_set("value", np.asarray(values, np.int32))
    me.update()


def clear_all(me: bpy.types.Mesh) -> None:
    """Remove all three paint attributes from ``me``."""
    for name in ATTRIBUTES:
        attr = me.attributes.get(name)
        if attr is not None:
            me.attributes.remove(attr)


def painted_face_count(me: bpy.types.Mesh) -> int:
    """Number of faces with any non-zero paint value."""
    mask = np.zeros(len(me.polygons), bool)
    for name in ATTRIBUTES:
        v = read(me, name)
        if v is not None:
            mask |= v != 0
    return int(mask.sum())
