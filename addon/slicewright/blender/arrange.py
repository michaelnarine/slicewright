# SPDX-License-Identifier: GPL-3.0-or-later
"""Arrange (03 section 4.6): moves Blender objects, with undo, from the engine's placements.

A transient ``SliceJob`` holds one convex-hull prism per plate object; ``job.arrange`` returns
placements and each becomes ``M_world_new = S^-1 . T . S . M_world_old`` on the object. Slicing
never arranges. With the fake engine the placements are simple row packing; the real engine
(plan M8) also treats the exclude areas and the prime tower as obstacles.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import bpy
from mathutils import Matrix

from ..core.arrange import hull_prism, placement_matrices
from ..engine import adapter
from . import bed_source, meshdata, plate_collection, registry, volume
from .units import scene_mm_per_bu


@dataclass
class ArrangeOutcome:
    moved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)       # plate objects with no usable footprint
    error: adapter.ErrorReport | None = None


def arrange_config(sc, scene) -> dict:
    """A normalised config for the transient job.

    Placeholder until the printer presets (plan M3): a minimal printer built from the scene's bed
    keys, a default process and one filament, composed through the engine so keys stay valid.
    """
    bed = bed_source.current_bed(scene)
    props = scene.slicewright
    printer = {"name": "Slicewright arrange", "printable_area": [p.strip() for p in props.printable_area.split(",")],
               "printable_height": f"{bed.height:g}", "nozzle_diameter": ["0.4"]}
    if props.bed_exclude_area.strip():
        printer["bed_exclude_area"] = [p.strip() for p in props.bed_exclude_area.split(",")]
    process = {"name": "Slicewright arrange", "layer_height": "0.2", "initial_layer_print_height": "0.2"}
    filament = {"name": "Slicewright arrange", "filament_type": ["PLA"], "filament_colour": ["#FFFFFF"],
                "nozzle_temperature": ["220"]}
    return sc.normalize_config(sc.compose_config(printer, process, [filament]))["config"]


def run_arrange(context, spacing_mm: float | None = None, allow_rotation: bool = False) -> ArrangeOutcome:
    """Arrange the plate. Engine errors are returned as an ``ErrorReport`` in the outcome, not raised."""
    scene = context.scene
    sc = registry.state.status.module
    outcome = ArrangeOutcome()
    mm_bu = scene_mm_per_bu(scene)
    owners = {o.name_full: o for o in plate_collection.plate_objects(scene)}
    verts = meshdata.plate_vertices_mm(context)
    job = sc.SliceJob()
    index_of: dict[int, bpy.types.Object] = {}
    try:
        job.set_config(arrange_config(sc, scene))
        for name, ob in owners.items():
            prism = hull_prism(verts[name]) if name in verts else None
            if prism is None:
                outcome.skipped.append(ob.name)
                continue
            handle = job.add_object(name, prism[0], prism[1], ensure_on_bed=False)
            index_of[handle] = ob
        if not index_of:
            return outcome
        placements = job.arrange(spacing_mm, allow_rotation)
    except Exception as exc:  # noqa: BLE001 - every engine error is mapped (04 section 7)
        outcome.error = adapter.map_exception(sc, exc)
        return outcome
    old = {i: np.array(ob.matrix_world, np.float64) for i, ob in index_of.items()}
    for index, matrix in placement_matrices(placements, old, mm_bu).items():
        ob = index_of[index]
        ob.matrix_world = Matrix(matrix.tolist())
        outcome.moved.append(ob.name)
    context.view_layer.update()
    volume.recompute_now(context)
    return outcome
