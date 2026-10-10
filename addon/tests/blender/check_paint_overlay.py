# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: paint overlay data, dirtiness and debounce (plan M4 layer 5). Drawing is in the GUI smoke."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import numpy as np  # noqa: E402

import bpy  # noqa: E402


class OverlayTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        self.addon = slicewright
        bl_common.fresh_scene()
        self.addon.register()
        self.addCleanup(self.addon.unregister)
        from slicewright.blender.paint import attributes, overlay
        self.attrs, self.overlay = attributes, overlay
        self.clock = [10.0]
        overlay.throttle.clock = lambda: self.clock[0]
        overlay.throttle._last = None
        self.addCleanup(setattr, overlay.throttle, "clock", __import__("time").monotonic)

    def cube(self, name="C", location=(50, 50, 5)):
        ob = bl_common.add_cube(name, 10.0, location)
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.slicewright.plate_add()
        return ob

    def test_only_painted_triangles_are_built(self):
        ob = self.cube()
        self.assertEqual(self.overlay.build_entries(bpy.context), [])        # unpainted: nothing
        self.attrs.write(ob.data, "slicewright_support", [1, 0, 2, 0, 0, 0])
        self.attrs.write(ob.data, "slicewright_filament", [0, 0, 0, 0, 0, 4])
        (e,) = self.overlay.build_entries(bpy.context)
        self.assertEqual(e.triangle_count, 6)                                # 3 painted quads -> 6 triangles
        self.assertEqual(e.positions.dtype, np.float32)
        self.assertEqual(e.colors.shape, (18, 4))
        a = e.colors[:, 3]
        self.assertTrue((np.isclose(a, 0.6) | np.isclose(a, 0.55)).all())
        np.testing.assert_allclose(e.matrix[:3, 3], [50, 50, 5], atol=1e-4)   # object space + matrix

    def test_painted_triangles_belong_to_the_painted_faces(self):
        ob = self.cube()
        self.attrs.write(ob.data, "slicewright_support", [0, 0, 0, 0, 0, 1])
        (e,) = self.overlay.build_entries(bpy.context)
        poly = ob.data.polygons[5]
        centre = np.array(poly.center)
        self.assertTrue(np.allclose(e.positions.mean(axis=0), centre, atol=1e-4))

    def test_non_plate_objects_are_not_drawn(self):
        stray = bl_common.add_cube("Stray", 10.0)
        self.attrs.write(stray.data, "slicewright_support", [1] * 6)
        self.assertEqual(self.overlay.build_entries(bpy.context), [])

    def test_modifier_output_is_what_gets_drawn(self):
        ob = self.cube()
        self.attrs.write(ob.data, "slicewright_support", [1, 0, 0, 0, 0, 0])
        ob.modifiers.new("s", "SUBSURF").levels = 1
        (e,) = self.overlay.build_entries(bpy.context)
        self.assertEqual(e.triangle_count, 8)                                # 1 face -> 4 quads -> 8 triangles

    def test_geometry_updates_mark_dirty_and_the_debounce_defers(self):
        ob = self.cube()
        self.attrs.write(ob.data, "slicewright_support", [1] * 6)
        self.overlay._dirty["value"] = True
        self.assertTrue(self.overlay.refresh(bpy.context, upload=False))
        self.assertEqual(len(self.overlay.entries), 1)
        self.assertFalse(self.overlay._dirty["value"])
        self.attrs.write(ob.data, "slicewright_support", [0] * 6)
        self.overlay.mark_dirty()
        self.clock[0] += 0.05
        self.assertFalse(self.overlay.refresh(bpy.context, upload=False))    # inside 100 ms
        self.assertEqual(len(self.overlay.entries), 1)                       # still the old data
        self.assertTrue(bpy.app.timers.is_registered(self.overlay._deferred_redraw))
        self.clock[0] += 0.1
        self.assertTrue(self.overlay.refresh(bpy.context, upload=False))
        self.assertEqual(self.overlay.entries, [])

    def test_a_depsgraph_geometry_update_marks_dirty(self):
        ob = self.cube()
        self.overlay._dirty["value"] = False
        ob.location.x += 1
        bpy.context.view_layer.update()
        self.assertTrue(self.overlay._dirty["value"])

    def test_visibility_follows_the_toggle_and_the_mode(self):
        scene = bpy.context.scene
        self.assertTrue(self.overlay._visible(scene))
        scene.slicewright.show_paint_overlay = False
        self.assertFalse(self.overlay._visible(scene))
        scene.slicewright.show_paint_overlay = True
        scene.slicewright.mode = "PREVIEW"
        self.assertFalse(self.overlay._visible(scene))

    def test_draw_in_background_mode_is_a_no_op(self):
        self.overlay.draw()

    def test_handlers_are_removed_on_unregister(self):
        self.assertIn(self.overlay._on_depsgraph_update, bpy.app.handlers.depsgraph_update_post)
        self.addon.unregister()
        self.assertNotIn(self.overlay._on_depsgraph_update, bpy.app.handlers.depsgraph_update_post)
        self.assertFalse(self.overlay.is_registered())
        self.addon.register()


if __name__ == "__main__":
    bl_common.run("__main__")
