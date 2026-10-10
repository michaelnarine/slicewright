# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the Arrange operator against the fake engine (plan M4 layer 7). Real arrange lands at M8."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import numpy as np  # noqa: E402

import bpy  # noqa: E402


class ArrangeTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        self.addon = slicewright
        self.scene = bl_common.fresh_scene()
        self.addon.register()
        self.addCleanup(self.addon.unregister)
        self.scene = bpy.context.scene

    def plate(self, *objs):
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        bpy.ops.slicewright.plate_add()

    def footprints(self):
        from slicewright.blender import meshdata
        out = {}
        for name, v in meshdata.plate_vertices_mm(bpy.context).items():
            out[name] = (float(v[:, 0].min()), float(v[:, 1].min()), float(v[:, 0].max()), float(v[:, 1].max()),
                         float(v[:, 2].min()))
        return out

    @staticmethod
    def overlap(a, b):
        return a[0] < b[2] - 1e-6 and b[0] < a[2] - 1e-6 and a[1] < b[3] - 1e-6 and b[1] < a[3] - 1e-6

    def assert_packed(self, fp):
        names = list(fp)
        for i, a in enumerate(names):
            x0, y0, x1, y1, _ = fp[a]
            self.assertTrue(x0 >= -1e-3 and y0 >= -1e-3 and x1 <= 256 + 1e-3 and y1 <= 256 + 1e-3, (a, fp[a]))
            for b in names[i + 1:]:
                self.assertFalse(self.overlap(fp[a], fp[b]), (a, b))

    def test_stacked_objects_are_spread_without_overlap_and_inside_the_bed(self):
        cubes = [bl_common.add_cube(f"C{i}", 30.0, (50, 50, 15)) for i in range(5)]     # all on top of each other
        self.plate(*cubes)
        self.assertEqual(bpy.ops.slicewright.arrange(), {"FINISHED"})
        fp = self.footprints()
        self.assertEqual(len(fp), 5)
        self.assert_packed(fp)
        for c in cubes:
            self.assertAlmostEqual(c.location.z, 15.0, places=3)                  # Z untouched
            self.assertAlmostEqual(c.scale.x, 1.0)

    def test_spacing_option_widens_the_gaps(self):
        cubes = [bl_common.add_cube(f"C{i}", 30.0, (50, 50, 15)) for i in range(2)]
        self.plate(*cubes)
        bpy.ops.slicewright.arrange(spacing=20.0)
        fp = self.footprints()
        gap = max(fp["C1"][0] - fp["C0"][2], fp["C0"][0] - fp["C1"][2],
                  fp["C1"][1] - fp["C0"][3], fp["C0"][1] - fp["C1"][3])
        self.assertAlmostEqual(gap, 20.0, places=2)

    def test_orientation_scale_and_negative_scale_survive(self):
        a = bl_common.add_cube("A", 20.0, (10, 10, 10))
        a.rotation_euler = (0, 0, math.radians(30))
        a.scale = (-1.5, 1.0, 1.0)
        b = bl_common.add_cube("B", 20.0, (10, 10, 10))
        self.plate(a, b)
        before = np.array(a.matrix_world)[:3, :3].copy()
        bpy.ops.slicewright.arrange()
        np.testing.assert_allclose(np.array(a.matrix_world)[:3, :3], before, atol=1e-5)
        self.assert_packed(self.footprints())

    def test_unit_scale_is_honoured(self):
        self.scene.unit_settings.scale_length = 0.01                              # 1 BU = 10 mm
        cubes = [bl_common.add_cube(f"C{i}", 3.0, (5, 5, 1.5)) for i in range(4)]  # 30 mm cubes
        self.plate(*cubes)
        bpy.ops.slicewright.arrange()
        self.assert_packed(self.footprints())

    def test_instances_move_with_their_instancer(self):
        src = bpy.data.collections.new("Src")
        self.scene.collection.children.link(src)
        bl_common.add_cube("Bolt", 20.0, (0, 0, 10), collection=src)
        bpy.context.view_layer.layer_collection.children["Src"].exclude = True
        empties = []
        for i in range(3):
            e = bpy.data.objects.new(f"G{i}", None)
            e.instance_type = "COLLECTION"
            e.instance_collection = src
            e.location = (100, 100, 0)
            self.scene.collection.objects.link(e)
            empties.append(e)
        self.plate(*empties)
        self.assertEqual(bpy.ops.slicewright.arrange(), {"FINISHED"})
        self.assert_packed(self.footprints())
        self.assertEqual(len({(round(e.location.x), round(e.location.y)) for e in empties}), 3)

    def test_arrange_is_an_undoable_operator(self):
        # ed.undo has no usable context in -b; the GUI smoke exercises the real undo step.
        self.assertIn("UNDO", bpy.types.SLICEWRIGHT_OT_arrange.bl_options)

    def test_objects_that_do_not_fit_report_an_arrange_error_and_stay_put(self):
        big = bl_common.add_cube("Big", 300.0, (128, 128, 150))
        small = bl_common.add_cube("Small", 10.0, (20, 20, 5))
        self.plate(big, small)
        before = [tuple(o.location) for o in (big, small)]
        self.assertEqual(bpy.ops.slicewright.arrange(), {"CANCELLED"})
        self.assertEqual([tuple(o.location) for o in (big, small)], before)
        self.assertTrue(big.select_get(), "the offending object is selected")

    def test_a_busy_engine_is_reported_not_raised_and_nothing_moves(self):
        import fake_engine as sc
        from slicewright.blender import arrange as arr
        cubes = [bl_common.add_cube(f"C{i}", 30.0, (50, 50, 15)) for i in range(2)]
        self.plate(*cubes)
        holder = sc.SliceJob()
        holder.set_config(arr.arrange_config(sc, self.scene))
        v = np.array([[x, y, z] for x in (100, 120) for y in (100, 120) for z in (0, 20)], np.float32)
        t = np.array([(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
                      (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)], np.int32)
        holder.add_object("x", v, t)
        holder.start()                                                             # holds the engine lock
        self.addCleanup(holder.cancel)
        before = [tuple(c.location) for c in cubes]
        out = arr.run_arrange(bpy.context)
        self.assertEqual(out.error.kind, "Busy")
        self.assertEqual(out.error.action, "retry_later")
        self.assertEqual(bpy.ops.slicewright.arrange(), {"CANCELLED"})
        self.assertEqual([tuple(c.location) for c in cubes], before)

    def test_poll_needs_prepare_mode_and_a_plate(self):
        self.assertFalse(bpy.ops.slicewright.arrange.poll())                       # no plate yet
        c = bl_common.add_cube("C", 10.0)
        self.plate(c)
        self.assertTrue(bpy.ops.slicewright.arrange.poll())
        self.scene.slicewright.mode = "PREVIEW"
        self.assertFalse(bpy.ops.slicewright.arrange.poll())

    def test_degenerate_objects_are_skipped_not_fatal(self):
        good = [bl_common.add_cube(f"C{i}", 20.0, (50, 50, 10)) for i in range(2)]
        empty = bpy.data.objects.new("Nothing", bpy.data.meshes.new("n"))
        self.scene.collection.objects.link(empty)
        self.plate(*good, empty)
        self.assertEqual(bpy.ops.slicewright.arrange(), {"FINISHED"})
        self.assert_packed(self.footprints())

    def test_the_engine_only_sees_hull_prisms(self):
        import fake_engine as sc
        from slicewright.blender import arrange as arr
        seen = []
        real = sc.SliceJob.add_object

        def spy(job, name, v, t, **kw):
            seen.append((name, len(v), len(t)))
            return real(job, name, v, t, **kw)
        sc.SliceJob.add_object = spy
        self.addCleanup(setattr, sc.SliceJob, "add_object", real)
        ob = bl_common.add_cube("Sub", 30.0, (50, 50, 15))
        ob.modifiers.new("s", "SUBSURF").levels = 3
        self.plate(ob)
        arr.run_arrange(bpy.context)
        self.assertEqual(len(seen), 1)
        self.assertLess(seen[0][1], 200)           # a prism over the hull (the subdivided cube has 386 vertices)


if __name__ == "__main__":
    bl_common.run("__main__")
