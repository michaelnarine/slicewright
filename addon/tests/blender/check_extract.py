# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: mesh extraction and pre-slice checks (plan M4 layer 3).

Transforms, negative scale, modifiers, instances and unit scale, each checked against numbers
computed independently in numpy, plus the fake engine accepting the extracted arrays.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import numpy as np  # noqa: E402

import bpy  # noqa: E402
from mathutils import Matrix  # noqa: E402


def cube_corners(size):
    h = size / 2.0
    return np.array([[x, y, z] for x in (-h, h) for y in (-h, h) for z in (-h, h)], np.float64)


def rows(a):
    """Sort rows (rounded) so vertex sets compare regardless of order."""
    a = np.round(np.asarray(a, np.float64), 3)
    return a[np.lexsort(a.T[::-1])]


class ExtractTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        self.addon = slicewright
        self.scene = bl_common.fresh_scene(mm_scene=False)      # 1 BU = 1 m: unit scale matters
        self.addon.register()
        self.addCleanup(self.addon.unregister)
        self.scene = bpy.context.scene

    def plate(self, *objs):
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.ops.slicewright.plate_add()

    def extract(self, **kw):
        from slicewright.blender import extract
        return extract.extract_plate(bpy.context, **kw)

    def volume(self, obj):
        from slicewright.core.meshcheck import signed_volume
        return signed_volume(obj.vertices, obj.triangles)

    # -- transforms and units ------------------------------------------------------------

    def test_world_transform_matches_independent_numpy(self):
        self.scene.unit_settings.scale_length = 0.001            # mm scene
        o = bl_common.add_cube("A", 2.0, (10, 20, 3))
        o.rotation_euler = (0.0, 0.0, math.radians(90))
        o.scale = (2.0, 1.0, 1.0)
        self.plate(o)
        bpy.context.view_layer.update()
        (got,) = self.extract().objects
        rz = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float)
        expect = (cube_corners(2.0) * [2, 1, 1]) @ rz.T + [10, 20, 3]
        self.assertEqual(got.name, "A")
        self.assertEqual(got.vertices.dtype, np.float32)
        self.assertEqual(got.triangles.dtype, np.int32)
        np.testing.assert_allclose(rows(got.vertices), rows(expect), atol=1e-3)
        self.assertEqual(len(got.triangles), 12)
        self.assertAlmostEqual(self.volume(got), 2 * 2 * 2 * 2, places=2)     # 4 x 2 x 2 -> 16

    def test_unit_scale_multiplies_into_millimetres(self):
        for scale_length, mm_per_bu in ((1.0, 1000.0), (0.01, 10.0), (0.001, 1.0)):
            with self.subTest(scale_length=scale_length):
                for ob in list(bpy.data.objects):
                    bpy.data.objects.remove(ob)
                self.scene.unit_settings.scale_length = scale_length
                o = bl_common.add_cube("U", 1.0, (0.5, 0.25, 0.125))
                self.plate(o)
                (got,) = self.extract().objects
                np.testing.assert_allclose(rows(got.vertices), rows(cube_corners(1.0) * mm_per_bu
                                                                    + np.array([0.5, 0.25, 0.125]) * mm_per_bu),
                                           atol=1e-3)

    def test_parent_transform_is_included(self):
        self.scene.unit_settings.scale_length = 0.001
        parent = bpy.data.objects.new("P", None)
        parent.location = (100, 0, 0)
        parent.scale = (3, 3, 3)
        self.scene.collection.objects.link(parent)
        child = bl_common.add_cube("C", 2.0, (1, 0, 0))
        child.parent = parent
        self.plate(child)
        bpy.context.view_layer.update()
        (got,) = self.extract().objects
        np.testing.assert_allclose(rows(got.vertices), rows(cube_corners(2.0) * 3 + [103, 0, 0]), atol=1e-3)

    def test_negative_scale_keeps_outward_winding(self):
        self.scene.unit_settings.scale_length = 0.001
        for scale in ((-1, 1, 1), (1, -2, 1), (1, 1, -1), (-1, -1, 1), (-1, -1, -1)):
            with self.subTest(scale=scale):
                for ob in list(bpy.data.objects):
                    bpy.data.objects.remove(ob)
                o = bl_common.add_cube("N", 2.0, (5, 5, 5))
                o.scale = scale
                self.plate(o)
                bpy.context.view_layer.update()
                (got,) = self.extract().objects
                expect = 8.0 * abs(scale[0] * scale[1] * scale[2])
                self.assertAlmostEqual(self.volume(got), expect, places=3)    # positive: outward
                # the vertices are the mirrored cube, so the extracted set equals the numpy one
                np.testing.assert_allclose(rows(got.vertices), rows(cube_corners(2.0) * scale + [5, 5, 5]),
                                           atol=1e-3)

    # -- modifiers, instances -------------------------------------------------------------

    def test_modifiers_are_applied(self):
        self.scene.unit_settings.scale_length = 0.001
        sub = bl_common.add_cube("Sub", 10.0, (0, 0, 0))
        sub.modifiers.new("s", "SUBSURF").levels = 2
        arr = bl_common.add_cube("Arr", 10.0, (50, 0, 0))
        m = arr.modifiers.new("a", "ARRAY")
        m.count = 3
        m.relative_offset_displace = (2.0, 0, 0)
        self.plate(sub, arr)
        got = {o.name: o for o in self.extract().objects}
        self.assertGreater(len(got["Sub"].triangles), 12 * 8)
        self.assertEqual(len(got["Arr"].triangles), 36)
        xs = got["Arr"].vertices[:, 0]
        self.assertAlmostEqual(float(xs.min()), 45.0, places=3)
        self.assertAlmostEqual(float(xs.max()), 50 + 5 + 2 * 10 * 2, places=3)     # 3 copies, 20 apart
        # the original mesh is untouched
        self.assertEqual(len(sub.data.polygons), 6)

    def test_collection_instances_become_one_object_each(self):
        self.scene.unit_settings.scale_length = 0.001
        src = bpy.data.collections.new("Src")
        self.scene.collection.children.link(src)
        bl_common.add_cube("Bolt", 4.0, (0, 0, 2), collection=src)
        bpy.context.view_layer.layer_collection.children["Src"].exclude = True
        empties = []
        for i, x in enumerate((20, 60, 100)):
            e = bpy.data.objects.new(f"Inst{i}", None)
            e.instance_type = "COLLECTION"
            e.instance_collection = src
            e.location = (x, 50, 0)
            e.rotation_euler = (0, 0, math.radians(30 * i))
            self.scene.collection.objects.link(e)
            empties.append(e)
        self.plate(*empties)
        bpy.context.view_layer.update()
        objs = self.extract().objects
        self.assertEqual(sorted(o.name for o in objs), ["Bolt#1", "Bolt#2", "Bolt#3"])
        centres = sorted(float(o.vertices[:, 0].mean()) for o in objs)
        np.testing.assert_allclose(centres, [20, 60, 100], atol=1e-3)
        self.assertEqual(len({o.key for o in objs}), 3)            # different placements, different keys

    def test_geometry_nodes_instances_are_extracted(self):
        self.scene.unit_settings.scale_length = 0.001
        ng = bpy.data.node_groups.new("Scatter", "GeometryNodeTree")
        ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        n_in, n_out = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
        iop = ng.nodes.new("GeometryNodeInstanceOnPoints")
        cube = ng.nodes.new("GeometryNodeMeshCube")
        cube.inputs["Size"].default_value = (6.0, 6.0, 6.0)
        ng.links.new(n_in.outputs[0], iop.inputs["Points"])
        ng.links.new(cube.outputs["Mesh"], iop.inputs["Instance"])
        ng.links.new(iop.outputs["Instances"], n_out.inputs[0])
        base = bl_common.add_cube("Grid", 40.0, (50, 50, 0))
        base.modifiers.new("gn", "NODES").node_group = ng
        self.plate(base)
        bpy.context.view_layer.update()
        objs = self.extract().objects
        self.assertEqual(len(objs), 8)                  # one instance per base vertex
        self.assertTrue(all(o.name.startswith("Cube#") or "#" in o.name for o in objs), [o.name for o in objs])
        xs = sorted({round(float(o.vertices[:, 0].mean()), 1) for o in objs})
        self.assertEqual(xs, [30.0, 70.0])

    # -- plate membership, checks, keys ---------------------------------------------------

    def test_only_plate_objects_are_extracted(self):
        a = bl_common.add_cube("In", 1.0, (0, 0, 0))
        bl_common.add_cube("Out", 1.0, (5, 0, 0))
        self.plate(a)
        self.assertEqual([o.name for o in self.extract().objects], ["In"])

    def test_degenerate_objects_are_skipped_with_a_warning(self):
        good = bl_common.add_cube("Good", 1.0)
        empty = bpy.data.objects.new("Empty mesh", bpy.data.meshes.new("em"))
        self.scene.collection.objects.link(empty)
        self.plate(good, empty)
        res = self.extract()
        self.assertEqual([o.name for o in res.objects], ["Good"])
        self.assertEqual([(i["code"], i["object_name"], i["level"]) for i in res.issues],
                         [("degenerate_object", "Empty mesh", "warning")])

    def test_manifold_check_runs_only_on_check_and_slice(self):
        from slicewright.blender import extract
        from slicewright.core import meshcheck
        o = bl_common.add_cube("Open", 1.0)
        bm = __import__("bmesh").new()
        bm.from_mesh(o.data)
        bm.faces.ensure_lookup_table()
        __import__("bmesh").ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY")
        bm.to_mesh(o.data)
        bm.free()
        self.plate(o)
        calls = []
        real = meshcheck.non_manifold_edges
        meshcheck.non_manifold_edges = lambda t: calls.append(1) or real(t)
        self.addCleanup(setattr, meshcheck, "non_manifold_edges", real)
        self.extract()
        bpy.context.view_layer.update()          # depsgraph updates must not trigger it either
        self.assertEqual(calls, [])
        res = extract.check_plate(bpy.context)
        self.assertEqual(len(calls), 1)
        self.assertIn("mesh_open_edges", [i["code"] for i in res.issues])
        self.assertIn("4 open", [i["message"] for i in res.issues if i["code"] == "mesh_open_edges"][0])

    def test_check_plate_reports_volume_problems_and_empty_plate(self):
        self.scene.unit_settings.scale_length = 0.001
        from slicewright.blender import extract
        self.assertEqual([i["code"] for i in extract.check_plate(bpy.context).issues], ["empty_plate"])
        o = bl_common.add_cube("Big", 20.0, (250, 100, 300))
        self.plate(o)
        codes = sorted(i["code"] for i in extract.check_plate(bpy.context).issues)
        self.assertEqual(codes, ["object_outside_bed", "object_too_tall"])

    def test_check_plate_operator_fills_the_issue_list(self):
        from slicewright.blender import registry
        self.scene.unit_settings.scale_length = 0.001
        o = bl_common.add_cube("Big", 20.0, (250, 100, 10))
        self.plate(o)
        self.assertEqual(bpy.ops.slicewright.check_plate(), {"FINISHED"})
        self.assertEqual([i["code"] for i in registry.state.plate_issues], ["object_outside_bed"])
        self.assertEqual(set(registry.state.plate_issues[0]), {"level", "code", "message", "opt_key", "object_name"})

    def test_input_keys_track_changes(self):
        self.scene.unit_settings.scale_length = 0.001
        o = bl_common.add_cube("K", 10.0, (50, 50, 5))
        self.plate(o)
        k1 = self.extract().objects[0].key
        self.assertEqual(k1, self.extract().objects[0].key)
        o.location.x += 1
        bpy.context.view_layer.update()
        k2 = self.extract().objects[0].key
        self.assertNotEqual(k1, k2)
        o.slicewright.filament = 2
        self.assertNotEqual(k2, self.extract().objects[0].key)

    # -- the fake engine accepts what we extract ------------------------------------------

    def test_extracted_objects_go_into_a_fake_job(self):
        import fake_engine as sc
        from slicewright.blender import extract
        self.scene.unit_settings.scale_length = 0.001
        a = bl_common.add_cube("A", 20.0, (60, 60, 10))
        b = bl_common.add_cube("B", 20.0, (120, 60, 12))          # floats 2 mm: ensure_on_bed must stay False
        b.slicewright.filament = 2
        self.plate(a, b)
        res = extract.extract_plate(bpy.context)
        printer = {"name": "P", "printable_area": ["0x0", "256x0", "256x256", "0x256"],
                   "printable_height": "250", "nozzle_diameter": ["0.4"]}
        process = {"name": "Q", "layer_height": "0.2", "initial_layer_print_height": "0.2"}
        filament = {"name": "F", "filament_type": ["PLA"], "filament_colour": ["#FFFFFF"],
                    "nozzle_temperature": ["220"]}
        flat = sc.normalize_config(sc.compose_config(printer, process, [filament, filament]))["config"]
        job = sc.SliceJob()
        job.set_config(flat)
        handles = extract.add_to_job(job, res.objects)
        self.assertEqual(handles, [0, 1])
        issues = job.validate()
        self.assertEqual([i for i in issues if i["level"] == "error"], [])
        self.assertFalse(any(i["code"] == "moved_to_bed" for i in issues))
        self.assertEqual(job._objects[1].extruder, 2)


if __name__ == "__main__":
    bl_common.run("__main__")
