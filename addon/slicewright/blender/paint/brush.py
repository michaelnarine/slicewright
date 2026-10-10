# SPDX-License-Identifier: GPL-3.0-or-later
"""Object-mode brush engine (03 section 5.2): raycast and dab, with no event or UI code.

A world-space ``BVHTree.FromPolygons`` of each plate object's **base** mesh serves the raycast and
a ``KDTree`` of face centres finds the faces within the brush radius; both are cached per object
and dropped when the depsgraph reports that object changed. Distances here are Blender units.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from ...core import brush as core_brush
from ...core import paint as core_paint
from .. import plate_collection
from . import attributes


@dataclass
class Target:
    """Cached raycast data for one mesh object (world space, BU)."""
    name: str
    bvh: BVHTree
    kd: KDTree
    matrix: np.ndarray
    normals: np.ndarray | None = None                # (n, 3) world, lazy
    adjacency: list | None = None                    # lazy
    mesh_name: str = ""


_cache: dict[str, Target] = {}
# Meshes a live stroke is writing attributes to: those writes raise geometry updates that must
# not throw the cached BVH away mid-stroke.
_suspended: set[str] = set()


def clear_cache() -> None:
    _cache.clear()
    _suspended.clear()


def _build(ob: bpy.types.Object) -> Target:
    me = ob.data
    m = np.array(ob.matrix_world, np.float64)
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    world = co.reshape(-1, 3).astype(np.float64) @ m[:3, :3].T + m[:3, 3]
    n_loops = len(me.loops)
    vi = np.empty(n_loops, np.int32)
    me.loops.foreach_get("vertex_index", vi)
    starts = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_start", starts)
    totals = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_total", totals)
    polys = [tuple(vi[s:s + t].tolist()) for s, t in zip(starts.tolist(), totals.tolist())]
    bvh = BVHTree.FromPolygons([tuple(v) for v in world.tolist()], polys, all_triangles=False)
    centers = np.empty(len(me.polygons) * 3, np.float32)
    me.polygons.foreach_get("center", centers)
    wc = centers.reshape(-1, 3).astype(np.float64) @ m[:3, :3].T + m[:3, 3]
    kd = KDTree(len(wc))
    for i, c in enumerate(wc.tolist()):
        kd.insert(Vector(c), i)
    kd.balance()
    return Target(ob.name_full, bvh, kd, m, mesh_name=me.name_full)


def target_for(ob: bpy.types.Object) -> Target:
    t = _cache.get(ob.name_full)
    if t is None:
        t = _cache[ob.name_full] = _build(ob)
    return t


def plate_meshes(scene) -> list[bpy.types.Object]:
    """Plate objects whose base mesh the brush can paint."""
    return [o for o in plate_collection.plate_objects(scene) if o.type == "MESH"]


def raycast(scene, origin, direction):
    """Nearest hit over the plate's base meshes: ``(object, face_index, location Vector, distance)`` or None."""
    best = None
    o, d = Vector(origin), Vector(direction).normalized()
    for ob in plate_meshes(scene):
        loc, _normal, index, dist = target_for(ob).bvh.ray_cast(o, d)
        if loc is not None and (best is None or dist < best[3]):
            best = (ob, index, loc, dist)
    return best


def _normals(ob: bpy.types.Object, t: Target) -> np.ndarray:
    if t.normals is None:
        n = np.empty(len(ob.data.polygons) * 3, np.float32)
        ob.data.polygons.foreach_get("normal", n)
        t.normals = core_paint.world_normals(n, t.matrix)
    return t.normals


def _adjacency(ob: bpy.types.Object, t: Target) -> list:
    if t.adjacency is None:
        me = ob.data
        edge = np.empty(len(me.loops), np.int32)
        me.loops.foreach_get("edge_index", edge)
        starts = np.empty(len(me.polygons), np.int32)
        me.polygons.foreach_get("loop_start", starts)
        totals = np.empty(len(me.polygons), np.int32)
        me.polygons.foreach_get("loop_total", totals)
        face_of_loop = np.repeat(np.arange(len(me.polygons)), totals)
        t.adjacency = core_brush.face_adjacency(edge, face_of_loop, len(me.polygons))
    return t.adjacency


