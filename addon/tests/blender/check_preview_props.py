# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: preview settings, scrubbing operator and draw-parameter mapping (headless).

No GPU is needed: ``draw_params`` only reads settings and the result's layer table.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402

FIXTURES = os.path.join(bl_common.TESTS_DIR, "fixtures", "gcode")


class PreviewPropsTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from fake_engine.gcode import from_gcode
        from slicewright.blender.preview import params, props, runtime
        self.addon, self.props_mod, self.runtime, self.params = slicewright, props, runtime, params
        self.before = bl_common.handler_snapshot()
        self.addCleanup(self.finish)
        self.addon.register()
        self.scene = bpy.context.scene
        self.rt = runtime.PreviewRuntime(from_gcode(os.path.join(FIXTURES, "plain_square.gcode")))
        runtime.put(self.scene, self.rt)
        self.p = getattr(self.scene, props.PROP_NAME)
        for name in self.p.bl_rna.properties.keys():         # values persist in the scene between tests
            if name != "rna_type":
                self.p.property_unset(name)

    def finish(self):
        self.addon.unregister()
        self.assertFalse(hasattr(bpy.types.Scene, self.props_mod.PROP_NAME))
        self.assertEqual(bl_common.handler_snapshot(), self.before)
        self.assertIsNone(self.runtime.get(self.scene))
        kc = bpy.context.window_manager.keyconfigs.addon
        if kc is not None:
            for km in kc.keymaps:
                self.assertFalse([i for i in km.keymap_items if i.idname == "slicewright.preview_step"])

    def test_defaults(self):
        self.assertEqual(self.p.view_type, "feature")
        self.assertFalse(self.p.show_travel or self.p.scrub_moves)
        self.assertTrue(all(self.p.role_mask))

    def test_step_operator_moves_layers_and_moves_within_clamps(self):
        n = len(self.rt.layers["z"])
        ops = bpy.ops.slicewright.preview_step
        ops(target="TOP", delta=1)
        self.assertEqual(self.p.layer_hi, 1)
        ops(target="TOP", delta=100)
        self.assertEqual(self.p.layer_hi, n - 1)
        ops(target="BOTTOM", delta=100)
        self.assertEqual(self.p.layer_lo, n - 1)                  # never past the top layer
        ops(target="BOTTOM", delta=-100)
        self.assertEqual(self.p.layer_lo, 0)
        ops(target="MOVE", delta=-3)
        self.assertTrue(self.p.scrub_moves)
        last = int(self.rt.layers["last"][n - 1] - self.rt.layers["first"][n - 1])
        self.assertEqual(self.p.move_pos, last - 3)
        ops(target="MOVE", delta=1000)
        self.assertFalse(self.p.scrub_moves)                      # back to the whole layer

    def test_operator_is_unavailable_without_a_result(self):
        self.runtime.put(self.scene, None)
        self.assertFalse(bpy.ops.slicewright.preview_step.poll())

    def test_draw_params_follow_the_settings(self):
        p = self.p
        p.layer_hi = 99                                           # beyond the table: clamped
        p.show_retracts, p.show_seams, p.grey_below = True, True, True
        p.view_type = "speed"
        p.role_mask[2] = False
        dp = self.params.draw_params(p, self.rt, viewport=(640, 480))
        n = len(self.rt.layers["z"])
        self.assertEqual((dp.lo, dp.hi, dp.p), (0, n - 1, None))
        self.assertEqual(dp.view_mode, 1)
        self.assertEqual(dp.markers, ("Retract", "Unretract", "Seam"))
        self.assertEqual(dp.role_mask, 0xFFFFFFFF & ~(1 << 2))
        self.assertEqual(dp.grey_below, n - 1)
        self.assertIsNone(dp.nozzle)
        p.scrub_moves, p.move_pos = True, 5
        dp = self.params.draw_params(p, self.rt)
        self.assertEqual(dp.p, 5)
        self.assertEqual(len(dp.nozzle), 3)

    def test_lines_lod_by_quality_or_by_threshold(self):
        self.assertFalse(self.params.draw_params(self.p, self.rt).lines_lod)
        self.p.quality = "LINES"
        self.assertTrue(self.params.draw_params(self.p, self.rt).lines_lod)
        self.p.quality = "TUBES"
        self.p.layer_hi = 99
        self.assertTrue(self.params.draw_params(self.p, self.rt, lines_threshold=10).lines_lod)

    def test_legend_chips_become_preview_icons_and_are_released(self):
        from slicewright.blender.preview import chips
        icon = chips.chip(0xFF7D38)                               # icon ids stay 0 in background mode
        self.assertEqual(chips.chip(0xFF7D38), icon)
        img = chips._coll["chip_ff7d38"]
        self.assertEqual(tuple(img.image_size), (16, 16))
        self.assertAlmostEqual(img.image_pixels_float[0], 1.0, places=2)
        chips.gradient()
        self.assertIn("range_gradient", chips._coll)
        chips.release()
        self.assertIsNone(chips._coll)

    def test_view_listeners_hear_view_and_range_changes(self):
        seen = []
        self.runtime.on_view_change(lambda scene: seen.append(scene.name))
        self.p.view_type = "fan"
        self.p.range_fixed = True
        self.assertEqual(seen, [self.scene.name] * 2)


if __name__ == "__main__":
    bl_common.run("__main__")
