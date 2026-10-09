# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh extraction from the evaluated depsgraph (03 section 4.1) and the pre-slice checks (section 4.3).

Everything runs on the main thread. Per plate instance: the evaluated mesh's loop triangles and
vertices are read with numpy ``foreach_get``, transformed in float64 to world millimetres and
stored as float32; a negative-determinant matrix flips the winding; per-face paint attributes
are mapped to per-triangle uint8 arrays through ``polygon_index``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import bpy

from ..core import checks, hashing, meshcheck
from ..core.transform import oriented_triangles, to_world_mm
from . import bed_source, plate_collection
from .meshdata import plate_instances
from .units import scene_mm_per_bu

ATTR_SUPPORT = "slicewright_support"
ATTR_SEAM = "slicewright_seam"
ATTR_FILAMENT = "slicewright_filament"


@dataclass
class ExtractedObject:
    name: str                              # the engine-side name ("Bolt#3" for instances)
    owner: str                             # name_full of the plate object it came from
    vertices: np.ndarray                   # (n, 3) float32, world mm
    triangles: np.ndarray                  # (m, 3) int32
    extruder: int = 0
    config_overrides: dict = field(default_factory=dict)
    face_extruder: np.ndarray | None = None    # (m,) uint8 or None
    face_support: np.ndarray | None = None
    face_seam: np.ndarray | None = None
    key: str = ""

    def add_object_kwargs(self) -> dict:
        """Keyword arguments for ``SliceJob.add_object`` after the name, vertices and triangles (04 section 4.1)."""
        return dict(extruder=self.extruder, config_overrides=self.config_overrides or None,
                    face_extruder=self.face_extruder, face_support=self.face_support,
                    face_seam=self.face_seam, ensure_on_bed=False)


