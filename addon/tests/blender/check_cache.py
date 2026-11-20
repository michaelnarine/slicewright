# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the slice result cache and the reconnect after a file load (03 section 6.4), and auto
re-slice (03 section 6.3), against the fake engine.

Timers never fire in ``-b`` (03 section 7.9a): the tests call ``slicing.poll_job`` for the slice and drive the tick
runner (cache writes and loads, preview upload) with ``timers.runner.run_until_idle()``.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class CacheTests(unittest.TestCase):
    cap_mb = 2048
    auto = False

    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import fake_engine
        import slicewright
        from slicewright import prefs
        from slicewright.blender import paths, registry, slicing, staleness, timers
        from slicewright.blender.preview import runtime
        self.sc, self.addon, self.prefs = fake_engine, slicewright, prefs
        self.paths, self.registry, self.slicing, self.st, self.timers, self.runtime = (
            paths, registry, slicing, staleness, timers, runtime)
        self.tmp = tempfile.mkdtemp(prefix="sw-cache-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        paths.set_override(os.path.join(self.tmp, "user"))
        self.addCleanup(paths.set_override, None)
        test = self

        class Prefs(prefs._Defaults):
            cache_cap_mb = test.cap_mb
            auto_reslice = test.auto
        self.prefs_obj = Prefs
        self._real_get = prefs.get
        prefs.get = lambda: Prefs
        self.addCleanup(setattr, prefs, "get", self._real_get)
        self.engine_starts = 0
        real_start = fake_engine.SliceJob.start

        def counting_start(job):
            test.engine_starts += 1
            return real_start(job)
        fake_engine.SliceJob.start = counting_start
        self.addCleanup(setattr, fake_engine.SliceJob, "start", real_start)
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
        bpy.ops.slicewright.plate_add()

    def finish(self):
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    # -- helpers ------------------------------------------------------------------------------------------

    def pump(self, limit=500):
        for _ in range(limit):
            if self.slicing.poll_job() is None:
                return
        self.fail("the slice never finished")

    def drain(self):
        """Everything the tick runner holds: cache writes and loads, the preview upload."""
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

    def cache(self):
        return self.slicing.result_cache()

    def sc_flush_stale(self):
        if bpy.app.timers.is_registered(self.st._tick):
            bpy.app.timers.unregister(self.st._tick)
        self.st._tick()

    def reload(self, path):
        bpy.ops.wm.open_mainfile(filepath=path)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        self.cube = bpy.data.objects["Cube"]

    def save(self, name="plate.blend"):
        path = os.path.join(self.tmp, name)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return path

    def tamper(self, key, name, same_size=True):
        path = os.path.join(self.cache().root, key, name)
        size = os.path.getsize(path)
        with open(path, "wb") as f:
            f.write(b"\x00" * (size if same_size else 5))


class CacheWriteTests(CacheTests):
    def test_a_finished_slice_is_written_to_the_cache_one_task_not_inline(self):
        self.slice()
        key = self.sw.last_cache_key
        self.assertEqual(self.engine_starts, 1)
        task = self.timers.runner.get(f"cache-write:{key}")
        self.assertIsNotNone(task)                                     # queued for the ticks, not done in the slice
        self.assertFalse(self.cache().has(key))
        self.drain()
        entry = self.cache().lookup(key)
        self.assertIsNotNone(entry)
        files = sorted(os.listdir(entry.path))
        for name in ("manifest.json", "out.gcode", "stats.json", "warnings.json", "gcode_line_ends.npy",
                     "moves.position.npy", "moves.type.npy", "layers.z.npy", "layers.first.npy"):
            self.assertIn(name, files)
        self.assertEqual(os.listdir(self.cache().root), [key])         # no staging left behind
        self.assertTrue(entry.path.startswith(os.path.join(self.tmp, "user")))

    def test_the_cache_holds_what_the_engine_produced(self):
        self.slice()
        key = self.sw.last_cache_key
        result = self.runtime.get(self.scene).result
        self.drain()
        got = self.cache().lookup(key).load()
        import numpy as np
        for name, arr in result.moves.items():
            self.assertTrue(np.array_equal(got.moves[name], arr), name)
        self.assertEqual(got.stats["time_s"], result.stats["time_s"])
        self.assertEqual(got.objects, result.objects)

    def test_the_cap_preference_evicts_the_least_recently_used(self):
        self.prefs_obj.cache_cap_mb = 5
        sizes = []
        keys = []
        for scale in (5.0, 4.5):                                       # a 100 mm and a 90 mm cube: ~3.7 and ~3.1 MB
            self.cube.scale = (scale, scale, scale)
            bpy.context.view_layer.update()
            self.slice()
            self.drain()
            keys.append(self.sw.last_cache_key)
            sizes.append(self.cache().lookup(keys[-1], touch=False).bytes)
        self.assertGreater(sum(sizes), 5 * 1024 * 1024)                # together they do not fit
        self.assertLess(max(sizes), 5 * 1024 * 1024)                   # singly they do
        self.assertEqual([e.key for e in self.cache().entries()], [keys[1]])   # the older one made way
        self.assertLessEqual(self.cache().usage(), 5 * 1024 * 1024)
        self.cube.scale = (5.0, 5.0, 5.0)                              # so the first plate slices again
        bpy.context.view_layer.update()
        self.slice()
        self.assertEqual(self.engine_starts, 3)
        self.drain()
        self.assertEqual([e.key for e in self.cache().entries()], [keys[0]])   # LRU again: the 90 mm one went

    def test_a_cap_of_zero_turns_the_cache_off(self):
        self.prefs_obj.cache_cap_mb = 0
        self.assertIsNone(self.cache())
        self.slice()
        self.drain()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "user", "cache")))
        self.slice()
        self.assertEqual(self.engine_starts, 2)                        # no hit without a cache
        self.assertEqual(self.sw.job_message, "Done")

    def test_a_failing_cache_write_does_not_break_the_slice(self):
        self.slice()                                                   # warm path: slice works first
        self.drain()
        self.cube.scale = (0.7, 0.7, 0.7)
        bpy.context.view_layer.update()
        root = self.cache().root
        shutil.rmtree(root, ignore_errors=True)
        with open(root, "w") as f:                                     # a file where the directory should be
            f.write("not a directory")
        self.slice()
        self.assertEqual(self.sw.job_state, "done")
        self.assertIsNotNone(self.runtime.get(self.scene))
        self.drain()                                                   # the write fails in its task, quietly
        self.assertFalse(self.cache().has(self.sw.last_cache_key))
        self.assertTrue(self.timers.runner.idle)


