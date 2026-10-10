# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the object-mode brush engine, stroke semantics and tool registration (plan M4 layer 6).

Rays are cast directly (no viewport in ``-b``); the event-driven click-through is in the GUI smoke.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bmesh  # noqa: E402
import numpy as np  # noqa: E402

import bpy  # noqa: E402

SUPPORT, FILAMENT = "slicewright_support", "slicewright_filament"
DOWN = (0.0, 0.0, -1.0)


class BrushTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        self.addon = slicewright
        self.scene = bl_common.fresh_scene()
        self.addon.register()
        self.addCleanup(self.addon.unregister)
        self.scene = bpy.context.scene
        from slicewright.blender.paint import attributes, brush
        self.attrs, self.brush = attributes, brush

    def grid_cube(self, name="G", size=40.0, cuts=3, location=(50, 50, 20)):
        """A cube whose faces are cut into (cuts+1)^2 quads: 6 * 16 = 96 faces for cuts=3."""
        ob = bl_common.add_cube(name, size, location)
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
        bm.to_mesh(ob.data)
        bm.free()
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.slicewright.plate_add()
        bpy.context.view_layer.update()
        return ob

    def stroke(self, attr=SUPPORT, value=1, radius_mm=5.0, **kw):
        return self.brush.Stroke(attr, value, radius_mm / 1.0, **kw)         # mm scene: 1 BU = 1 mm

    def top_faces(self, ob):
        return [p.index for p in ob.data.polygons if p.normal.z > 0.9]

    # -- raycast ----------------------------------------------------------------------------

    def test_raycast_hits_the_top_face_and_misses_beside_the_cube(self):
        ob = self.grid_cube()
        hit = self.brush.raycast(self.scene, (50, 50, 100), DOWN)
        self.assertEqual(hit[0], ob)
        self.assertAlmostEqual(hit[2].z, 40.0, places=3)
        self.assertIn(hit[1], self.top_faces(ob))
        self.assertIsNone(self.brush.raycast(self.scene, (200, 200, 100), DOWN))

    def test_raycast_picks_the_nearest_object_and_ignores_non_plate_ones(self):
        a = self.grid_cube("A", location=(50, 50, 20))
        bl_common.add_cube("Stray", 10.0, (50, 50, 90))                      # in the ray, not on the plate
        b = self.grid_cube("B", location=(50, 50, 80))                       # above A, on the plate
        self.assertEqual(self.brush.raycast(self.scene, (50, 50, 200), DOWN)[0], b)
        self.assertEqual(self.brush.raycast(self.scene, (50, 50, 55), DOWN)[0], a)      # between the two cubes

    def test_raycast_uses_the_base_mesh_in_world_space(self):
        ob = self.grid_cube()
        ob.location.x += 100
        ob.rotation_euler.z = 0.5
        bpy.context.view_layer.update()
        self.assertIsNone(self.brush.raycast(self.scene, (50, 50, 100), DOWN))
        hit = self.brush.raycast(self.scene, (150, 50, 100), DOWN)
        self.assertEqual(hit[0], ob)

    # -- dabs --------------------------------------------------------------------------------

    def test_a_dab_paints_the_faces_within_the_radius_and_only_those(self):
        ob = self.grid_cube()                                                # top: 4x4 faces of 10 mm
        s = self.stroke(radius_mm=6.0)
        n = s.dab(self.scene, (50, 50, 100), DOWN)                           # hit at the centre: 4 faces meet there
        painted = np.flatnonzero(self.attrs.read(ob.data, SUPPORT))
        self.assertEqual(n, len(painted))
        self.assertEqual(sorted(painted.tolist()), sorted(set(painted.tolist()) & set(self.top_faces(ob))))
        # the 4 faces around the centre have centres 7.07 mm away: radius 6 catches none via the KD range,
        # but the hit face is always painted
        self.assertEqual(len(painted), 1)
        s2 = self.stroke(radius_mm=8.0)
        s2.dab(self.scene, (50, 50, 100), DOWN)
        self.assertEqual(len(np.flatnonzero(self.attrs.read(ob.data, SUPPORT))), 4)

    def test_a_bigger_radius_wraps_over_the_edge_without_smart_fill(self):
        ob = self.grid_cube()
        s = self.stroke(radius_mm=14.0)
        s.dab(self.scene, (68, 50, 100), DOWN)                               # 2 mm from the +X edge of the top
        values = self.attrs.read(ob.data, SUPPORT)
        sides = [p.index for p in ob.data.polygons if abs(p.normal.x) > 0.9 and p.normal.x > 0]
        self.assertTrue(values[sides].any(), "a plain brush paints the side faces within the radius too")

    def test_smart_fill_stops_at_the_crease(self):
        ob = self.grid_cube()
        s = self.stroke(radius_mm=14.0, smart=True, angle_deg=20.0)
        s.dab(self.scene, (68, 50, 100), DOWN)
        values = self.attrs.read(ob.data, SUPPORT)
        painted = set(np.flatnonzero(values).tolist())
        self.assertTrue(painted)
        self.assertTrue(painted <= set(self.top_faces(ob)), "the 90 degree crease must stop the fill")

    def test_erase_clears_only_the_brushes_attribute(self):
        ob = self.grid_cube()
        self.attrs.write(ob.data, SUPPORT, np.full(96, 2))
        self.attrs.write(ob.data, FILAMENT, np.full(96, 3))
        self.stroke(radius_mm=8.0, erase=True).dab(self.scene, (50, 50, 100), DOWN)
        support = self.attrs.read(ob.data, SUPPORT)
        self.assertEqual(int((support == 0).sum()), 4)
        self.assertTrue((self.attrs.read(ob.data, FILAMENT) == 3).all())

    def test_filament_stroke_writes_the_slot_value(self):
        ob = self.grid_cube()
        self.stroke(attr=FILAMENT, value=5, radius_mm=8.0).dab(self.scene, (50, 50, 100), DOWN)
        self.assertEqual(sorted(set(self.attrs.read(ob.data, FILAMENT).tolist())), [0, 5])

    def test_a_miss_paints_nothing_and_creates_no_attribute(self):
        ob = self.grid_cube()
        self.assertEqual(self.stroke().dab(self.scene, (300, 300, 100), DOWN), 0)
        self.assertIsNone(self.attrs.read(ob.data, SUPPORT))

    def test_cancel_restores_an_existing_attribute_and_removes_a_new_one(self):
        ob = self.grid_cube()
        original = np.zeros(96, np.int32)
        original[[0, 1]] = 2
        self.attrs.write(ob.data, SUPPORT, original)
        s = self.stroke(radius_mm=30.0)
        s.dab(self.scene, (50, 50, 100), DOWN)
        s.dab(self.scene, (50, 50, 100), DOWN)
        self.assertGreater(int((self.attrs.read(ob.data, SUPPORT) != original).sum()), 0)
        s.cancel()
        self.assertEqual(self.attrs.read(ob.data, SUPPORT).tolist(), original.tolist())
        s3 = self.stroke(attr=FILAMENT, value=2)
        s3.dab(self.scene, (50, 50, 100), DOWN)
        self.assertIsNotNone(self.attrs.read(ob.data, FILAMENT))
        s3.cancel()
        self.assertIsNone(self.attrs.read(ob.data, FILAMENT))

    # -- cache ---------------------------------------------------------------------------------

    def test_the_bvh_is_cached_across_dabs_and_survives_the_strokes_own_writes(self):
        ob = self.grid_cube()
        first = self.brush.target_for(ob)
        s = self.stroke(radius_mm=8.0)
        s.dab(self.scene, (50, 50, 100), DOWN)
        bpy.context.view_layer.update()                                      # the attribute write's depsgraph update
        s.dab(self.scene, (60, 60, 100), DOWN)
        self.assertIs(self.brush.target_for(ob), first)
        s.end()

    def test_moving_the_object_invalidates_the_cache(self):
        ob = self.grid_cube()
        first = self.brush.target_for(ob)
        ob.location.x += 5
        bpy.context.view_layer.update()
        self.assertIsNot(self.brush.target_for(ob), first)

    def test_editing_the_mesh_invalidates_the_cache(self):
        ob = self.grid_cube()
        first = self.brush.target_for(ob)
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.translate(bm, verts=bm.verts[:], vec=(0, 0, 1))
        bm.to_mesh(ob.data)
        bm.free()
        bpy.context.view_layer.update()
        self.assertIsNot(self.brush.target_for(ob), first)

    # -- operator and tool ------------------------------------------------------------------

    def test_brush_settings_map_to_the_stroke(self):
        props = self.scene.slicewright
        props.brush_kind = "SEAM_BLOCK"
        from slicewright.core.brush import brush_target
        self.assertEqual(brush_target(props.brush_kind, props.paint_filament), ("slicewright_seam", 2))
        self.assertEqual(props.brush_radius, 5.0)
        self.assertFalse(props.brush_smart)

    def test_the_workspace_tool_is_registered_and_removed(self):
        from slicewright.blender.paint import tool
        self.assertTrue(bpy.types.SLICEWRIGHT_OT_paint_brush)
        self.assertEqual(tool.SLICEWRIGHT_TOOL_paint.bl_idname, "slicewright.paint_tool")
        self.assertTrue(self._tool_present(tool.TOOL_ID))
        self.addon.unregister()
        self.assertFalse(self._tool_present(tool.TOOL_ID))
        self.addon.register()

    @staticmethod
    def _tool_present(idname):
        from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
        cls = ToolSelectPanelHelper._tool_class_from_space_type("VIEW_3D")
        for item in ToolSelectPanelHelper._tools_flatten(cls.tools_from_context(bpy.context, mode="OBJECT")
                                                         if hasattr(cls, "tools_from_context") else []):
            if item is not None and getattr(item, "idname", None) == idname:
                return True
        return False

    def test_the_brush_handlers_are_removed_on_unregister(self):
        self.assertIn(self.brush._on_depsgraph_update, bpy.app.handlers.depsgraph_update_post)
        self.addon.unregister()
        self.assertNotIn(self.brush._on_depsgraph_update, bpy.app.handlers.depsgraph_update_post)
        self.assertEqual(self.brush._cache, {})
        self.addon.register()


if __name__ == "__main__":
    bl_common.run("__main__")
