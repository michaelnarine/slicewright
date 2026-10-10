# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: plate collection, drop to bed, center, out-of-volume checks (plan M4 layer 2)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class PlateTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        self.addon = slicewright
        self.scene = bl_common.fresh_scene()
        self.addon.register()
        self.addCleanup(self.addon.unregister)
        self.scene = bpy.context.scene

    def select(self, *objs):
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]

    def test_add_creates_and_links_the_plate_collection(self):
        from slicewright.blender import plate_collection as pc
        a = bl_common.add_cube("A")
        self.assertIsNone(pc.plate_collection(self.scene))
        self.select(a)
        self.assertEqual(bpy.ops.slicewright.plate_add(), {"FINISHED"})
        coll = pc.plate_collection(self.scene)
        self.assertEqual(coll.name, "Print plate")
        self.assertIn(coll, list(self.scene.collection.children))
        self.assertEqual(self.scene.slicewright.plate_collection, coll)
        self.assertEqual([o.name for o in pc.plate_objects(self.scene)], ["A"])
        # adding again is a no-op; remove unlinks but keeps the object in the scene
        self.assertEqual(bpy.ops.slicewright.plate_add(), {"CANCELLED"})
        self.assertEqual(bpy.ops.slicewright.plate_remove(), {"FINISHED"})
        self.assertEqual(pc.plate_objects(self.scene), [])
        self.assertIn("A", self.scene.collection.objects)

    def _plate(self, *objs):
        self.select(*objs)
        bpy.ops.slicewright.plate_add()

    def test_drop_to_bed_in_a_millimetre_scene(self):
        a = bl_common.add_cube("A", 10.0, (50, 50, 37))
        self._plate(a)
        bpy.ops.slicewright.drop_to_bed()
        self.assertAlmostEqual(a.location.z, 5.0, places=4)       # lowest point at 0

    def test_drop_to_bed_honours_unit_scale(self):
        self.scene.unit_settings.scale_length = 0.01              # 1 BU = 10 mm
        a = bl_common.add_cube("A", 1.0, (5, 5, 3.0))             # 10 mm cube, floating
        self._plate(a)
        bpy.ops.slicewright.drop_to_bed()
        self.assertAlmostEqual(a.location.z, 0.5, places=4)       # 5 mm up = 0.5 BU

    def test_drop_to_bed_handles_scale_rotation_and_negative_scale(self):
        a = bl_common.add_cube("A", 10.0, (50, 50, 40))
        a.scale = (-2.0, 1.0, 1.0)
        a.rotation_euler = (0.0, 0.0, 0.7)
        self._plate(a)
        bpy.ops.slicewright.drop_to_bed()
        from slicewright.blender import meshdata
        v = meshdata.plate_vertices_mm(bpy.context)["A"]
        self.assertAlmostEqual(float(v[:, 2].min()), 0.0, places=3)

    def test_drop_acts_on_selection_only_when_something_is_selected(self):
        a = bl_common.add_cube("A", 10.0, (50, 50, 30))
        b = bl_common.add_cube("B", 10.0, (90, 50, 30))
        self._plate(a, b)
        self.select(a)
        bpy.ops.slicewright.drop_to_bed()
        self.assertAlmostEqual(a.location.z, 5.0, places=4)
        self.assertAlmostEqual(b.location.z, 30.0, places=4)

    def test_center_moves_the_group_to_the_bed_centre(self):
        a = bl_common.add_cube("A", 10.0, (0, 0, 5))
        b = bl_common.add_cube("B", 10.0, (30, 0, 5))             # group spans x -5..35, y -5..5
        self._plate(a, b)
        bpy.ops.slicewright.center()
        self.assertAlmostEqual(a.location.x, 128.0 - 15.0, places=3)
        self.assertAlmostEqual(b.location.x, 128.0 + 15.0, places=3)
        self.assertAlmostEqual(a.location.y, 128.0, places=3)
        self.assertAlmostEqual(a.location.z, 5.0, places=3)        # Z untouched

    def test_out_of_volume_flags_and_clears(self):
        from slicewright.blender import volume
        self.scene.slicewright.bed_exclude_area = "0x0,30x0,30x30,0x30"
        ok = bl_common.add_cube("Ok", 20.0, (128, 128, 10))
        off = bl_common.add_cube("Off", 20.0, (250, 128, 10))
        tall = bl_common.add_cube("Tall", 20.0, (128, 60, 300))
        low = bl_common.add_cube("Low", 20.0, (200, 200, 0))
        hidden = bl_common.add_cube("InExclude", 20.0, (15, 15, 10))
        self._plate(ok, off, tall, low, hidden)
        flagged = volume.compute(bpy.context)
        self.assertNotIn("Ok", flagged)
        self.assertEqual(flagged["Off"]["reasons"], ["outside_bed"])
        self.assertIn("too_tall", flagged["Tall"]["reasons"])
        self.assertEqual(flagged["Low"]["reasons"], ["below_bed"])
        self.assertEqual(flagged["InExclude"]["reasons"], ["in_exclude_area"])
        off.location.x = 128
        bpy.context.view_layer.update()
        self.assertNotIn("Off", volume.compute(bpy.context))

    def test_objects_outside_the_plate_are_ignored(self):
        from slicewright.blender import volume
        stray = bl_common.add_cube("Stray", 20.0, (500, 500, 500))
        self.assertEqual(volume.compute(bpy.context), {})
        self.assertIsNotNone(stray)

    def test_collection_instances_count_towards_their_instancer(self):
        from slicewright.blender import meshdata
        src = bpy.data.collections.new("Src")
        self.scene.collection.children.link(src)
        bl_common.add_cube("Bolt", 4.0, (0, 0, 2), collection=src)
        bpy.context.view_layer.layer_collection.children["Src"].exclude = True   # the usual instancing setup
        empty = bpy.data.objects.new("Group", None)
        empty.instance_type = "COLLECTION"
        empty.instance_collection = src
        empty.location = (100, 100, 0)
        self.scene.collection.objects.link(empty)
        self._plate(empty)
        verts = meshdata.plate_vertices_mm(bpy.context)
        self.assertEqual(list(verts), ["Group"])
        self.assertAlmostEqual(float(verts["Group"][:, 0].mean()), 100.0, places=3)

    def test_live_refresh_is_throttled_then_runs_from_the_timer(self):
        from slicewright.blender import volume
        a = bl_common.add_cube("A", 20.0, (250, 128, 10))
        self._plate(a)
        clock = [100.0]
        volume.throttle.clock = lambda: clock[0]
        self.addCleanup(setattr, volume.throttle, "clock", __import__("time").monotonic)
        volume.throttle._last = None
        volume._dirty["value"] = True
        self.assertTrue(volume.refresh())                           # first run
        self.assertIn("A", volume.flagged)
        a.location.x = 128
        bpy.context.view_layer.update()
        volume._dirty["value"] = True
        clock[0] += 0.05
        self.assertFalse(volume.refresh())                          # inside 200 ms: deferred
        self.assertIn("A", volume.flagged)                          # stale until the timer runs
        self.assertTrue(bpy.app.timers.is_registered(volume._deferred))
        clock[0] += 0.2
        volume._deferred()                                          # timers never fire in -b: drive it
        self.assertNotIn("A", volume.flagged)

    def test_preview_mode_skips_live_checks(self):
        from slicewright.blender import volume
        a = bl_common.add_cube("A", 20.0, (250, 128, 10))
        self._plate(a)
        self.scene.slicewright.mode = "PREVIEW"
        volume._dirty["value"] = True
        self.assertFalse(volume.refresh())

    def test_volume_handlers_are_removed_on_unregister(self):
        from slicewright.blender import volume
        self.assertIn(volume._on_depsgraph_update, bpy.app.handlers.depsgraph_update_post)
        self.addon.unregister()
        self.assertNotIn(volume._on_depsgraph_update, bpy.app.handlers.depsgraph_update_post)
        self.assertFalse(volume.is_registered())
        self.addon.register()


if __name__ == "__main__":
    bl_common.run("__main__")
