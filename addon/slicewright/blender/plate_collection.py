# SPDX-License-Identifier: GPL-3.0-or-later
"""The "Print plate" collection and its objects (03 sections 1.2 and 2.1)."""
from __future__ import annotations

import bpy

PLATE_NAME = "Print plate"
GEOMETRY_TYPES = ("MESH", "EMPTY", "CURVE", "SURFACE", "META", "FONT", "CURVES")


def plate_collection(scene: bpy.types.Scene, create: bool = False) -> bpy.types.Collection | None:
    """The scene's plate collection. With ``create`` it is made (and linked under the scene) if missing."""
    props = scene.slicewright
    coll = props.plate_collection
    if coll is not None:
        return coll
    coll = bpy.data.collections.get(PLATE_NAME)
    if coll is None:
        if not create:
            return None
        coll = bpy.data.collections.new(PLATE_NAME)
    if coll not in scene.collection.children_recursive:
        scene.collection.children.link(coll)
    props.plate_collection = coll
    return coll


def plate_objects(scene: bpy.types.Scene) -> list[bpy.types.Object]:
    """Original objects in the plate collection (and its children) that can hold geometry."""
    coll = plate_collection(scene)
    if coll is None:
        return []
    return [o for o in coll.all_objects if o.type in GEOMETRY_TYPES]
