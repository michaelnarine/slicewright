# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: stale-preview detection (03 section 6.3) against the fake engine.

Timers never fire in ``-b`` (03 section 7.9a), so the tests call ``staleness._tick`` (the timer callback) where the
timer would run, and ``slicing.poll_job`` for the slice.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class StalenessTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright.blender import registry, slicing, staleness
        from slicewright.blender.preview import runtime
        self.addon, self.registry, self.slicing, self.st, self.runtime = slicewright, registry, slicing, staleness, runtime
        self.scene = bl_common.fresh_scene()
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        self.sw.printer_edits.printable_area = "0x0,256x0,256x256,0x256"
        self.sw.printer_edits.printable_height = 250.0
        self.cube = bl_common.add_cube("Cube", 20.0, (100.0, 100.0, 10.0))
        self.other = bl_common.add_cube("Other", 20.0, (200.0, 200.0, 10.0))     # in the scene, not on the plate
        self.cube.select_set(True)
        bpy.ops.slicewright.plate_add()

    def finish(self):
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    def pump(self, limit=500):
        for _ in range(limit):
            if self.slicing.poll_job() is None:
                return
        self.fail("the slice never finished")

    def slice(self):
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.pump()
        self.assertEqual(self.sw.job_state, "done")
        self.flush()

    def flush(self):
        """What the throttled timer does: run once, then it is gone (it returns ``None``)."""
        if bpy.app.timers.is_registered(self.st._tick):
            bpy.app.timers.unregister(self.st._tick)
        self.assertIsNone(self.st._tick())

    def update(self):
        bpy.context.view_layer.update()

    def pending(self):
        return dict(self.st._rt.pending)

    def move(self, ob, dx):
        ob.location.x += dx
        self.update()

    # -- a fresh slice is not stale -----------------------------------------------------------------------

    def test_a_finished_slice_is_not_stale_and_stays_so_after_the_confirming_check(self):
        self.slice()
        self.assertFalse(self.sw.stale)
        self.assertEqual(self.st.check_now(self.scene, objects=True), False)

    def test_nothing_is_ever_stale_without_a_result(self):
        self.move(self.cube, 10.0)
        self.sw.process_edits.layer_height = 0.3
        self.assertEqual(self.pending(), {})
        self.assertFalse(bpy.app.timers.is_registered(self.st._tick))
        self.assertIsNone(self.st.check_now(self.scene))
        self.assertFalse(self.sw.stale)
        self.sw.process_edits.property_unset("layer_height")

    # -- plate changes ------------------------------------------------------------------------------------

    def test_moving_a_plate_object_marks_stale_and_moving_it_back_clears_it(self):
        self.slice()
        self.move(self.cube, 10.0)
        self.assertEqual(self.pending(), {self.scene.name: True})
        self.assertTrue(bpy.app.timers.is_registered(self.st._tick))
        self.assertFalse(self.sw.stale)                                            # nothing computed in the event
        self.flush()
        self.assertTrue(self.sw.stale)
        self.move(self.cube, -10.0)
        self.flush()
        self.assertFalse(self.sw.stale)                                            # the key is equal again

    def test_rotating_and_scaling_mark_stale(self):
        self.slice()
        self.cube.rotation_euler.z = 0.5
        self.update()
        self.flush()
        self.assertTrue(self.sw.stale)
        self.cube.rotation_euler.z = 0.0
        self.cube.scale = (1.5, 1.0, 1.0)
        self.update()
        self.flush()
        self.assertTrue(self.sw.stale)
        self.cube.scale = (1.0, 1.0, 1.0)
        self.update()
        self.flush()
        self.assertFalse(self.sw.stale)

    def test_editing_the_mesh_marks_stale(self):
        self.slice()
        me = self.cube.data
        me.vertices[0].co.x -= 3.0
        me.update()
        self.update()
        self.flush()
        self.assertTrue(self.sw.stale)
        me.vertices[0].co.x += 3.0
        me.update()
        self.update()
        self.flush()
        self.assertFalse(self.sw.stale)

    def test_a_modifier_marks_stale_and_removing_it_clears_it(self):
        self.slice()
        mod = self.cube.modifiers.new("Arr", "ARRAY")
        mod.count = 2
        mod.constant_offset_displace = (30.0, 0.0, 0.0)
        self.update()
        self.flush()
        self.assertTrue(self.sw.stale)
        self.cube.modifiers.remove(mod)
        self.update()
        self.flush()
        self.assertFalse(self.sw.stale)

    def test_a_modifier_setting_marks_stale(self):
        mod = self.cube.modifiers.new("Arr", "ARRAY")
        mod.count = 2
        mod.constant_offset_displace = (30.0, 0.0, 0.0)
        self.update()
        self.slice()
        mod.count = 3
        self.update()
        self.flush()
        self.assertTrue(self.sw.stale)

    def test_a_modifier_operand_object_moving_marks_stale(self):
        cutter = bl_common.add_cube("Cutter", 10.0, (100.0, 100.0, 10.0))
        mod = self.cube.modifiers.new("Cut", "BOOLEAN")
        mod.object, mod.operation = cutter, "DIFFERENCE"
        self.update()
        self.slice()
        self.move(cutter, 4.0)
        self.flush()
        self.assertTrue(self.sw.stale)

    def test_adding_and_removing_a_plate_object_marks_stale(self):
        self.slice()
        for ob in bpy.context.view_layer.objects:
            ob.select_set(ob is self.other)
        bpy.ops.slicewright.plate_add()
        self.update()
        self.flush()
        self.assertTrue(self.sw.stale)
        bpy.ops.slicewright.plate_remove()
        self.update()
        self.flush()
        self.assertFalse(self.sw.stale)

    def test_hiding_a_plate_object_marks_stale(self):
        for ob in bpy.context.view_layer.objects:
            ob.select_set(ob is self.other)
        bpy.ops.slicewright.plate_add()
        self.slice()
        self.other.hide_viewport = True
        self.update()
        self.flush()
        self.assertTrue(self.sw.stale)
        self.other.hide_viewport = False
        self.update()
        self.flush()
        self.assertFalse(self.sw.stale)

    def test_per_object_properties_mark_stale(self):
        self.slice()
        self.cube.slicewright.filament = 2
        self.flush()
        self.assertTrue(self.sw.stale)
        self.cube.slicewright.filament = 0
        self.flush()
        self.assertFalse(self.sw.stale)

    def test_changes_to_objects_off_the_plate_are_ignored(self):
        self.slice()
        before = dict(self.st.stats)
        self.move(self.other, 25.0)
        self.other.rotation_euler.z = 1.0
        self.update()
        self.assertEqual(self.pending(), {})
        self.assertFalse(bpy.app.timers.is_registered(self.st._tick))
        self.flush()
        self.assertEqual({k: v for k, v in self.st.stats.items() if k != "handler_calls"},
                         {k: v for k, v in before.items() if k != "handler_calls"})
        self.assertFalse(self.sw.stale)

    # -- settings -----------------------------------------------------------------------------------------

    def test_a_process_setting_marks_stale_and_changing_it_back_clears_it_without_re_extracting(self):
        self.slice()
        extractions = self.st.stats["extractions"]
        self.sw.process_edits.layer_height = 0.3
        self.assertEqual(self.pending(), {self.scene.name: False})                  # settings only: no re-extraction
        self.flush()
        self.assertTrue(self.sw.stale)
        self.sw.process_edits.layer_height = 0.2
        self.flush()
        self.assertFalse(self.sw.stale)
        self.assertEqual(self.st.stats["extractions"], extractions)

    def test_a_printer_setting_and_the_bed_mark_stale(self):
        self.slice()
        self.sw.printer_edits.nozzle_diameter = "0.6"
        self.flush()
        self.assertTrue(self.sw.stale)
        self.sw.printer_edits.property_unset("nozzle_diameter")
        self.st.mark()                                                             # property_unset calls no callback
        self.flush()
        self.assertFalse(self.sw.stale)
        old = self.sw.printer_edits.printable_area
        self.sw.printer_edits.printable_area = "0x0,300x0,300x300,0x300"
        self.flush()
        self.assertTrue(self.sw.stale)
        self.sw.printer_edits.printable_area = old
        self.flush()
        self.assertFalse(self.sw.stale)

    def test_clearing_overrides_through_config_pg_marks(self):
        from slicewright.blender import config_pg
        slot = self.sw.filaments.add()
        self.slice()
        slot.overrides.nozzle_temperature = "235"
        self.flush()
        self.assertTrue(self.sw.stale)
        config_pg.clear(slot.overrides, "nozzle_temperature")
        self.assertEqual(self.pending(), {self.scene.name: False})
        self.flush()
        self.assertFalse(self.sw.stale)

    def test_filament_slots_mark_stale(self):
        slot = self.sw.filaments.add()
        self.slice()
        slot.color = (1.0, 0.0, 0.0)
        self.flush()
        self.assertTrue(self.sw.stale)
        slot.color = (0.8, 0.8, 0.8)
        self.flush()
        self.assertFalse(self.sw.stale)
        self.sw.filaments.add()                       # the add operator needs the profile library (picker.add_slot)
        self.st.mark()
        self.flush()
        self.assertTrue(self.sw.stale)
        self.assertEqual(bpy.ops.slicewright.filament_remove(index=1), {"FINISHED"})
        self.assertEqual(self.pending(), {self.scene.name: False})        # the operator marks: nothing else did
        self.flush()
        self.assertFalse(self.sw.stale)

    def test_the_unit_scale_marks_stale(self):
        self.slice()
        self.scene.unit_settings.scale_length = 0.002
        self.update()
        self.flush()
        self.assertTrue(self.sw.stale)

    # -- the add-on's own writes are ignored --------------------------------------------------------------

    def test_job_view_and_preview_writes_do_not_mark_anything(self):
        self.slice()
        before = dict(self.st.stats)
        sw, p = self.sw, self.sw.preview
        sw.job_message, sw.job_progress = "Slicing 50%", 0.5
        sw.stale = False
        sw.last_cache_key = sw.last_cache_key
        sw.mode = "PREPARE"
        sw.mode = "PREVIEW"
        sw.settings_role, sw.settings_page, sw.settings_filter = "process", "Quality", "x"
        p.view_type = "speed"
        p.layer_hi = 3
        p.show_travel = True
        p.quality = "LINES"
        p.grey_below = True
        p.role_mask[2] = False
        sw.show_paint_overlay = False
        self.update()
        self.assertEqual(self.pending(), {})
        self.assertFalse(bpy.app.timers.is_registered(self.st._tick))
        self.flush()
        self.assertEqual({k: v for k, v in self.st.stats.items() if k != "handler_calls"},
                         {k: v for k, v in before.items() if k != "handler_calls"})
        self.assertFalse(sw.stale)

    def test_polling_a_running_slice_never_marks_or_computes(self):
        self.slice()
        bpy.ops.slicewright.slice()
        before = dict(self.st.stats)
        for _ in range(5):
            self.slicing.poll_job()
            self.update()
        self.assertEqual(self.pending(), {})
        self.assertEqual({k: v for k, v in self.st.stats.items() if k != "handler_calls"},
                         {k: v for k, v in before.items() if k != "handler_calls"})
        self.pump()

    def test_an_edit_made_while_a_slice_runs_makes_its_result_stale_on_completion(self):
        bpy.ops.slicewright.slice()
        self.pump_n(3)
        self.move(self.cube, 10.0)                                                  # ignored while it runs
        self.assertEqual(self.pending(), {})
        self.pump()
        self.assertEqual(self.pending(), {self.scene.name: True})                  # but the finish asks for a check
        self.flush()
        self.assertTrue(self.sw.stale)

    def pump_n(self, n):
        for _ in range(n):
            self.slicing.poll_job()

    # -- throttling: events mark, a timer computes --------------------------------------------------------

    def test_a_drag_of_many_events_is_one_timer_and_one_check(self):
        self.slice()
        stats = dict(self.st.stats)
        intervals = []
        real = self.registry.register_timer
        self.registry.register_timer = lambda fn, first_interval=0.0: (intervals.append(first_interval),
                                                                      real(fn, first_interval))
        self.addCleanup(setattr, self.registry, "register_timer", real)
        for i in range(60):
            self.move(self.cube, 0.5)
        self.assertEqual(self.st.stats["extractions"], stats["extractions"])       # events alone compute nothing
        self.assertEqual(self.st.stats["checks"], stats["checks"])
        self.assertGreaterEqual(self.st.stats["handler_calls"] - stats["handler_calls"], 60)
        self.assertTrue(all(i >= self.st.MIN_DELAY_S for i in intervals), intervals)
        self.assertTrue(bpy.app.timers.is_registered(self.st._tick))
        self.assertEqual(self.registry.state.timers.count(self.st._tick), 1)               # tracked once
        self.flush()
        self.assertEqual(self.st.stats["extractions"], stats["extractions"] + 1)
        self.assertEqual(self.st.stats["checks"], stats["checks"] + 1)
        self.assertTrue(self.sw.stale)

    def test_the_timer_is_idempotent_and_a_second_burst_waits_out_the_throttle(self):
        self.slice()
        self.move(self.cube, 5.0)
        self.flush()
        done = dict(self.st.stats)
        self.flush()
        self.flush()
        self.assertEqual(self.st.stats, done)                                       # nothing pending: nothing computed
        intervals = []
        real = self.registry.register_timer
        self.registry.register_timer = lambda fn, first_interval=0.0: (intervals.append(first_interval),
                                                                      real(fn, first_interval))
        self.addCleanup(setattr, self.registry, "register_timer", real)
        self.move(self.cube, 5.0)
        self.assertEqual(len(intervals), 1)
        self.assertGreater(intervals[0], self.st.MIN_DELAY_S)                       # right after a check: the throttle
        self.assertLessEqual(intervals[0], self.st.INTERVAL_S)

    def test_a_settings_only_burst_never_extracts_the_plate(self):
        self.slice()
        extractions = self.st.stats["extractions"]
        for i in range(20):
            self.sw.process_edits.layer_height = 0.2 + 0.01 * (i + 1)
        self.flush()
        self.assertEqual(self.st.stats["extractions"], extractions)
        self.assertEqual(self.st.stats["key_computes"], self.st.stats["checks"])   # one key per check
        self.assertTrue(self.sw.stale)

    def test_an_invalid_setting_counts_as_changed(self):
        self.slice()
        self.sw.process_edits.layer_height = -1.0
        self.flush()
        self.assertTrue(self.sw.stale)
        self.sw.process_edits.layer_height = 0.2
        self.flush()
        self.assertFalse(self.sw.stale)

    # -- undo, preview lifetime, loading ------------------------------------------------------------------

    def test_undo_and_redo_recheck_everything(self):
        self.slice()
        self.sw.process_edits["layer_height"] = 0.4                                # no update callback, like an undo
        self.assertEqual(self.pending(), {})
        for handler in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
            self.assertTrue(any(h.__name__ == "_undo_redo_post" for h in handler))
        next(h for h in bpy.app.handlers.undo_post if h.__name__ == "_undo_redo_post")(self.scene)
        self.assertEqual(self.pending(), {self.scene.name: True})
        self.flush()
        self.assertTrue(self.sw.stale)
        self.sw.process_edits.property_unset("layer_height")

    def test_stale_is_reset_by_a_completed_slice(self):
        self.slice()
        self.move(self.cube, 10.0)
        self.flush()
        self.assertTrue(self.sw.stale)
        self.slice()
        self.assertFalse(self.sw.stale)

    def test_clearing_the_preview_resets_stale_and_the_caches(self):
        from slicewright.blender.preview import handler
        self.slice()
        self.move(self.cube, 10.0)
        self.flush()
        self.assertTrue(self.sw.stale)
        handler.clear(self.scene)
        self.assertFalse(self.sw.stale)
        self.assertEqual((self.st._rt.object_keys, self.st._rt.plate, self.st._rt.pending), ({}, {}, {}))
        self.assertIsNone(self.runtime.get(self.scene))
        self.sw.job_state = "idle"                                                 # no preview and no finished slice
        self.move(self.cube, 1.0)
        self.assertEqual(self.pending(), {})

    def test_a_file_load_drops_the_caches_and_the_flag(self):
        self.slice()
        self.move(self.cube, 10.0)
        self.assertTrue(self.st._rt.object_keys)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            self.sw.stale = True
            bpy.ops.wm.open_mainfile(filepath=path)
        self.assertEqual((self.st._rt.object_keys, self.st._rt.plate, self.st._rt.pending), ({}, {}, {}))
        self.assertFalse(bpy.app.timers.is_registered(self.st._tick))
        self.assertFalse(bpy.context.scene.slicewright.stale)

    # -- what the user sees --------------------------------------------------------------------------------

    def test_the_banner_shows_in_the_slice_panel_also_after_a_failure(self):
        from slicewright.blender.ui import slice_panel as ui

        class Layout:
            def __init__(self, log):
                self.log, self.scale_y, self.alert = log, 1.0, False

            def row(self, align=False):
                return self

            def box(self):
                return Layout(self.log)

            def column(self, align=False):
                return self

            def operator(self, idname, text="", icon="NONE"):
                pass

            def label(self, text="", icon="NONE"):
                self.log.append(text)
        log = []
        ui.draw_slice(Layout(log), self.sw)
        self.assertNotIn("Preview out of date", log)
        self.sw.stale = True
        ui.draw_slice(Layout(log), self.sw)
        self.assertEqual(log, ["Preview out of date"])
        self.sw.job_state, self.sw.job_message = "failed", "Validation failed: x"
        log.clear()
        ui.draw_slice(Layout(log), self.sw)
        self.assertEqual(log, ["Validation failed: x", "Preview out of date"])

    def test_a_stale_preview_is_drawn_desaturated(self):
        from slicewright.blender.preview import params, props
        self.slice()
        rt, p = self.runtime.get(self.scene), props.of(self.scene)
        self.assertEqual(params.draw_params(p, rt).saturation, 1.0)
        self.assertEqual(params.draw_params(p, rt, stale=True).saturation, params.STALE_SATURATION)
        self.assertLess(params.STALE_SATURATION, 1.0)

    # -- hygiene --------------------------------------------------------------------------------------------

    def test_unregister_removes_the_handlers_the_timer_and_the_hooks(self):
        self.slice()
        self.move(self.cube, 10.0)
        self.assertTrue(bpy.app.timers.is_registered(self.st._tick))
        self.addon.unregister()
        for name in ("depsgraph_update_post", "undo_post", "redo_post"):
            self.assertEqual(getattr(bpy.app.handlers, name), self.before[name], name)
        self.assertFalse(bpy.app.timers.is_registered(self.st._tick))
        self.assertEqual(self.registry.state.timers, [])
        self.assertFalse(self.st._rt.active)
        self.assertEqual((self.st._rt.object_keys, self.st._rt.plate, self.st._rt.pending), ({}, {}, {}))
        self.assertNotIn(self.st._on_file_load, self.registry.state.load_post_hooks)
        self.st.mark(self.scene)                                                   # a late call is harmless
        self.assertEqual(self.pending(), {})


if __name__ == "__main__":
    bl_common.run("__main__")