@dataclass
class Extraction:
    objects: list[ExtractedObject] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Hash of the objects only; callers fold in the engine version and config with ``hashing.scene_key``."""
        return hashing.scene_key("", {}, [o.key for o in self.objects])


def issue(level: str, code: str, message: str, object_name: str | None = None, opt_key: str | None = None) -> dict:
    """An ``Issue`` dict (04 section 2.6)."""
    return {"level": level, "code": code, "message": message, "opt_key": opt_key, "object_name": object_name}


def face_attribute(me: bpy.types.Mesh, name: str) -> np.ndarray | None:
    """The per-face INT attribute ``name`` as int32, or None if missing or not an INT FACE attribute."""
    attr = me.attributes.get(name)
    if attr is None or attr.domain != "FACE" or attr.data_type != "INT":
        return None
    out = np.empty(len(me.polygons), np.int32)
    attr.data.foreach_get("value", out)
    return out


def _per_triangle(values: np.ndarray | None, poly: np.ndarray) -> np.ndarray | None:
    """Per-face int32 -> per-triangle uint8 (clipped, never wrapped); None stays None."""
    if values is None:
        return None
    return np.clip(values, 0, 255).astype(np.uint8)[poly]


def _paint_lost(owner: bpy.types.Object, evaluated_mesh: bpy.types.Mesh) -> list[str]:
    """Names of painted base-mesh attributes (any non-zero value) that the evaluated mesh no longer has."""
    base = owner.data if owner.type == "MESH" else None
    if base is None:
        return []
    lost = []
    for name in (ATTR_SUPPORT, ATTR_SEAM, ATTR_FILAMENT):
        painted = face_attribute(base, name)
        if painted is not None and painted.any() and face_attribute(evaluated_mesh, name) is None:
            lost.append(name)
    return lost


def extract_instance(inst, owner: bpy.types.Object, name: str, mm_per_bu: float,
                     issues: list[dict]) -> ExtractedObject | None:
    ob = inst.object
    me = ob.to_mesh()
    try:
        n = len(me.loop_triangles)
        if n == 0 or len(me.vertices) == 0:
            issues.append(issue("warning", "degenerate_object", f"{name} has no triangles and is skipped", name))
            return None
        tri = np.empty(n * 3, np.int32)
        me.loop_triangles.foreach_get("vertices", tri)
        poly = np.empty(n, np.int32)
        me.loop_triangles.foreach_get("polygon_index", poly)
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
        m = np.asarray(inst.matrix_world, np.float64)
        v = to_world_mm(co, m, mm_per_bu)
        t = oriented_triangles(tri.reshape(-1, 3), m)
        support = _per_triangle(face_attribute(me, ATTR_SUPPORT), poly)
        seam = _per_triangle(face_attribute(me, ATTR_SEAM), poly)
        filament = _per_triangle(face_attribute(me, ATTR_FILAMENT), poly)
        for attr in _paint_lost(owner, me):
            issues.append(issue("warning", "paint_dropped",
                                f"{name}: the modifiers drop the painted attribute {attr}; paint is ignored",
                                name))
    finally:
        ob.to_mesh_clear()
    if meshcheck.is_degenerate(v, t):
        issues.append(issue("warning", "degenerate_object", f"{name} has zero volume and is skipped", name))
        return None
    if len(t) > meshcheck.LARGE_MESH_TRIANGLES:
        issues.append(issue("info", "large_mesh",
                            f"{name} has {len(t):,} triangles; consider Decimate", name))
    props = getattr(owner, "slicewright", None)
    extruder = int(props.filament) if props is not None else 0
    obj = ExtractedObject(name, owner.name_full, v, t, extruder, {}, filament, support, seam)
    obj.key = hashing.object_key(v, t, filament, support, seam, obj.config_overrides, extruder)
    return obj


def extract_plate(context, check_manifold: bool = False) -> Extraction:
    """Extract every plate instance. ``check_manifold`` adds the open-edge check (Slice or Check plate only)."""
    scene = context.scene
    mm_bu = scene_mm_per_bu(scene)
    result = Extraction()
    used: dict[str, int] = {}
    for owner, inst in plate_instances(context.evaluated_depsgraph_get(), scene):
        base = inst.object.original.name_full
        if inst.is_instance:
            used[base] = used.get(base, 0) + 1
            name = f"{inst.object.original.name}#{used[base]}"
        else:
            name = inst.object.original.name
        obj = extract_instance(inst, owner, name, mm_bu, result.issues)
        if obj is None:
            continue
        result.objects.append(obj)
        if check_manifold:
            open_edges = len(meshcheck.non_manifold_edges(obj.triangles))
            if open_edges:
                result.issues.append(issue("warning", "mesh_open_edges",
                                           f"{obj.name} has {open_edges} open or non-manifold edge(s)", obj.name))
    return result


_VOLUME_ISSUES = {
    checks.OUTSIDE_BED: ("error", "object_outside_bed", "is outside the printable area"),
    checks.IN_EXCLUDE: ("error", "object_outside_bed", "overlaps a bed exclude area"),
    checks.TOO_TALL: ("error", "object_too_tall", "is taller than the printable height"),
    checks.BELOW_BED: ("warning", "object_below_bed", "goes below the bed"),
}


def check_plate(context) -> Extraction:
    """The full pre-slice check: extraction, degenerate and manifold checks and the volume check."""
    result = extract_plate(context, check_manifold=True)
    bed = bed_source.current_bed(context.scene)
    if not plate_collection.plate_objects(context.scene):
        result.issues.append(issue("error", "empty_plate", "The Print plate has no objects"))
    if bed is None:
        result.issues.append(issue("error", "validation", "printable_area is not usable", opt_key="printable_area"))
        return result
    for obj in result.objects:
        for reason in checks.out_of_volume(obj.vertices, bed):
            level, code, text = _VOLUME_ISSUES[reason]
            result.issues.append(issue(level, code, f"{obj.name} {text}", obj.name))
    return result


def add_to_job(job, objects: list[ExtractedObject]) -> list[int]:
    """``job.add_object`` for each extracted object; returns the engine handles."""
    return [job.add_object(o.name, o.vertices, o.triangles, **o.add_object_kwargs()) for o in objects]
