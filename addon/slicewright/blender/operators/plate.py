# SPDX-License-Identifier: GPL-3.0-or-later
"""Plate operators: units, the plate collection, drop to bed, center (03 sections 1.2 and 1.4)."""
from __future__ import annotations

import bpy
from mathutils import Vector

from ...names import OP_PREFIX
from .. import bed_source, meshdata, plate_collection, units, volume

_PREFIX = OP_PREFIX.lower()


class SLICEWRIGHT_OT_use_mm_scene(bpy.types.Operator):
    bl_idname = f"{_PREFIX}.use_mm_scene"
    bl_label = "Use millimetre scene"
    bl_description = ("Set the scene to millimetres (unit scale 0.001, one Blender unit per mm) "
                      "and adjust the 3D views' grid and clip distances")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        units.use_millimetre_scene(context.scene, context.screen)
        self.report({"INFO"}, "Scene set to millimetres")
        return {"FINISHED"}


class SLICEWRIGHT_OT_plate_add(bpy.types.Operator):
    bl_idname = f"{_PREFIX}.plate_add"
    bl_label = "Add selected to plate"
    bl_description = "Link the selected objects into the Print plate collection"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        coll = plate_collection.plate_collection(context.scene, create=True)
        added = 0
        for ob in context.selected_objects:
            if ob.type in plate_collection.GEOMETRY_TYPES and ob.name not in coll.objects:
                coll.objects.link(ob)
                added += 1
        self.report({"INFO"}, f"{added} object(s) added to the plate")
        return {"FINISHED"} if added else {"CANCELLED"}


class SLICEWRIGHT_OT_plate_remove(bpy.types.Operator):
    bl_idname = f"{_PREFIX}.plate_remove"
    bl_label = "Remove selected from plate"
    bl_description = "Unlink the selected objects from the Print plate collection (they stay in the scene)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        coll = plate_collection.plate_collection(context.scene)
        removed = 0
        if coll is not None:
            for ob in context.selected_objects:
                if ob.name in coll.objects:
                    coll.objects.unlink(ob)
                    removed += 1
        return {"FINISHED"} if removed else {"CANCELLED"}


def _targets(context) -> set[str]:
    """Names of the plate objects to act on: the selected ones, or the whole plate if none is selected."""
    plate = {o.name_full for o in plate_collection.plate_objects(context.scene)}
    chosen = {o.name_full for o in context.selected_objects} & plate
    return chosen or plate


def _translate(ob: bpy.types.Object, delta_mm, mm_per_bu: float) -> None:
    m = ob.matrix_world.copy()
    m.translation += Vector(delta_mm) / mm_per_bu
    ob.matrix_world = m


class SLICEWRIGHT_OT_drop_to_bed(bpy.types.Operator):
    bl_idname = f"{_PREFIX}.drop_to_bed"
    bl_label = "Drop to bed"
    bl_description = "Move each selected plate object (or all) straight down so its lowest point is at Z = 0"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return plate_collection.plate_collection(context.scene) is not None

    def execute(self, context):
        mm_bu = units.scene_mm_per_bu(context.scene)
        names = _targets(context)
        verts = meshdata.plate_vertices_mm(context, names)
        moved = 0
        for ob in plate_collection.plate_objects(context.scene):
            v = verts.get(ob.name_full)
            if v is None:
                continue
            dz = -float(v[:, 2].min())
            if abs(dz) > 1e-6:
                _translate(ob, (0.0, 0.0, dz), mm_bu)
                moved += 1
        context.view_layer.update()
        volume.recompute_now(context)
        self.report({"INFO"}, f"Dropped {moved} object(s)")
        return {"FINISHED"}


class SLICEWRIGHT_OT_center(bpy.types.Operator):
    bl_idname = f"{_PREFIX}.center"
    bl_label = "Center on bed"
    bl_description = "Move the selected plate objects (or all) together so their footprint is centred on the bed"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return plate_collection.plate_collection(context.scene) is not None

    def execute(self, context):
        bed = bed_source.current_bed(context.scene)
        if bed is None:
            self.report({"ERROR"}, "printable_area is not usable")
            return {"CANCELLED"}
        mm_bu = units.scene_mm_per_bu(context.scene)
        names = _targets(context)
        verts = meshdata.plate_vertices_mm(context, names)
        if not verts:
            return {"CANCELLED"}
        lo = min(float(v[:, 0].min()) for v in verts.values()), min(float(v[:, 1].min()) for v in verts.values())
        hi = max(float(v[:, 0].max()) for v in verts.values()), max(float(v[:, 1].max()) for v in verts.values())
        cx, cy = bed.center
        delta = (cx - (lo[0] + hi[0]) / 2.0, cy - (lo[1] + hi[1]) / 2.0, 0.0)
        for ob in plate_collection.plate_objects(context.scene):
            if ob.name_full in verts:
                _translate(ob, delta, mm_bu)
        context.view_layer.update()
        volume.recompute_now(context)
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_use_mm_scene, SLICEWRIGHT_OT_plate_add, SLICEWRIGHT_OT_plate_remove,
           SLICEWRIGHT_OT_drop_to_bed, SLICEWRIGHT_OT_center)