class CacheHitTests(CacheTests):
    def test_the_same_inputs_are_served_from_the_cache_without_an_engine_job(self):
        self.slice()
        key = self.sw.last_cache_key
        self.drain()
        self.assertEqual(self.engine_starts, 1)
        if bpy.app.timers.is_registered(self.slicing.poll_job):       # left armed by the first slice (no timers in -b)
            bpy.app.timers.unregister(self.slicing.poll_job)
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.assertEqual(self.engine_starts, 1)                        # short-circuited
        self.assertFalse(self.slicing.is_live())
        self.assertEqual((self.sw.job_state, self.sw.job_message), ("done", "Done (from cache)"))
        self.assertEqual((self.sw.last_cache_key, self.sw.mode, self.sw.stale), (key, "PREVIEW", False))
        self.assertIsNone(self.slicing.poll_job())                     # the poll timer was never needed
        self.assertFalse(bpy.app.timers.is_registered(self.slicing.poll_job))
        self.assertIsNotNone(self.timers.runner.get(f"cache-load:{self.scene.name}"))   # the load is on the ticks
        self.drain()
        rt = self.runtime.get(self.scene)
        self.assertIsNotNone(rt)
        self.assertEqual(len(rt.layers["z"]), 100)
        self.assertTrue(getattr(rt.result, "from_cache", False))
        self.assertIsNotNone(rt.controller)
        self.assertEqual(self.engine_starts, 1)

    def test_changed_inputs_miss_the_cache_and_slice(self):
        self.slice()
        self.drain()
        self.cube.location.x += 10.0
        bpy.context.view_layer.update()
        self.slice()
        self.assertEqual(self.engine_starts, 2)
        self.assertEqual(self.sw.job_message, "Done")
        self.drain()
        self.cube.location.x -= 10.0                                   # back to the first plate: its entry is still there
        bpy.context.view_layer.update()
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.assertEqual(self.engine_starts, 2)
        self.assertEqual(self.sw.job_message, "Done (from cache)")

    def test_issues_of_the_cached_slice_come_back(self):
        self.sw.printer_edits.thumbnails = "48x48/PNG"                 # none in -b: the engine warns
        self.slice()
        self.drain()
        before = [i["code"] for i in self.registry.state.plate_issues]
        self.assertEqual(before, ["thumbnail_missing", "thumbnails_skipped"])   # the engine's, and our own note
        bpy.ops.slicewright.slice()
        self.assertEqual(self.engine_starts, 1)
        # the engine's warnings were cached; the note was about this run's render, and none happens on a hit
        self.assertEqual([i["code"] for i in self.registry.state.plate_issues], ["thumbnail_missing"])

    def test_a_second_slice_while_the_cached_preview_loads_supersedes_it(self):
        self.slice()
        self.drain()
        bpy.ops.slicewright.slice()
        first = self.timers.runner.get(f"cache-load:{self.scene.name}")
        bpy.ops.slicewright.slice()
        self.assertTrue(first.finished)                                # replaced, not stacked
        self.drain()
        self.assertEqual(len(self.runtime.get(self.scene).layers["z"]), 100)
        self.assertEqual(self.engine_starts, 1)

    def test_an_unreadable_entry_falls_back_to_slicing(self):
        self.slice()
        key = self.sw.last_cache_key
        self.drain()
        self.tamper(key, "moves.type.npy")                             # same size: only reading it tells
        bpy.ops.slicewright.slice()
        self.assertEqual(self.engine_starts, 1)                        # it looked fine
        self.drain()                                                   # the load fails, the slice starts instead
        self.assertEqual(self.engine_starts, 2)
        self.pump()
        self.assertEqual(self.sw.job_state, "done")
        self.assertEqual(self.sw.job_message, "Done")
        self.drain()
        self.assertEqual(len(self.runtime.get(self.scene).layers["z"]), 100)
        self.assertTrue(self.cache().has(key))                         # and the bad entry was replaced
        self.assertEqual(self.cache().lookup(key).load().moves["type"].shape[0],
                         len(self.runtime.get(self.scene).moves["type"]))


