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

    def _select_printer(self, preset_id):
        from slicewright.blender import library, timers
        library.request()
        timers.runner.run_until_idle()
        self.scene.slicewright.printer_id = preset_id

    def test_scene_has_no_hand_editable_bed_properties(self):
        props = self.scene.slicewright
        for key in ("printable_area", "bed_exclude_area", "printable_height"):
            self.assertNotIn(key, props.bl_rna.properties)

    def test_no_printer_selected_uses_the_schema_default_bed(self):
        from slicewright.blender import bed_source
        self.assertEqual(self.scene.slicewright.printer_id, "")
        from slicewright.blender import config_pg
        from slicewright.core.bed import parse_bed
        sch = config_pg.schema()    # the engine's own defaults, so this holds on the fake and real engines
        want = parse_bed(sch["printable_area"]["default"], sch["bed_exclude_area"]["default"],
                         float(sch["printable_height"]["default"]))
        bed = bed_source.current_bed(self.scene)
        self.assertEqual((bed.bounds, bed.height), (want.bounds, want.height))
        self.assertEqual(len(bed.exclude), len(want.exclude))

    def test_bed_follows_the_selected_printer_preset(self):
        from slicewright.blender import bed_source
        self._select_printer("sys:Acme/Acme Maker 1 0.4 nozzle")
        bed = bed_source.current_bed(self.scene)
        self.assertEqual((bed.bounds, bed.height), ((0.0, 0.0, 220.0, 220.0), 250.0))
        self._select_printer("sys:Acme/Acme Maker 2 0.4 nozzle")
        bed = bed_source.current_bed(self.scene)
        self.assertEqual((bed.bounds, bed.height), ((0.0, 0.0, 300.0, 300.0), 300.0))

    def test_bed_follows_edits_to_the_printer_buffer(self):
        from slicewright.blender import bed_source
        self._select_printer("sys:Acme/Acme Maker 1 0.4 nozzle")
        cfg = self.scene.slicewright.printer_edits
        cfg.printable_area = "0x0,180x0,180x180,0x180"
        cfg.bed_exclude_area = "0x0,20x0,20x20,0x20"
        cfg.printable_height = 150.0
        bed = bed_source.current_bed(self.scene)
        self.assertEqual(bed.bounds, (0.0, 0.0, 180.0, 180.0))
        self.assertEqual(len(bed.exclude), 1)
        self.assertEqual(bed.height, 150.0)

    def test_an_unusable_printable_area_gives_no_bed(self):
        from slicewright.blender import bed_source
        self._select_printer("sys:Acme/Acme Maker 1 0.4 nozzle")
        self.scene.slicewright.printer_edits.printable_area = "garbage"
        self.assertIsNone(bed_source.current_bed(self.scene))
        self.scene.slicewright.printer_edits.printable_area = "0x0,10x0"       # two points
        self.assertIsNone(bed_source.current_bed(self.scene))

    def test_unusable_bed_blocks_center_and_arrange_and_check_plate(self):
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bl_common
        from slicewright.blender import extract
        ob = bl_common.add_cube("A", 10.0, (5, 5, 5))
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.slicewright.plate_add()
        self.scene.slicewright.printer_edits.printable_area = "garbage"
        with self.assertRaisesRegex(RuntimeError, "printable_area is not usable"):
            bpy.ops.slicewright.center()
        with self.assertRaisesRegex(RuntimeError, "printable_area is not usable"):
            bpy.ops.slicewright.arrange()
        result = extract.check_plate(bpy.context)
        self.assertTrue(any(i["code"] == "validation" and i["level"] == "error" for i in result.issues))

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
