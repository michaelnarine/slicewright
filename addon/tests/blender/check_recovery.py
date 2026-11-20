# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: recovery copies written before a slice (03 section 6.1), against the fake engine.

Background mode skips recovery copies, so the tests set ``recovery.allow_background``. Time is a fake clock.
"""
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class RecoveryTests(unittest.TestCase):
    enabled = True

    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright import prefs
        from slicewright.blender import paths, recovery, slicing
        self.addon, self.recovery, self.slicing = slicewright, recovery, slicing
        self.tmp = tempfile.mkdtemp(prefix="sw-recovery-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        paths.set_override(os.path.join(self.tmp, "user"))
        self.addCleanup(paths.set_override, None)
        test = self

        class Prefs(prefs._Defaults):
            recovery_copy = test.enabled
            cache_cap_mb = 0                                   # keep the result cache out of these tests
        self.prefs_obj = Prefs
        real_get = prefs.get
        prefs.get = lambda: Prefs
        self.addCleanup(setattr, prefs, "get", real_get)
        self.now = [1000.0]
        recovery.clock = lambda: self.now[0]
        recovery.allow_background = True
        recovery.reset()
        self.addCleanup(self.restore_recovery)
        bl_common.fresh_scene()
        self.before = bl_common.handler_snapshot()
        addon = slicewright
        addon.register()
        self.addCleanup(self.finish)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        self.sw.printer_edits.printable_area = "0x0,256x0,256x256,0x256"
        self.sw.printer_edits.printable_height = 250.0
        self.cube = bl_common.add_cube("Cube", 20.0, (100.0, 100.0, 10.0))
        self.cube.select_set(True)
        bpy.ops.slicewright.plate_add()

    def restore_recovery(self):
        import time
        self.recovery.clock = time.monotonic
        self.recovery.allow_background = False
        self.recovery.reset()

    def finish(self):
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    # -- helpers ------------------------------------------------------------------------------------------

    def folder(self):
        return os.path.join(self.tmp, "user", "recovery")

    def copies(self):
        try:
            return sorted(os.listdir(self.folder()))
        except OSError:
            return []

    def slice(self):
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        for _ in range(500):
            if self.slicing.poll_job() is None:
                break
        self.assertEqual(self.sw.job_state, "done")

    def saved_and_dirty(self, name="plate.blend"):
        path = os.path.join(self.tmp, name)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        self.edit()
        self.assertTrue(bpy.data.is_dirty)
        return path

    def edit(self):
        """An unsaved change. In -b only an undo push marks the file dirty (a property write does not)."""
        self.cube.location.y += 1.0
        bpy.context.view_layer.update()
        bpy.ops.ed.undo_push(message="edit")

    def slice_after(self, seconds):
        self.now[0] += seconds
        self.edit()
        self.slice()


class WhenToWriteTests(RecoveryTests):
    def test_the_first_slice_writes_a_copy(self):
        self.saved_and_dirty()
        self.assertEqual(self.copies(), [])
        self.slice()
        self.assertEqual(self.copies(), ["plate-1.blend"])
        with open(os.path.join(self.folder(), "plate-1.blend"), "rb") as f:
            head = f.read(4)
        self.assertIn(head, (b"BLEN", b"\x28\xb5\x2f\xfd"))        # a .blend, plain or zstd-compressed
        self.assertGreater(os.path.getsize(os.path.join(self.folder(), "plate-1.blend")), 1000)

    def test_a_second_slice_inside_the_interval_does_not(self):
        self.saved_and_dirty()
        self.slice()
        self.slice_after(60.0)
        self.slice_after(238.0)                                    # 298 s after the copy
        self.assertEqual(self.copies(), ["plate-1.blend"])

    def test_the_interval_elapsing_writes_another(self):
        self.saved_and_dirty()
        self.slice()
        self.slice_after(300.0)                                    # exactly the interval
        self.assertEqual(self.copies(), ["plate-1.blend", "plate-2.blend"])
        self.slice_after(10.0)
        self.assertEqual(len(self.copies()), 2)

    def test_the_oldest_are_pruned_to_three(self):
        self.saved_and_dirty()
        self.slice()
        for _ in range(5):
            self.slice_after(301.0)
        self.assertEqual(self.copies(), ["plate-4.blend", "plate-5.blend", "plate-6.blend"])

    def test_a_clean_saved_file_needs_no_copy(self):
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(self.tmp, "clean.blend"))
        self.assertFalse(bpy.data.is_dirty)
        self.assertIsNone(self.recovery.maybe_save())              # the saved file is as good as a copy
        self.assertEqual(self.copies(), [])
        self.edit()
        self.assertEqual(os.path.basename(self.recovery.maybe_save()), "clean-1.blend")

    def test_an_unsaved_file_is_copied_as_untitled(self):
        self.assertEqual(bpy.data.filepath, "")
        self.slice()
        self.assertEqual(self.copies(), ["untitled-1.blend"])


class WhatItMustNotTouchTests(RecoveryTests):
    def test_the_users_path_and_dirty_flag_are_unchanged(self):
        path = self.saved_and_dirty()
        before = (bpy.data.filepath, bpy.data.is_dirty, self.sw.embedded_presets)
        self.slice()
        self.assertEqual(self.copies(), ["plate-1.blend"])
        self.assertEqual((bpy.data.filepath, bpy.data.is_dirty), before[:2])
        self.assertEqual(bpy.data.filepath, path)
        self.assertTrue(bpy.data.is_dirty)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "plate.blend1")))

    def test_the_users_file_on_disk_is_not_rewritten(self):
        path = self.saved_and_dirty()
        stamp = (os.path.getmtime(path), os.path.getsize(path))
        self.slice()
        self.assertEqual((os.path.getmtime(path), os.path.getsize(path)), stamp)

    def test_an_unsaved_file_stays_unsaved(self):
        self.slice()
        self.assertEqual(bpy.data.filepath, "")


class SwitchesTests(RecoveryTests):
    def test_background_mode_skips_it_unless_a_test_asks(self):
        self.saved_and_dirty()
        self.recovery.allow_background = False
        self.assertTrue(bpy.app.background)
        self.assertFalse(self.recovery.enabled())
        self.slice()
        self.assertEqual(self.copies(), [])
        self.recovery.allow_background = True
        self.slice_after(1.0)
        self.assertEqual(self.copies(), ["plate-1.blend"])         # and nothing was held against it

    def test_the_preference_disables_it(self):
        self.saved_and_dirty()
        self.prefs_obj.recovery_copy = False
        self.slice()
        self.slice_after(1000.0)
        self.assertEqual(self.copies(), [])
        self.prefs_obj.recovery_copy = True
        self.slice_after(1.0)
        self.assertEqual(self.copies(), ["plate-1.blend"])

    def test_the_default_is_on(self):
        from slicewright import prefs
        self.assertTrue(prefs._Defaults.recovery_copy)
        self.assertIn("recovery_copy", prefs.SLICEWRIGHT_AP_Preferences.__annotations__)


class FailureTests(RecoveryTests):
    def test_a_read_only_directory_does_not_break_the_slice(self):
        self.saved_and_dirty()
        os.makedirs(self.folder())
        os.chmod(self.folder(), stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, self.folder(), stat.S_IRWXU)
        self.slice()                                               # slice() asserts the job reached done
        self.assertEqual(self.copies(), [])
        self.assertIsNotNone(self.slicing.runner())
        os.chmod(self.folder(), stat.S_IRWXU)                      # fixed: the next slice tries again at once
        self.slice_after(1.0)
        self.assertEqual(self.copies(), ["plate-1.blend"])

    def test_a_file_where_the_directory_should_be_does_not_break_the_slice(self):
        self.saved_and_dirty()
        os.makedirs(os.path.dirname(self.folder()))
        with open(self.folder(), "w") as f:
            f.write("in the way")
        self.slice()
        self.assertEqual(self.sw.job_state, "done")

    def test_a_failing_save_operator_is_swallowed(self):
        self.saved_and_dirty()

        class Boom:
            def __call__(self, *a, **k):
                raise RuntimeError("Error: Cannot open file for writing")
        self.recovery.bpy = type("B", (), {"ops": type("O", (), {"wm": type("W", (), {"save_as_mainfile": Boom()})()})(),
                                           "data": bpy.data, "app": bpy.app})()
        self.addCleanup(setattr, self.recovery, "bpy", bpy)
        self.slice()
        self.assertEqual(self.sw.job_state, "done")
        self.assertEqual(self.copies(), [])


if __name__ == "__main__":
    bl_common.run("__main__")
