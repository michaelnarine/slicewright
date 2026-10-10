# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: INT face paint attributes, bmesh assignment and paint arrays (plan M4 layer 4)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bmesh  # noqa: E402
import numpy as np  # noqa: E402

import bpy  # noqa: E402

SUPPORT, SEAM, FILAMENT = "slicewright_support", "slicewright_seam", "slicewright_filament"


class PaintTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        self.addon = slicewright
        bl_common.fresh_scene()
        self.addon.register()
        self.addCleanup(self.addon.unregister)
        self.scene = bpy.context.scene
        from slicewright.blender.paint import attributes
        self.attrs = attributes

    def plated_cube(self, name="C", size=10.0, location=(50, 50, 5)):
        ob = bl_common.add_cube(name, size, location)
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.slicewright.plate_add()
        return ob

    def extract(self):
        from slicewright.blender import extract
        return extract.extract_plate(bpy.context)

    # -- storage ----------------------------------------------------------------------------

    def test_attributes_are_int_32_bit_on_the_face_domain(self):
        ob = bl_common.add_cube("C")
        for name in (SUPPORT, SEAM, FILAMENT):
            attr = self.attrs.ensure_attribute(ob.data, name)
            self.assertEqual((attr.data_type, attr.domain), ("INT", "FACE"))
            self.assertEqual(len(attr.data), 6)
        self.assertEqual(self.attrs.ensure_attribute(ob.data, SUPPORT).name, SUPPORT)
        self.assertEqual([a.name for a in ob.data.attributes].count(SUPPORT), 1)   # ensuring again adds nothing

    def test_a_wrongly_typed_attribute_is_refused_not_overwritten(self):
        ob = bl_common.add_cube("C")
        ob.data.attributes.new(SUPPORT, "FLOAT", "POINT")
        with self.assertRaises(self.attrs.PaintAttributeError):
            self.attrs.ensure_attribute(ob.data, SUPPORT)
        self.assertIsNone(self.attrs.read(ob.data, SUPPORT))

    def test_int8_would_be_invisible_to_bmesh_but_int_is_visible(self):
        ob = bl_common.add_cube("C")
        ob.data.attributes.new("i8", "INT8", "FACE")
        self.attrs.ensure_attribute(ob.data, SUPPORT)
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        self.assertIsNotNone(bm.faces.layers.int.get(SUPPORT))
        self.assertIsNone(bm.faces.layers.int.get("i8"))
        bm.free()

    def test_writable_through_bmesh_and_foreach_set(self):
        ob = bl_common.add_cube("C")
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        layer = self.attrs.bm_layer(bm, SUPPORT)
        bm.faces.ensure_lookup_table()
        bm.faces[1][layer] = 2
        bm.faces[4][layer] = 1
        bm.to_mesh(ob.data)
        bm.free()
        self.assertEqual(self.attrs.read(ob.data, SUPPORT).tolist(), [0, 2, 0, 0, 1, 0])
        self.assertEqual(ob.data.attributes[SUPPORT].data_type, "INT")
        self.attrs.write(ob.data, SEAM, np.arange(6))
        self.assertEqual(self.attrs.read(ob.data, SEAM).tolist(), [0, 1, 2, 3, 4, 5])

    def test_survives_subdivision_modifier_and_applying_it(self):
        ob = self.plated_cube()
        self.attrs.write(ob.data, SUPPORT, [0, 1, 0, 0, 0, 2])
        mod = ob.modifiers.new("s", "SUBSURF")
        mod.levels = 2
        mod.subdivision_type = "SIMPLE"           # keeps the 6 sides' boundaries exact
        res = self.extract()
        (obj,) = res.objects
        self.assertEqual(res.issues, [])
        self.assertEqual(len(obj.triangles), 6 * 16 * 2)         # 6 faces -> 16 quads each -> 2 tris
        counts = np.bincount(obj.face_support, minlength=3)
        self.assertEqual(counts.tolist(), [4 * 32, 32, 32])       # 4 unpainted sides, 1 enforce, 1 block
        # the evaluated mesh keeps the attribute as INT FACE, so bmesh can edit it after applying
        bpy.ops.object.modifier_apply(modifier="s")
        self.assertEqual(ob.data.attributes[SUPPORT].data_type, "INT")
        self.assertEqual(len(self.attrs.read(ob.data, SUPPORT)), 6 * 16)
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        self.assertIsNotNone(bm.faces.layers.int.get(SUPPORT))
        bm.free()

    def test_a_modifier_that_drops_the_attribute_warns(self):
        ob = self.plated_cube()
        self.attrs.write(ob.data, SUPPORT, [1, 1, 1, 1, 1, 1])
        mod = ob.modifiers.new("r", "REMESH")
        mod.mode = "VOXEL"
        mod.voxel_size = 1.0
        res = self.extract()
        self.assertEqual(len(res.objects), 1)
        self.assertIsNone(res.objects[0].face_support)
        self.assertEqual([i["code"] for i in res.issues], ["paint_dropped"])
        self.assertEqual(res.issues[0]["level"], "warning")

    def test_unpainted_objects_have_no_paint_arrays(self):
        self.plated_cube()
        (obj,) = self.extract().objects
        self.assertEqual((obj.face_support, obj.face_seam, obj.face_extruder), (None, None, None))

    # -- paint arrays ---------------------------------------------------------------------

    def test_paint_arrays_match_the_expected_triangles(self):
        ob = self.plated_cube()
        support = [0, 1, 2, 0, 0, 0]
        seam = [0, 0, 0, 1, 2, 0]
        filament = [0, 0, 0, 0, 0, 7]
        self.attrs.write(ob.data, SUPPORT, support)
        self.attrs.write(ob.data, SEAM, seam)
        self.attrs.write(ob.data, FILAMENT, filament)
        bpy.context.view_layer.update()
        (obj,) = self.extract().objects
        me = ob.data
        me.calc_loop_triangles()
        poly = np.array([t.polygon_index for t in me.loop_triangles])
        self.assertEqual(len(obj.triangles), 12)
        for got, face_values, label in ((obj.face_support, support, "support"),
                                        (obj.face_seam, seam, "seam"),
                                        (obj.face_extruder, filament, "filament")):
            self.assertEqual(got.dtype, np.uint8, label)
            self.assertEqual(got.tolist(), [face_values[p] for p in poly], label)
        # the arrays are per triangle, two per quad, and the fake engine accepts them
        import fake_engine as sc
        printer = {"name": "P", "printable_area": ["0x0", "256x0", "256x256", "0x256"],
                   "printable_height": "250", "nozzle_diameter": ["0.4"]}
        process = {"name": "Q", "layer_height": "0.2", "initial_layer_print_height": "0.2"}
        filament_p = {"name": "F", "filament_type": ["PLA"], "filament_colour": ["#FFFFFF"],
                      "nozzle_temperature": ["220"]}
        flat = sc.normalize_config(sc.compose_config(printer, process, [filament_p] * 8))["config"]
        job = sc.SliceJob()
        job.set_config(flat)
        from slicewright.blender import extract
        extract.add_to_job(job, [obj])
        self.assertEqual(job._objects[0].face_support.tolist(), obj.face_support.tolist())

    def test_values_above_255_are_clipped_not_wrapped(self):
        ob = self.plated_cube()
        self.attrs.write(ob.data, FILAMENT, [0, 0, 0, 0, 0, 300])
        (obj,) = self.extract().objects
        self.assertEqual(int(obj.face_extruder.max()), 255)       # the engine then reports paint_out_of_range

    def test_paint_changes_the_input_key(self):
        ob = self.plated_cube()
        k1 = self.extract().objects[0].key
        self.attrs.write(ob.data, SUPPORT, [1, 0, 0, 0, 0, 0])
        self.assertNotEqual(k1, self.extract().objects[0].key)

    # -- edit-mode operators ------------------------------------------------------------------

    def _edit(self, ob):
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.tool_settings.mesh_select_mode = (False, False, True)

    def _select_faces(self, ob, indices):
        bm = bmesh.from_edit_mesh(ob.data)
        bm.faces.ensure_lookup_table()
        for f in bm.faces:
            f.select_set(f.index in indices)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data)

    def test_assign_in_edit_mode_writes_the_selected_faces(self):
        ob = self.plated_cube()
        self._edit(ob)
        self._select_faces(ob, {0, 3})
        self.assertEqual(bpy.ops.slicewright.paint_assign(kind="SUPPORT", value=1), {"FINISHED"})
        self._select_faces(ob, {3})
        self.assertEqual(bpy.ops.slicewright.paint_assign(kind="SUPPORT", value=2), {"FINISHED"})
        self._select_faces(ob, {5})
        self.assertEqual(bpy.ops.slicewright.paint_assign(kind="FILAMENT", value=3), {"FINISHED"})
        self._select_faces(ob, {5})
        self.assertEqual(bpy.ops.slicewright.paint_assign(kind="SEAM", value=2), {"FINISHED"})
        bpy.ops.object.mode_set(mode="OBJECT")
        self.assertEqual(self.attrs.read(ob.data, SUPPORT).tolist(), [1, 0, 0, 2, 0, 0])
        self.assertEqual(self.attrs.read(ob.data, FILAMENT).tolist(), [0, 0, 0, 0, 0, 3])
        self.assertEqual(self.attrs.read(ob.data, SEAM).tolist(), [0, 0, 0, 0, 0, 2])
        self.assertEqual(self.attrs.painted_face_count(ob.data), 3)

    def test_assign_with_nothing_selected_cancels_and_rejects_bad_values(self):
        ob = self.plated_cube()
        self._edit(ob)
        self._select_faces(ob, set())
        self.assertEqual(bpy.ops.slicewright.paint_assign(kind="SUPPORT", value=1), {"CANCELLED"})
        self._select_faces(ob, {0})
        with self.assertRaises(RuntimeError):
            bpy.ops.slicewright.paint_assign(kind="SEAM", value=3)

    def test_assign_needs_edit_mode_and_is_undoable(self):
        self.plated_cube()
        with self.assertRaises(RuntimeError):                      # poll fails outside Edit Mode
            bpy.ops.slicewright.paint_assign(kind="SUPPORT", value=1)
        self.assertIn("UNDO", bpy.types.SLICEWRIGHT_OT_paint_assign.bl_options)

    def test_select_overhangs_picks_downward_faces_in_world_space(self):
        ob = self.plated_cube()
        ob.rotation_euler = (np.radians(180), 0, 0)                # upside down: the old top now faces down
        bpy.context.view_layer.update()
        self._edit(ob)
        self.assertEqual(bpy.ops.slicewright.select_overhangs(angle=np.radians(30)), {"FINISHED"})
        bm = bmesh.from_edit_mesh(ob.data)
        selected = [f.index for f in bm.faces if f.select]
        self.assertEqual(len(selected), 1)
        top = max(bm.faces, key=lambda f: f.calc_center_median().z)
        self.assertEqual(selected, [top.index])                    # object-space top, world-space bottom
        bpy.ops.object.mode_set(mode="OBJECT")

    def test_clear_all_removes_the_attributes(self):
        ob = self.plated_cube()
        for n in (SUPPORT, SEAM, FILAMENT):
            self.attrs.ensure_attribute(ob.data, n)
        self.assertEqual(bpy.ops.slicewright.paint_clear_all(), {"FINISHED"})
        self.assertEqual([n for n in (SUPPORT, SEAM, FILAMENT) if n in ob.data.attributes], [])


if __name__ == "__main__":
    bl_common.run("__main__")
