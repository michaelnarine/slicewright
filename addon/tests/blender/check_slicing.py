# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the Slice operator, the poll timer and the preview hand-over, against the fake engine.

Timers never fire in ``-b`` (03 section 7.9a), so the tests pump ``slicing.poll_job`` directly, as the timer would.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class SlicingTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright.blender import registry, slicing, timers
        from slicewright.blender.preview import runtime
        self.addon, self.registry, self.slicing, self.timers, self.runtime = slicewright, registry, slicing, timers, runtime
        self.scene = bl_common.fresh_scene()           # reading factory settings re-adds Blender's own handlers
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        self.cube = bl_common.add_cube("Cube", 20.0, (100.0, 100.0, 10.0))
        self.cube.select_set(True)
        bpy.ops.slicewright.plate_add()

    def finish(self):
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    def pump(self, n=None, limit=500):
        """Call the timer callback ``n`` times, or until it stops itself."""
        for i in range(limit):
            if n is not None and i >= n:
                return
            if self.slicing.poll_job() is None:
                return
        if n is None:
            self.fail("the slice never finished")

    # -- a full slice -----------------------------------------------------------------------------------

    def test_slice_runs_to_done_and_loads_the_preview(self):
        self.assertEqual(self.sw.job_state, "idle")
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.assertEqual(self.sw.job_state, "validating")
        self.assertTrue(bpy.app.timers.is_registered(self.slicing.poll_job))      # armed, persistent
        seen = []
        for _ in range(100):
            more = self.slicing.poll_job()
            seen.append((self.sw.job_state, self.sw.job_progress, self.sw.job_message))
            if more is None:
                break
        states = [s for s, _, _ in seen]
        self.assertIn("slicing", states)
        self.assertEqual(states[-1], "done")
        self.assertTrue(any(m.startswith("Slicing ") and "%" in m for _, _, m in seen), seen)
        progress = [p for _, p, _ in seen]
        self.assertEqual(progress, sorted(progress))
        self.assertEqual((self.sw.job_progress, self.sw.job_message), (1.0, "Done"))
        self.assertEqual(len(self.sw.last_cache_key), 32)
        self.assertFalse(self.sw.stale)
        self.assertEqual(self.sw.mode, "PREVIEW")
        rt = self.runtime.get(self.scene)
        self.assertIsNotNone(rt)                                                  # show_result ran
        self.assertEqual(len(rt.layers["z"]), 100)                                # 20 mm at 0.2 mm
        self.assertIsNotNone(rt.controller)
        self.assertFalse(self.slicing.is_live())
        self.assertEqual(self.slicing.poll_job(), None)                           # the timer stops itself

    def test_the_cache_key_follows_the_plate(self):
        bpy.ops.slicewright.slice()
        self.pump()
        first = self.sw.last_cache_key
        bpy.ops.slicewright.slice()
        self.pump()
        self.assertEqual(self.sw.last_cache_key, first)                           # identical inputs
        self.cube.location.x += 10.0
        bpy.context.view_layer.update()
        bpy.ops.slicewright.slice()
        self.pump()
        self.assertNotEqual(self.sw.last_cache_key, first)

    def test_a_default_printer_and_filament_slice_without_a_library(self):
        from slicewright.blender import slice_inputs
        req = slice_inputs.build_request(bpy.context, threads=2)
        self.assertEqual((req.threads, req.tag, len(req.objects)), (2, self.scene.name, 1))
        self.assertEqual([f["name"] for f in req.filaments], ["Default filament"])
        self.assertIn("printable_area", req.printer)
        self.assertIsInstance(req.printer["printable_area"], list)                # vectors in preset-file form
        self.assertIn("layer_height", req.process)
        self.assertNotIn("layer_height", req.printer)                             # keys narrowed to their role

    def test_slot_overrides_and_colour_reach_the_filament_preset(self):
        from slicewright.blender import slice_inputs
        slot = self.sw.filaments.add()
        slot.color = (1.0, 0.0, 0.5)
        slot.overrides.nozzle_temperature = "235"
        (filament,) = slice_inputs.filament_dicts(self.sw, slice_inputs.config_pg.schema())
        self.assertEqual(filament["filament_colour"], ["#FF0080"])
        self.assertEqual(filament["nozzle_temperature"], ["235"])

    # -- cancelling ---------------------------------------------------------------------------------------

    def test_cancel_mid_run_ends_cancelled_without_a_preview(self):
        bpy.ops.slicewright.slice()
        self.pump(3)
        self.assertEqual(self.sw.job_state, "slicing")
        self.assertTrue(bpy.ops.slicewright.slice_cancel.poll())
        self.assertEqual(bpy.ops.slicewright.slice_cancel(), {"FINISHED"})
        self.assertEqual(self.sw.job_message, "Cancelling…")
        self.pump()
        self.assertEqual((self.sw.job_state, self.sw.job_message), ("cancelled", "Cancelled"))
        self.assertIsNone(self.runtime.get(self.scene))
        self.assertEqual(self.sw.last_cache_key, "")
        self.assertFalse(bpy.ops.slicewright.slice_cancel.poll())                 # nothing left to cancel
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})               # and the engine is free again
        self.pump()
        self.assertEqual(self.sw.job_state, "done")

    def test_a_second_slice_cancels_the_first(self):
        bpy.ops.slicewright.slice()
        self.pump(3)
        first_key = self.slicing.runner().status.key
        self.cube.scale = (0.5, 0.5, 0.5)                                         # the second job slices a smaller plate
        bpy.context.view_layer.update()
        bpy.ops.slicewright.slice()
        self.assertEqual(self.sw.job_message, "Cancelling…")
        self.pump()
        self.assertEqual(self.sw.job_state, "done")
        self.assertNotEqual(self.sw.last_cache_key, first_key)
        self.assertEqual(len(self.runtime.get(self.scene).layers["z"]), 50)       # 10 mm: the new plate

    def test_a_file_load_cancels_the_job_and_drops_its_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plate.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.slicewright.slice()
            self.pump(3)
            bpy.ops.wm.open_mainfile(filepath=path)                               # load_pre cancels, load_post resets
            scene = bpy.context.scene
            self.assertEqual(scene.slicewright.job_state, "idle")
            self.pump()
            self.assertEqual(scene.slicewright.job_state, "idle")
            self.assertIsNone(self.runtime.get(scene))
            self.assertEqual(scene.slicewright.last_cache_key, "")
            self.assertFalse(self.slicing.is_live())
            self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})           # the engine lock was released
            self.pump()
            self.assertEqual(bpy.context.scene.slicewright.job_state, "done")

    # -- failures show the typed error --------------------------------------------------------------------

    def test_an_object_off_the_bed_fails_with_a_validation_message(self):
        self.cube.location.x = 5000.0
        bpy.context.view_layer.update()
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.pump()
        self.assertEqual(self.sw.job_state, "failed")
        self.assertTrue(self.sw.job_message.startswith("Validation failed:"), self.sw.job_message)
        self.assertIn("Cube", self.sw.job_message)
        self.assertIn("object_outside_bed", [i["code"] for i in self.registry.state.plate_issues])
        self.assertIsNone(self.runtime.get(self.scene))

    def test_an_invalid_setting_fails_with_a_settings_message(self):
        self.sw.process_edits.layer_height = -1.0
        self.assertEqual(bpy.ops.slicewright.slice(), {"CANCELLED"})              # fails at once: no engine job to poll
        self.assertEqual(self.sw.job_state, "failed")
        self.assertTrue(self.sw.job_message.startswith("Settings error:"), self.sw.job_message)
        self.assertIn("layer_height", self.sw.job_message)
        self.assertFalse(self.slicing.is_live())
        self.sw.process_edits.property_unset("layer_height")

    def test_an_engine_thread_config_error_reaches_the_status(self):
        self.sw.printer_edits.machine_start_gcode = "{ unbalanced"
        bpy.ops.slicewright.slice()
        self.pump()
        self.assertEqual(self.sw.job_state, "failed")
        self.assertTrue(self.sw.job_message.startswith("Settings error:"), self.sw.job_message)
        self.sw.printer_edits.property_unset("machine_start_gcode")

    def test_an_empty_plate_is_refused_with_a_warning(self):
        bpy.data.objects.remove(self.cube)
        self.assertEqual(bpy.ops.slicewright.slice(), {"CANCELLED"})
        self.assertEqual(self.sw.job_state, "failed")
        self.assertIn("no objects", self.sw.job_message)

    def test_busy_engine_is_reported_as_a_typed_failure(self):
        import fake_engine as sc
        holder = sc.SliceJob()
        holder.set_config(sc.normalize_config(sc.compose_config({"name": "p"}, {"name": "q"}, [{"name": "f"}]))["config"])
        holder.start()
        self.addCleanup(holder.cancel)
        self.assertEqual(bpy.ops.slicewright.slice(), {"CANCELLED"})
        self.assertEqual(self.sw.job_state, "failed")
        self.assertIn("busy", self.sw.job_message.lower())
        self.assertFalse(self.slicing.is_live())

    # -- the panel ----------------------------------------------------------------------------------------

    def test_panel_draws_slice_when_idle_and_progress_with_cancel_while_running(self):
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
                self.log.append(("operator", idname, text))

            def progress(self, factor=0.0, type="BAR", text=""):
                self.log.append(("progress", round(factor, 2), text))

            def label(self, text="", icon="NONE"):
                self.log.append(("label", text))

        log = []
        ui.draw_slice(Layout(log), self.sw)
        self.assertEqual(log, [("operator", "slicewright.slice", "Slice")])
        self.sw.job_state, self.sw.job_progress, self.sw.job_message = "slicing", 0.42, "Slicing 42% · Generating infill"
        log.clear()
        ui.draw_slice(Layout(log), self.sw)
        self.assertEqual(log, [("progress", 0.42, "Slicing 42% · Generating infill"),
                               ("operator", "slicewright.slice_cancel", "")])
        self.sw.job_state, self.sw.job_message = "failed", "Validation failed: x"
        log.clear()
        ui.draw_slice(Layout(log), self.sw)
        self.assertEqual(log[-1], ("label", "Validation failed: x"))

    def test_the_status_lines_are_drawn_apart_from_the_button_so_they_never_move_it(self):
        from slicewright.blender.ui import slice_panel as ui

        class Layout:
            def __init__(self, log):
                self.log, self.scale_y, self.alert = log, 1.0, False

            def row(self, align=False):
                return self

            def box(self):
                return self

            def column(self, align=False):
                return self

            def operator(self, idname, text="", icon="NONE"):
                self.log.append(("operator", idname, text))

            def progress(self, factor=0.0, type="BAR", text=""):
                self.log.append(("progress", round(factor, 2), text))

            def label(self, text="", icon="NONE"):
                self.log.append(("label", text))

        self.sw.job_state, self.sw.job_message = "failed", "Validation failed: x"
        button, status = [], []
        self.assertFalse(ui.draw_slice_button(Layout(button), self.sw))
        ui.draw_status(Layout(status), self.sw)
        self.assertEqual(button, [("operator", "slicewright.slice", "Slice")])        # the same whatever happened
        self.assertEqual(status[0], ("label", "Validation failed: x"))
        self.sw.job_state, self.sw.job_message = "idle", ""
        again = []
        ui.draw_slice_button(Layout(again), self.sw)
        self.assertEqual(again, button)
        self.sw.job_state, self.sw.job_progress, self.sw.job_message = "slicing", 0.3, "Slicing 30%"
        running, none = [], []
        self.assertTrue(ui.draw_slice_button(Layout(running), self.sw))
        ui.draw_status(Layout(none), self.sw)                                          # nothing under it while running
        self.assertEqual((running[0][0], none), ("progress", []))
        import inspect
        self.assertIn("draw_status(self.layout", inspect.getsource(ui.SLICEWRIGHT_PT_mode.draw))      # below the mode row
        self.assertNotIn("draw_status", inspect.getsource(ui.SLICEWRIGHT_PT_slice.draw))

    # -- hygiene ------------------------------------------------------------------------------------------

    def test_unregister_with_a_live_job_leaves_nothing_behind(self):
        import fake_engine as sc
        bpy.ops.slicewright.slice()
        self.pump(3)
        self.assertTrue(self.slicing.is_live())
        self.addon.unregister()
        self.assertFalse(bpy.app.timers.is_registered(self.slicing.poll_job))
        self.assertEqual(self.registry.state.timers, [])
        self.assertIsNone(self.slicing._rt.runner)
        self.assertEqual(self.slicing._keymaps, [])
        self.assertNotIn(self.slicing._on_load_pre, self.registry.state.load_pre_hooks)
        probe = sc.SliceJob()                                                     # the engine lock was released
        probe.set_config(sc.normalize_config(sc.compose_config({"name": "p"}, {"name": "q"}, [{"name": "f"}]))["config"])
        probe.start()
        probe.cancel()
        for name in ("SLICEWRIGHT_OT_slice", "SLICEWRIGHT_OT_slice_cancel"):
            self.assertIsNone(bpy.types.Operator.bl_rna_get_subclass_py(name), name)
        self.assertIsNone(bpy.types.Panel.bl_rna_get_subclass_py("SLICEWRIGHT_PT_slice"))


if __name__ == "__main__":
    bl_common.run("__main__")