class ReconnectTests(CacheTests):
    def sliced_and_saved(self):
        self.slice()
        self.drain()
        return self.sw.last_cache_key, self.save()

    def test_reopening_a_file_restores_the_preview_without_slicing(self):
        key, path = self.sliced_and_saved()
        self.assertEqual(self.engine_starts, 1)
        self.reload(path)
        self.assertEqual(self.sw.last_cache_key, key)                  # saved with the file
        self.assertEqual((self.sw.job_state, self.sw.stale), ("idle", False))   # runtime state was reset
        self.assertIsNone(self.runtime.get(self.scene))                # nothing yet: it loads on the ticks
        self.assertIsNotNone(self.timers.runner.get(f"cache-load:{self.scene.name}"))
        self.drain()
        rt = self.runtime.get(self.scene)
        self.assertIsNotNone(rt)
        self.assertEqual(len(rt.layers["z"]), 100)
        self.assertIsNotNone(rt.controller)
        self.assertEqual(self.engine_starts, 1)                        # no engine job for the restore
        self.assertFalse(self.slicing.is_live())
        self.assertEqual((self.sw.job_state, self.sw.job_message, self.sw.job_progress),
                         ("done", "Restored from cache", 1.0))
        self.assertEqual(self.slicing.note(self.scene), "")
        self.sc_flush_stale()
        self.assertFalse(self.sw.stale)                                # inputs unchanged since the slice

    def test_staleness_is_seeded_from_the_restored_key(self):
        key, _ = self.sliced_and_saved()
        self.cube.location.x += 25.0                                   # edited after the slice, then saved
        bpy.context.view_layer.update()
        path = self.save("edited.blend")
        self.reload(path)
        self.assertFalse(self.sw.stale)
        self.drain()
        self.assertTrue(self.st.has_result(self.scene))
        self.sc_flush_stale()
        self.assertTrue(self.sw.stale)                                 # the restored preview is out of date
        self.assertEqual(self.sw.last_cache_key, key)
        self.assertEqual(self.engine_starts, 1)

    def test_a_missing_entry_leaves_the_preview_empty_with_a_gentle_note(self):
        key, path = self.sliced_and_saved()
        shutil.rmtree(self.cache().root)
        self.reload(path)
        self.drain()
        self.assertIsNone(self.runtime.get(self.scene))
        self.assertEqual(self.sw.last_cache_key, key)                  # kept: nothing was lost but the cache
        self.assertEqual(self.sw.job_state, "idle")
        self.assertIn("gone", self.slicing.note(self.scene))
        self.assertEqual(self.engine_starts, 1)                        # and nothing sliced behind the user's back
        self.assertTrue(self.timers.runner.idle)
        self.assertFalse(self.st.has_result(self.scene))
        self.slice()                                                   # slicing again clears the note
        self.assertEqual(self.slicing.note(self.scene), "")
        self.assertEqual(self.engine_starts, 2)

    def test_a_partial_entry_counts_as_missing(self):
        key, path = self.sliced_and_saved()
        self.tamper(key, "moves.position.npy", same_size=False)        # truncated
        self.reload(path)
        self.drain()
        self.assertIsNone(self.runtime.get(self.scene))
        self.assertIn("gone", self.slicing.note(self.scene))

    def test_an_unreadable_entry_is_a_note_not_a_slice(self):
        key, path = self.sliced_and_saved()
        self.tamper(key, "layers.z.npy")                               # right size, wrong content
        self.reload(path)
        self.drain()
        self.assertIsNone(self.runtime.get(self.scene))
        self.assertIn("could not be read", self.slicing.note(self.scene))
        self.assertEqual(self.engine_starts, 1)

    def test_a_file_without_a_slice_has_nothing_to_restore(self):
        path = self.save()
        self.reload(path)
        self.assertTrue(self.timers.runner.idle)
        self.assertEqual((self.sw.last_cache_key, self.slicing.note(self.scene)), ("", ""))

    def test_loading_another_file_drops_a_restore_in_progress(self):
        key, path = self.sliced_and_saved()
        other = self.save("other.blend")
        self.reload(path)
        task = f"cache-load:{self.scene.name}"
        self.assertIsNotNone(self.timers.runner.get(task))
        bpy.ops.wm.read_homefile(use_empty=True)                       # load_pre cancels the pending restore
        self.assertIsNone(self.timers.runner.get(task))
        self.drain()
        self.assertIsNone(self.runtime.get(bpy.context.scene))
        del other


