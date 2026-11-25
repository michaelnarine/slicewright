# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake datablocks (03 section 7.11): copying the arrays of ``core/bake.py`` into Blender curves and meshes, the bake
collection, the materials, and the tags that mark what the add-on made.

Everything here is one bounded step: a generator yields after each array it copies so the tick runner can give the UI a turn.
Attributes are written through ``attribute.data.foreach_set`` (a straight copy; ``Mesh.vertices.foreach_set("co")`` is
twenty times slower). Nothing is linked to a scene here: the job links the finished objects in one go.
"""
from __future__ import annotations

import numpy as np

import bpy

from ..core import bake, bake_plan as bp
from ..core.preview_palette import srgb_to_linear as palette_srgb_to_linear

ADD_CURVES_STEP = 100_000


srgb_to_linear = palette_srgb_to_linear


# -- tags and the collection -------------------------------------------------------------------------------------

def tag(idb, kind: str, source: str, scene_name: str) -> None:
    """Mark ``idb`` (object or data) as made by a bake: the only thing a re-bake may replace."""
    idb[bp.TAG] = kind
    idb[bp.TAG_SOURCE] = source
    idb[bp.TAG_SCENE] = scene_name


def is_ours(idb, scene_name: str | None = None) -> bool:
    try:
        if idb.get(bp.TAG) not in bp.KINDS:
            return False
        return scene_name is None or idb.get(bp.TAG_SCENE) == scene_name
    except ReferenceError:
        return False


def tagged_objects(scene_name: str, kind: str | None = None, sources=None) -> list:
    """The bake objects of ``scene_name`` (optionally of one ``kind`` and ``sources``), anywhere in the file."""
    out = []
    for ob in bpy.data.objects:
        if is_ours(ob, scene_name) and (kind is None or ob.get(bp.TAG) == kind) \
                and (sources is None or ob.get(bp.TAG_SOURCE) in sources):
            out.append(ob)
    return out


def bake_collection(scene) -> bpy.types.Collection:
    """The add-on's ``Slicewright Bake`` collection, linked under the scene's master collection (never under the
    plate). Only a collection the add-on tagged is reused; a user's collection of that name is left alone."""
    coll = next((c for c in bpy.data.collections if c.get(bp.TAG_MATERIAL) == "collection"), None)
    if coll is None:
        coll = bpy.data.collections.new(bp.COLLECTION_NAME)
        coll[bp.TAG_MATERIAL] = "collection"
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def remove_objects(objs) -> int:
    """Delete the bake objects ``objs`` and the curves or mesh data only they used (and only when the add-on tagged
    that data). Returns how many objects went."""
    n = 0
    for ob in list(objs):
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        n += 1
        if data is not None and is_ours(data) and data.users == 0:
            (bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.hair_curves).remove(data)
    return n


def discard(datablocks) -> None:
    """Throw away half-built data of a cancelled or failed bake."""
    for d in datablocks:
        try:
            if isinstance(d, bpy.types.Mesh):
                bpy.data.meshes.remove(d)
            elif isinstance(d, bpy.types.Curves):
                bpy.data.hair_curves.remove(d)
            elif isinstance(d, bpy.types.Material) and d.users == 0:
                bpy.data.materials.remove(d)
        except (ReferenceError, RuntimeError):
            pass


# -- materials ---------------------------------------------------------------------------------------------------

def material_for(key: int, role_names, created: list) -> bpy.types.Material:
    """The add-on's material for a feature type; made on first use (its name goes into ``created`` so a failed bake can
    take it away again). A re-bake finds it and so keeps whatever the user did to it."""
    name = bp.material_name(key, role_names)
    for m in bpy.data.materials:
        if m.get(bp.TAG_MATERIAL) == name:
            return m
    mat = bpy.data.materials.new(name)
    mat[bp.TAG_MATERIAL] = name
    r, g, b = (srgb_to_linear(c) for c in bp.feature_color(key, role_names))
    mat.diffuse_color = (r, g, b, 1.0)
    tree = mat.node_tree
    node = tree.nodes.get("Principled BSDF") if tree is not None else None
    if node is not None:
        node.inputs["Base Color"].default_value = (r, g, b, 1.0)
    created.append(mat)
    return mat


# -- curves ------------------------------------------------------------------------------------------------------

def _attr(owner, name: str, data_type: str, domain: str, values: np.ndarray) -> None:
    a = owner.attributes.new(name, data_type, domain)
    a.data.foreach_set("value", values)


def build_curves(name: str, out: bake.CurveArrays):
    """Generator: a ``Curves`` datablock from ``out``; yields after each array and returns the datablock."""
    curves = bpy.data.hair_curves.new(name)
    try:
        sizes = out.sizes
        for a in range(0, len(sizes), ADD_CURVES_STEP):
            curves.add_curves(sizes[a:a + ADD_CURVES_STEP].tolist())
            yield
        curves.attributes["position"].data.foreach_set("vector", out.position.reshape(-1))
        out.position = None
        yield
        _attr(curves, "radius", "FLOAT", "POINT", out.radius)
        out.radius = None
        yield
        for attr_name in bake.ATTRIBUTES:
            integer = attr_name in bake.INT_ATTRIBUTES
            _attr(curves, attr_name, "INT" if integer else "FLOAT", "POINT", out.attributes.pop(attr_name))
            yield
        _attr(curves, "material_index", "INT", "CURVE", out.material_index)
        curves.set_types(type='POLY')
        curves.update_tag()
    except BaseException:
        discard([curves])
        raise
    return curves


# -- meshes ------------------------------------------------------------------------------------------------------

def build_mesh(name: str, part: bake.PrismArrays):
    """Generator: a ``Mesh`` datablock from one part's arrays; yields between the copies (the face offsets and the final
    ``update`` are the costly ones, about 40 ns and 0.5 us a segment) and returns the mesh."""
    mesh = bpy.data.meshes.new(name)
    try:
        mesh.vertices.add(part.n_vertices)
        mesh.edges.add(len(part.edge_verts))
        mesh.loops.add(len(part.corner_vert))
        mesh.polygons.add(part.n_faces)
        mesh.attributes["position"].data.foreach_set("vector", part.co.reshape(-1))
        mesh.attributes[".edge_verts"].data.foreach_set("value", part.edge_verts.reshape(-1))
        mesh.attributes[".corner_vert"].data.foreach_set("value", part.corner_vert)
        mesh.attributes[".corner_edge"].data.foreach_set("value", part.corner_edge)
        yield
        mesh.polygons.foreach_set("loop_start", part.loop_start)
        yield
        faces = bake.face_attributes(part)
        for attr_name in bake.ATTRIBUTES:
            integer = attr_name in bake.INT_ATTRIBUTES
            _attr(mesh, attr_name, "INT" if integer else "FLOAT", "FACE", faces[attr_name])
        _attr(mesh, "material_index", "INT", "FACE", faces["material_index"])
        sharp = np.zeros(part.n_faces, bool)
        sharp[part.n * part.sides:] = True                      # the caps are flat; the sides read as a round bead
        if part.sides <= 4:
            sharp[:] = True
        _attr(mesh, "sharp_face", "BOOLEAN", "FACE", sharp)
        yield
        mesh.update()
        yield
    except BaseException:
        discard([mesh])
        raise
    return mesh
