# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading evaluated, world-space plate geometry from the depsgraph (03 section 4.1)."""
from __future__ import annotations

from typing import Iterator

import numpy as np

import bpy

from ..core.transform import to_world_mm
from . import plate_collection
from .units import scene_mm_per_bu


def plate_instances(depsgraph, scene) -> Iterator[tuple[bpy.types.Object, "bpy.types.DepsgraphObjectInstance"]]:
    """Yield ``(plate_owner, instance)`` for every evaluated MESH instance that belongs to the plate.

    ``plate_owner`` is the original object that sits in the plate collection: the object itself,
    or for collection/Geometry Nodes instances the instancer that spawned them.
    """
    members = {o.name_full for o in plate_collection.plate_objects(scene)}
    for inst in depsgraph.object_instances:
        ob = inst.object
        if ob.type != "MESH":
            continue
        owner = inst.parent.original if inst.is_instance and inst.parent is not None else ob.original
        if owner.name_full in members:
            yield owner, inst


def instance_world_vertices_mm(inst, mm_per_bu: float) -> np.ndarray:
    """Evaluated vertices of one instance in world mm, float32 (n, 3)."""
    ob = inst.object
    me = ob.to_mesh()
    try:
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
        return to_world_mm(co, inst.matrix_world, mm_per_bu)
    finally:
        ob.to_mesh_clear()


def plate_vertices_mm(context, owners: set[str] | None = None) -> dict[str, np.ndarray]:
    """World-mm vertices per plate owner (by ``name_full``), merging that owner's instances.

    ``owners`` restricts the result to those owner names.
    """
    scene = context.scene
    mm_bu = scene_mm_per_bu(scene)
    parts: dict[str, list[np.ndarray]] = {}
    for owner, inst in plate_instances(context.evaluated_depsgraph_get(), scene):
        if owners is not None and owner.name_full not in owners:
            continue
        v = instance_world_vertices_mm(inst, mm_bu)
        if len(v):
            parts.setdefault(owner.name_full, []).append(v)
    return {k: np.concatenate(v) for k, v in parts.items()}
