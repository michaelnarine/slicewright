# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: scene units, the bed source, the millimetre scene, bed handler hygiene (plan M4 layer 1)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402,F401  (puts addon/ and addon/tests/ on sys.path)

import bpy  # noqa: E402


class UnitsBedTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        self.addon = slicewright
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.addon.register()
        self.addCleanup(self.addon.unregister)
        self.scene = bpy.context.scene

    def test_default_scene_is_a_metre_per_unit(self):
        from slicewright.blender.units import scene_mm_per_bu
        self.assertEqual(self.scene.unit_settings.scale_length, 1.0)
        self.assertEqual(scene_mm_per_bu(self.scene), 1000.0)

    def test_mm_per_bu_follows_scale_length(self):
        from slicewright.blender.units import scene_mm_per_bu
        self.scene.unit_settings.scale_length = 0.01
        self.assertAlmostEqual(scene_mm_per_bu(self.scene), 10.0, places=4)   # RNA stores float32

    def test_use_millimetre_scene_sets_units(self):
        from slicewright.blender.units import scene_mm_per_bu
        self.assertEqual(bpy.ops.slicewright.use_mm_scene(), {"FINISHED"})
        self.assertAlmostEqual(self.scene.unit_settings.scale_length, 0.001)
        self.assertEqual(self.scene.unit_settings.length_unit, "MILLIMETERS")
        self.assertAlmostEqual(scene_mm_per_bu(self.scene), 1.0)

    def test_bed_source_reads_the_scene_properties(self):
        from slicewright.blender import bed_source
        props = self.scene.slicewright
        bed = bed_source.current_bed(self.scene)
        self.assertEqual(bed.bounds, (0.0, 0.0, 256.0, 256.0))
        self.assertEqual(bed.exclude, ())
        props.printable_area = "0x0,180x0,180x180,0x180"
        props.bed_exclude_area = "0x0,20x0,20x20,0x20"
        props.printable_height = 150.0
        bed = bed_source.current_bed(self.scene)
        self.assertEqual(bed.bounds, (0.0, 0.0, 180.0, 180.0))
        self.assertEqual(len(bed.exclude), 1)
        self.assertEqual(bed.height, 150.0)

    def test_an_unusable_printable_area_gives_no_bed(self):
        from slicewright.blender import bed_source
        self.scene.slicewright.printable_area = "garbage"
        self.assertIsNone(bed_source.current_bed(self.scene))

    def test_bed_handler_is_registered_and_removed(self):
        from slicewright.blender import bed_draw
        self.assertTrue(bed_draw.is_registered())
        self.addon.unregister()
        self.assertFalse(bed_draw.is_registered())
        self.addon.register()
        self.assertTrue(bed_draw.is_registered())

    def test_bed_draw_in_background_mode_never_raises(self):
        from slicewright.blender import bed_draw
        bed_draw.draw()      # no GPU context in -b: a no-op, never an error


if __name__ == "__main__":
    bl_common.run("__main__")