class PathsTests(CacheTests):
    def test_the_override_roots_every_user_directory(self):
        path = self.paths.user_dir("recovery")
        self.assertEqual(path, os.path.join(self.tmp, "user", "recovery"))
        self.assertTrue(os.path.isdir(path))

    def test_without_an_override_a_source_checkout_falls_back_to_the_config_dir(self):
        self.paths.set_override(None)
        path = self.paths.user_dir("cache/slices", create=False)
        self.assertTrue(path and path.replace("\\", "/").endswith("slicewright/cache/slices"), path)

    def test_the_profile_index_cache_dir_goes_through_user_dir(self):
        from slicewright.blender import library
        path = library._cache_dir()
        self.assertEqual(path, os.path.join(self.tmp, "user", "cache", "profiles"))
        self.assertTrue(os.path.isdir(path))
        self.paths.set_override(None)             # source checkout: the config-dir fallback, not None
        fallback = library._cache_dir()
        self.assertTrue(fallback and fallback.replace("\\", "/").endswith("slicewright/cache/profiles"), fallback)

    def test_an_installed_extension_uses_the_extension_user_path(self):
        # the form paths.user_dir uses once the package is bl_ext.<repo>.slicewright (not creating anything)
        path = bpy.utils.extension_path_user("bl_ext.user_default.slicewright", path="cache/slices", create=False)
        self.assertTrue(path.replace("\\", "/").endswith("slicewright/cache/slices"), path)