def faces_in_brush(ob, face_index: int, location, radius_bu: float, smart: bool = False,
                   angle_deg: float = 20.0) -> list[int]:
    """Faces within ``radius_bu`` of ``location``; with ``smart`` only those reachable from the hit
    face across edges whose neighbouring normals differ by at most ``angle_deg``."""
    t = target_for(ob)
    found = [i for _co, i, _d in t.kd.find_range(Vector(location), radius_bu)]
    if face_index not in found:
        found.append(face_index)          # a hit face is always painted, even with a tiny radius
    if not smart:
        return sorted(found)
    return sorted(core_brush.grow_region(face_index, found, _normals(ob, t), _adjacency(ob, t), angle_deg))


@dataclass
class Stroke:
    """One brush stroke: the settings, plus snapshots so Esc can undo what the stroke wrote."""
    attr: str
    value: int
    radius_bu: float
    erase: bool = False
    smart: bool = False
    angle_deg: float = 20.0
    snapshots: dict = field(default_factory=dict)       # (mesh name, attr) -> original int32 array or None
    touched: set = field(default_factory=set)           # mesh names

    def dab(self, scene, origin, direction) -> int:
        """Paint where the ray hits; returns the number of faces written (0 on a miss)."""
        hit = raycast(scene, origin, direction)
        if hit is None:
            return 0
        ob, face, loc, _dist = hit
        return self.dab_faces(ob, faces_in_brush(ob, face, loc, self.radius_bu, self.smart, self.angle_deg))

    def dab_faces(self, ob, faces) -> int:
        me = ob.data
        key = (me.name_full, self.attr)
        if key not in self.snapshots:
            before = attributes.read(me, self.attr)
            self.snapshots[key] = None if before is None else before.copy()
        values = attributes.read(me, self.attr)
        if values is None:
            values = np.zeros(len(me.polygons), np.int32)
        values[np.asarray(faces, np.int64)] = 0 if self.erase else self.value
        attributes.write(me, self.attr, values)
        self.touched.add(me.name_full)
        _suspended.add(me.name_full)
        return len(faces)

    def end(self) -> None:
        """Finish the stroke: stop shielding the cache (the next geometry update invalidates normally)."""
        _suspended.difference_update(self.touched)

    def cancel(self) -> None:
        """Restore every attribute this stroke touched to its state before the first dab."""
        for (mesh_name, attr), before in self.snapshots.items():
            me = bpy.data.meshes.get(mesh_name)
            if me is None:
                continue
            if before is None:
                existing = me.attributes.get(attr)
                if existing is not None:
                    me.attributes.remove(existing)
            else:
                attributes.write(me, attr, before)
        self.end()
        self.snapshots.clear()
        self.touched.clear()


@persistent
def _on_depsgraph_update(scene, depsgraph=None) -> None:
    if depsgraph is None:
        clear_cache()
        return
    for update in depsgraph.updates:
        if update.is_updated_geometry or update.is_updated_transform:
            ob = update.id.original if hasattr(update.id, "original") else None
            if isinstance(ob, bpy.types.Object):
                if ob.type == "MESH" and ob.data.name_full in _suspended:
                    continue
                _cache.pop(ob.name_full, None)
            elif isinstance(ob, bpy.types.Mesh):
                if ob.name_full in _suspended:
                    continue
                for name in [k for k, t in _cache.items() if t.mesh_name == ob.name_full]:
                    _cache.pop(name, None)


@persistent
def _on_load_post(*_args) -> None:
    clear_cache()


def register() -> None:
    clear_cache()
    for handlers, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
                         (bpy.app.handlers.load_post, _on_load_post)):
        if fn not in handlers:
            handlers.append(fn)


def unregister() -> None:
    for handlers, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
                         (bpy.app.handlers.load_post, _on_load_post)):
        while fn in handlers:
            handlers.remove(fn)
    clear_cache()
