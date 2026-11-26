# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the Prepare / Preview switch (03 section 1.1) against the fake engine.

Timers never fire in ``-b`` (03 section 7.9a): the tests pump ``slicing.poll_job`` and drive the tick runner by hand.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class Layout:
    """A ``UILayout`` stand-in that records the operator buttons drawn."""

    def __init__(self, log):
        self.log = log

    def row(self, align=False):
        return self

    def separator(self):
        self.log.append(("separator",))

    def operator(self, idname, text="", icon="NONE", depress=False):
        self.log.append(("operator", idname, text, depress))


class ModeBase(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright import prefs
        from slicewright.blender import mode, paths, registry, slicing, timers, volume
        from slicewright.blender.preview import runtime
        self.addon, self.mode, self.slicing, self.timers, self.runtime = slicewright, mode, slicing, timers, runtime
        self.volume, self.registry = volume, registry
        self.tmp = tempfile.mkdtemp(prefix="sw-mode-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        paths.set_override(os.path.join(self.tmp, "user"))
        self.addCleanup(paths.set_override, None)
        real_get = prefs.get
        prefs.get = lambda: prefs._Defaults
        self.addCleanup(setattr, prefs, "get", real_get)
        self.scene = bl_common.fresh_scene()
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        self.sw.printer_edits.printable_area = "0x0,256x0,256x256,0x256"
        self.sw.printer_edits.printable_height = 250.0
        self.cube = bl_common.add_cube("Cube", 20.0, (100.0, 100.0, 10.0))
        self.cube.select_set(True)
        bpy.context.view_layer.objects.active = self.cube
        bpy.ops.slicewright.plate_add()

    def finish(self):
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    def pump(self, limit=500):
        for _ in range(limit):
            if self.slicing.poll_job() is None:
                return
        self.fail("the slice never finished")

    def drain(self):
        runner = self.timers.runner
        for _ in range(10_000):
            for task in runner.pending():
                if task.name.startswith("preview:"):                  # the GPU upload cannot run in -b (03 7.9a)
                    runner.cancel(task)
            if runner.idle:
                return
            runner.tick()
        self.fail("the tick runner never went idle")

    def slice(self):
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.pump()
        self.assertEqual(self.sw.job_state, "done")



class ModeTests(ModeBase):
    # -- the switch ---------------------------------------------------------------------------------------

    def test_a_new_scene_is_in_prepare_and_the_preview_button_slices_first_without_a_slice(self):
        self.assertEqual(self.sw.mode, "PREPARE")
        self.assertTrue(bpy.ops.slicewright.mode_prepare.poll())
        self.assertTrue(bpy.ops.slicewright.mode_preview.poll())                  # no slice yet: the button slices first
        self.assertEqual(self.mode.set_mode(self.scene, "PREVIEW"), "Slice the plate to preview it")
        self.assertEqual(self.sw.mode, "PREPARE")

    def test_the_preview_button_without_a_slice_slices_and_then_shows_the_preview(self):
        self.assertIsNone(self.runtime.get(self.scene))
        self.assertEqual(self.sw.mode, "PREPARE")
        bpy.ops.slicewright.mode_preview()
        self.pump()
        self.assertEqual(self.sw.job_state, "done")
        self.assertEqual(self.sw.mode, "PREVIEW")
        self.assertIsNotNone(self.runtime.get(self.scene))

    def test_a_slice_switches_to_preview_and_the_buttons_go_back_and_forth(self):
        self.slice()
        self.assertEqual(self.sw.mode, "PREVIEW")
        self.assertTrue(bpy.ops.slicewright.mode_preview.poll())
        self.assertEqual(bpy.ops.slicewright.mode_prepare(), {"FINISHED"})
        self.assertEqual(self.sw.mode, "PREPARE")
        self.assertIsNotNone(self.runtime.get(self.scene))                        # the slice is kept
        self.assertEqual(bpy.ops.slicewright.mode_preview(), {"FINISHED"})
        self.assertEqual(self.sw.mode, "PREVIEW")

    def test_editing_after_a_slice_marks_it_stale_and_never_flips_the_mode(self):
        from slicewright.blender import staleness
        self.slice()
        self.cube.location.x += 5.0
        bpy.context.view_layer.update()
        staleness.check_now(self.scene, objects=True)
        self.assertTrue(self.sw.stale)
        self.assertEqual(self.sw.mode, "PREVIEW")
        self.sw.mode = "PREPARE"
        self.cube.location.x -= 5.0
        bpy.context.view_layer.update()
        staleness.check_now(self.scene, objects=True)
        self.assertFalse(self.sw.stale)
        self.assertEqual(self.sw.mode, "PREPARE")

    def test_the_button_row_marks_the_current_mode_and_the_header_draws_it(self):
        log = []
        self.mode.draw_switch(Layout(log), self.scene)
        self.assertEqual(log, [("operator", "slicewright.mode_prepare", "Prepare", True),
                               ("operator", "slicewright.mode_preview", "Preview", False)])
        self.slice()
        log.clear()

        class Header:
            layout = Layout(log)
        self.mode.draw_header(Header, bpy.context)
        self.assertEqual(log, [("separator",), ("operator", "slicewright.mode_prepare", "Prepare", False),
                               ("operator", "slicewright.mode_preview", "Preview", True)])

    def test_the_header_entry_is_added_once_and_removed_on_unregister(self):
        self.assertTrue(self.mode.header_installed())
        self.mode.install_header()                                                # a second install adds nothing
        funcs = bpy.types.VIEW3D_HT_header.draw._draw_funcs
        self.assertEqual(list(funcs).count(self.mode.draw_header), 1)
        self.addon.unregister()
        self.assertFalse(self.mode.header_installed())
        self.addon.register()                                                     # the cleanup unregisters again
        self.assertTrue(self.mode.header_installed())

    def test_the_mode_panel_is_registered_and_draws_the_switch(self):
        from slicewright.blender.ui import slice_panel
        log = []
        slice_panel.draw_mode(Layout(log), self.scene)
        self.assertEqual([c[1] for c in log if c[0] == "operator"],
                         ["slicewright.mode_prepare", "slicewright.mode_preview"])

    # -- what depends on the mode -------------------------------------------------------------------------

    def test_arrange_and_the_paint_operators_are_greyed_in_preview(self):
        self.assertTrue(bpy.ops.slicewright.arrange.poll())
        self.assertTrue(bpy.ops.slicewright.paint_clear_all.poll())
        self.slice()
        self.assertEqual(self.sw.mode, "PREVIEW")
        self.assertFalse(bpy.ops.slicewright.arrange.poll())
        self.assertFalse(bpy.ops.slicewright.paint_clear_all.poll())
        self.assertFalse(bpy.ops.slicewright.select_overhangs.poll())
        self.assertFalse(bpy.ops.slicewright.paint_assign.poll())
        bpy.ops.slicewright.mode_prepare()
        self.assertTrue(bpy.ops.slicewright.arrange.poll())
        self.assertTrue(bpy.ops.slicewright.paint_clear_all.poll())

    def test_edit_mode_paint_is_greyed_in_preview(self):
        bpy.ops.object.mode_set(mode="EDIT")
        self.addCleanup(lambda: bpy.ops.object.mode_set(mode="OBJECT") if bpy.context.mode != "OBJECT" else None)
        self.assertTrue(bpy.ops.slicewright.paint_assign.poll())
        bpy.ops.object.mode_set(mode="OBJECT")
        self.slice()
        bpy.ops.object.mode_set(mode="EDIT")
        self.assertFalse(bpy.ops.slicewright.paint_assign.poll())
        self.assertFalse(bpy.ops.slicewright.select_overhangs.poll())

    def test_preview_navigation_and_panel_only_exist_in_preview(self):
        from slicewright.blender.preview import ui as preview_ui
        self.assertFalse(bpy.ops.slicewright.preview_step.poll())
        self.slice()
        self.assertTrue(bpy.ops.slicewright.preview_step.poll())
        self.assertTrue(preview_ui.SLICEWRIGHT_PT_preview.poll(bpy.context))
        bpy.ops.slicewright.mode_prepare()
        self.assertFalse(bpy.ops.slicewright.preview_step.poll())                 # the arrow keys pass through
        self.assertFalse(preview_ui.SLICEWRIGHT_PT_preview.poll(bpy.context))

    def test_back_in_prepare_the_out_of_volume_outlines_are_computed_again(self):
        self.slice()
        self.cube.location = (400.0, 100.0, 10.0)                                 # off the 256 mm bed, in Preview
        bpy.context.view_layer.update()
        self.volume.mark_dirty()
        self.volume.throttle._last = None
        self.assertFalse(self.volume.refresh())                                   # Preview: no live checks
        self.assertNotIn("Cube", self.volume.flagged)
        self.volume.throttle._last = None
        bpy.ops.slicewright.mode_prepare()                                        # recomputed by the switch itself
        self.assertEqual(self.volume.flagged["Cube"]["reasons"], ["outside_bed"])

    def test_the_outlines_and_the_plate_warning_are_not_shown_in_preview(self):
        self.assertTrue(self.volume.shown(self.scene))
        self.slice()
        self.assertFalse(self.volume.shown(self.scene))

    def test_a_script_write_of_the_mode_does_the_same_housekeeping(self):
        self.slice()
        self.volume.flagged["Stale"] = {"reasons": ["outside_bed"], "box": None}
        self.volume.throttle._last = None
        self.sw.mode = "PREPARE"                                                  # not through the operator
        self.assertNotIn("Stale", self.volume.flagged)                            # recomputed at once


class ModeAcrossFileLoadTests(ModeBase):
    """A file load: the saved mode, the cached slice that comes back (03 section 6.4), and nothing left over."""

    def save(self, name="plate.blend"):
        path = os.path.join(self.tmp, name)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return path

    def reload(self, path):
        bpy.ops.wm.open_mainfile(filepath=path)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright

    def test_a_file_saved_in_preview_opens_in_preview_while_its_slice_comes_back(self):
        self.slice()
        self.drain()                                                              # the cache write
        key = self.sw.last_cache_key
        path = self.save()
        self.reload(path)
        self.assertEqual(self.sw.last_cache_key, key)
        self.assertEqual(self.sw.mode, "PREVIEW")                                 # while the preview loads
        self.drain()
        self.assertIsNotNone(self.runtime.get(self.scene))
        self.assertEqual(self.sw.mode, "PREVIEW")

    def test_a_file_saved_in_preview_opens_in_prepare_when_its_cached_slice_is_gone(self):
        self.slice()
        self.drain()
        path = self.save()
        shutil.rmtree(self.slicing.result_cache().root)
        self.reload(path)
        self.assertEqual(self.sw.mode, "PREPARE")
        self.assertIsNone(self.runtime.get(self.scene))
        self.assertEqual(self.sw.mode, "PREPARE")

    def test_a_file_saved_in_preview_that_was_never_sliced_opens_in_prepare(self):
        self.sw.mode = "PREVIEW"                                                  # a script, with nothing sliced
        path = self.save()
        self.reload(path)
        self.assertEqual(self.sw.mode, "PREPARE")

    def test_the_old_files_preview_does_not_outlive_the_load(self):
        empty = self.save("empty.blend")                                          # prepared, never sliced
        self.slice()
        self.drain()
        self.assertIsNotNone(self.runtime.get(self.scene))
        self.assertEqual(self.sw.mode, "PREVIEW")
        self.reload(empty)
        self.assertEqual(self.sw.mode, "PREPARE")
        self.assertIsNone(self.runtime.get(self.scene))


if __name__ == "__main__":
    bl_common.run(__name__)