class AutoResliceTests(CacheTests):
    auto = True

    def edit(self):
        self.cube.location.x += 15.0
        bpy.context.view_layer.update()
        self.sc_flush_stale()

    def test_a_stale_preview_is_resliced_after_the_debounce(self):
        self.slice()
        self.drain()
        self.edit()
        self.assertTrue(self.sw.stale)
        self.assertTrue(bpy.app.timers.is_registered(self.st._auto_tick))     # armed by the edit
        self.assertEqual(self.engine_starts, 1)
        self.assertIsNone(self.st._auto_tick())                              # the 1.5 s elapsed
        self.assertTrue(self.slicing.is_live())
        self.pump()
        self.assertEqual((self.sw.job_state, self.sw.stale), ("done", False))
        self.assertEqual(self.engine_starts, 2)

    def test_the_default_is_off(self):
        self.assertFalse(self.addon.prefs._Defaults.auto_reslice)
        self.prefs_obj.auto_reslice = False
        self.slice()
        self.drain()
        self.edit()
        self.assertTrue(self.sw.stale)
        self.assertFalse(bpy.app.timers.is_registered(self.st._auto_tick))
        self.st._auto_tick()
        self.assertFalse(self.slicing.is_live())
        self.assertEqual(self.engine_starts, 1)

    def test_never_while_a_job_runs(self):
        self.slice()
        self.drain()
        self.edit()
        bpy.ops.slicewright.slice()                                          # a manual slice is running
        self.assertTrue(self.slicing.is_live())
        starts = self.engine_starts
        self.st._auto_tick()
        self.assertEqual(self.engine_starts, starts)
        self.pump()

    def test_no_result_no_reslice(self):
        self.edit()
        self.st._auto_tick()
        self.assertFalse(self.slicing.is_live())
        self.assertEqual(self.engine_starts, 0)

    def test_every_edit_restarts_the_debounce(self):
        self.slice()
        self.drain()
        calls = []
        real = bpy.app.timers.unregister

        def spy(fn):
            calls.append(fn)
            return real(fn)
        bpy.app.timers.unregister = spy
        self.addCleanup(setattr, bpy.app.timers, "unregister", real)
        for _ in range(3):
            self.cube.location.x += 5.0
            bpy.context.view_layer.update()
        self.assertGreaterEqual(calls.count(self.st._auto_tick), 2)         # re-armed on each edit after the first
        self.assertEqual(self.engine_starts, 1)

    def test_an_edit_that_changes_nothing_in_the_end_does_not_reslice(self):
        self.slice()
        self.drain()
        self.cube.location.x += 15.0
        bpy.context.view_layer.update()
        self.cube.location.x -= 15.0
        bpy.context.view_layer.update()
        self.st._auto_tick()
        self.assertFalse(self.sw.stale)
        self.assertFalse(self.slicing.is_live())
        self.assertEqual(self.engine_starts, 1)


if __name__ == "__main__":
    bl_common.run("__main__")
