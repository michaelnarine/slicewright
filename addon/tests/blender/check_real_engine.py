# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the add-on's slice path against the REAL engine (``slicewright_engine``).

The other ``check_*`` scripts use the fake engine. This one drives the same orchestration (Slice operator, job runner,
result cache, preview hand-over, staleness, Issues list) with the built module and a real printer from the real
profile library (Bambu Lab A1, 0.4 nozzle). Skipped when the module is not built (``SLICEWRIGHT_ENGINE_PATH`` or
``engine/build/engine/python``). The real engine runs on its own threads, so the pump sleeps between polls.
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402

BUILD = os.path.join(bl_common.TESTS_DIR, os.pardir, os.pardir, "engine", "build", "engine", "python")
if not os.environ.get("SLICEWRIGHT_ENGINE_PATH") and os.path.isdir(os.path.join(BUILD, "slicewright_engine")):
    os.environ["SLICEWRIGHT_ENGINE_PATH"] = os.path.abspath(BUILD)
os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "slicewright_engine"
PRINTER = "Bambu Lab A1 0.4 nozzle"


def engine_available() -> bool:
    from slicewright.engine import adapter
    adapter.add_engine_paths()
    try:
        import slicewright_engine  # noqa: F401
    except ImportError:
        return False
    return True


class RealEngineTests(unittest.TestCase):
    def setUp(self):
        if not engine_available():
            self.skipTest("slicewright_engine is not built")
        import slicewright
        from slicewright import prefs
        from slicewright.blender import library, paths, picker, registry, slicing, staleness, timers
        from slicewright.blender.preview import runtime
        self.addon, self.library, self.picker, self.registry = slicewright, library, picker, registry
        self.slicing, self.st, self.timers, self.runtime, self.paths = slicing, staleness, timers, runtime, paths
        self.tmp = tempfile.mkdtemp(prefix="sw-real-")
        paths.set_override(os.path.join(self.tmp, "user"))
        self.addCleanup(paths.set_override, None)

        class Prefs(prefs._Defaults):
            cache_cap_mb = 2048
            auto_reslice = False
        self._real_get = prefs.get
        prefs.get = lambda: Prefs
        self.addCleanup(setattr, prefs, "get", self._real_get)
        bl_common.fresh_scene()
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)
        self.assertEqual(self.registry.state.status.module_name, "slicewright_engine")
        self.assertTrue(self.registry.state.status.ok, self.registry.state.status.summary)
        self.sc = self.registry.state.status.module
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        self.library.request()
        self.drain()
        self.lib = self.library.get()
        entries = [e for e in self.lib.printers() if e.name == PRINTER]
        self.assertEqual(len(entries), 1)
        self.picker.select_printer(self.sw, entries[0])
        self.cube = bl_common.add_cube("Cube", 20.0, (100.0, 100.0, 10.0))
        self.cube.select_set(True)
        bpy.ops.slicewright.plate_add()

    def finish(self):
        if self.slicing.is_live():
            self.slicing.runner().cancel(discard=True)
            self.pump(expect=None)
        self.drain()
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    # -- helpers ------------------------------------------------------------------------------------------

    def pump(self, timeout=60.0, expect="done"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.slicing.poll_job() is None:
                break
            time.sleep(0.005)
        else:
            self.fail("the slice never finished")
        if expect is not None:
            self.assertEqual(self.sw.job_state, expect, self.sw.job_message)

    def drain(self):
        runner = self.timers.runner
        for _ in range(100_000):
            for task in runner.pending():
                if task.name.startswith("preview:"):                  # the GPU upload cannot run in -b (03 7.9a)
                    runner.cancel(task)
            if runner.idle:
                return
            runner.tick()
        self.fail("the tick runner never went idle")

    def slice(self, **kw):
        self.assertEqual(bpy.ops.slicewright.slice(**kw), {"FINISHED"})
        self.pump()

    def flush_stale(self):
        if bpy.app.timers.is_registered(self.st._tick):
            bpy.app.timers.unregister(self.st._tick)
        self.st._tick()

    def issues(self):
        from slicewright.blender import issues
        return issues.current()

    def resize(self, sx, sy, sz):
        """Scale the 20 mm cube about its centre and keep it standing on the bed (an object that floats has an empty
        first layer, which the real engine rejects; the fake never minded)."""
        self.cube.scale = (sx, sy, sz)
        self.cube.location.z = 10.0 * sz
        bpy.context.view_layer.update()

    def add_second_cube(self, name="Cube2", x=160.0, filament=2):
        ob = bl_common.add_cube(name, 20.0, (x, 100.0, 10.0))
        for o in bpy.context.view_layer.objects:
            o.select_set(o is ob)
        bpy.ops.slicewright.plate_add()
        ob.slicewright.filament = filament
        return ob

    # -- the library --------------------------------------------------------------------------------------

    def test_the_real_library_is_complete_and_its_index_is_cached(self):
        self.assertEqual(self.library.status()[0], "ready")
        self.assertGreater(len(self.lib.printers()), 900)
        # the shared bases live in OrcaFilamentLibrary/filament/base/: vendors that inherit from them resolve
        self.assertIsNotNone(self.lib.index.lookup("filament", "fdm_filament_pla", "TwoTrees"))
        index_dir = os.path.join(self.tmp, "user", "cache", "profiles")
        self.assertTrue(os.path.isdir(index_dir) and os.listdir(index_dir), "the profile-index cache was not written")

    # -- a full slice -------------------------------------------------------------------------------------

    def test_slice_runs_to_done_and_loads_the_preview(self):
        self.assertEqual(self.sw.job_state, "idle")
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.assertEqual(self.sw.job_state, "validating")
        self.assertTrue(bpy.app.timers.is_registered(self.slicing.poll_job))
        seen = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            more = self.slicing.poll_job()
            seen.append((self.sw.job_state, self.sw.job_progress))
            if more is None:
                break
            time.sleep(0.002)
        states = [s for s, _ in seen]
        self.assertEqual(states[-1], "done", self.sw.job_message)
        progress = [p for _, p in seen]
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(len(self.sw.last_cache_key), 32)
        self.assertFalse(self.sw.stale)
        self.assertEqual(self.sw.mode, "PREVIEW")
        rt = self.runtime.get(self.scene)
        self.assertIsNotNone(rt)
        self.assertEqual(len(rt.layers["z"]), 100)                               # 20 mm at the A1's 0.2 mm
        self.assertGreater(rt.result.stats["time_s"]["normal"], 60)
        self.assertGreater(rt.result.stats["filament_per_extruder"][0]["mm"], 100)
        self.assertEqual(rt.result.stats["layer_count"], 100)
        self.assertFalse([i for i in self.issues() if i["level"] == "error"], self.issues())

    def test_the_real_gcode_is_orca_gcode_for_the_a1(self):
        self.slice()
        rt = self.runtime.get(self.scene)
        with open(rt.result.gcode_path, errors="replace") as f:
            text = f.read()
        self.assertIn("; HEADER_BLOCK_START", text)
        self.assertIn("Bambu Lab A1", text)
        self.assertIn("; total layer number: 100", text)
        self.assertEqual(text.count("\n; layer num/total_layer_count: "), 100)

    def test_every_failure_class_lands_in_the_issues_list(self):
        self.cube.scale = (30.0, 30.0, 1.0)                                      # 600 mm: far off the A1's 256 mm bed
        bpy.context.view_layer.update()
        bpy.ops.slicewright.slice()
        if self.slicing.is_live():
            self.pump(expect=None)
        self.assertEqual(self.sw.job_state, "failed")
        errors = [i for i in self.issues() if i["level"] == "error"]
        self.assertTrue(errors, self.issues())
        self.assertIsNone(self.runtime.get(self.scene))

    # -- the cache ----------------------------------------------------------------------------------------

    def test_a_real_result_is_cached_and_the_second_slice_never_reaches_the_engine(self):
        self.slice()
        key = self.sw.last_cache_key
        self.drain()
        cache = self.slicing.result_cache()
        entry = cache.lookup(key)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.manifest["n_moves"], len(self.runtime.get(self.scene).result.moves["type"]))
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.assertFalse(self.slicing.is_live())                                  # served from disk, no engine job
        self.assertEqual(self.sw.job_state, "done")
        self.drain()
        loaded = self.runtime.get(self.scene)
        self.assertEqual(len(loaded.layers["z"]), 100)
        self.assertTrue(getattr(loaded.result, "from_cache", False))
        self.assertEqual(self.sw.last_cache_key, key)

    def test_the_preview_comes_back_after_reloading_the_file(self):
        self.slice()
        key = self.sw.last_cache_key
        self.drain()
        path = os.path.join(self.tmp, "plate.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        self.assertEqual(self.sw.last_cache_key, key)
        self.drain()
        self.assertEqual((self.sw.job_state, self.sw.job_message), ("done", "Restored from cache"))
        rt = self.runtime.get(self.scene)
        self.assertEqual(len(rt.layers["z"]), 100)
        self.assertFalse(self.sw.stale)

    # -- staleness (the key from the real normalized config) ----------------------------------------------

    def test_staleness_follows_the_real_config(self):
        self.slice()
        self.flush_stale()
        self.assertFalse(self.sw.stale)
        self.sw.process_edits.layer_height = 0.3
        self.flush_stale()
        self.assertTrue(self.sw.stale)
        self.sw.process_edits.layer_height = 0.2
        self.flush_stale()
        self.assertFalse(self.sw.stale)
        self.cube.location.x += 12.0
        bpy.context.view_layer.update()
        self.flush_stale()
        self.assertTrue(self.sw.stale)
        self.slice()
        self.flush_stale()
        self.assertFalse(self.sw.stale)

    # -- multi-object, multi-filament ---------------------------------------------------------------------

    def test_two_objects_on_two_filaments_slice_with_a_wipe_tower(self):
        import numpy as np
        self.add_second_cube()
        self.assertGreaterEqual(len(self.sw.filaments), 2)                       # the A1's default materials
        self.slice()
        rt = self.runtime.get(self.scene)
        result = rt.result
        self.assertEqual(sorted(result.objects), ["Cube", "Cube2"])
        used = {int(f) for f in np.unique(result.moves["filament"]) if f != 255}
        self.assertTrue({0, 1} <= used, used)
        self.assertGreater(result.stats["total_filament_changes"] + result.stats["total_tool_changes"], 0)
        self.assertIsNotNone(result.wipe_tower)
        self.assertEqual(self.sw.mode, "PREVIEW")
        self.assertFalse([i for i in self.issues() if i["level"] == "error"], self.issues())
        self.drain()
        self.assertTrue(self.slicing.result_cache().has(self.sw.last_cache_key))

    # -- cancelling ---------------------------------------------------------------------------------------

    def test_cancel_mid_run_then_slice_again(self):
        self.resize(1.0, 1.0, 5.0)                                               # 100 mm tall: long enough to cancel
        bpy.ops.slicewright.slice()
        time.sleep(0.02)
        self.slicing.poll_job()
        if self.slicing.is_live():
            self.assertEqual(bpy.ops.slicewright.slice_cancel(), {"FINISHED"})
            self.pump(expect=None)
            self.assertIn(self.sw.job_state, ("cancelled", "done"))             # a fast machine may have finished
        self.resize(1.0, 1.0, 1.0)
        self.slice()
        self.assertEqual(len(self.runtime.get(self.scene).layers["z"]), 100)

    def test_a_second_slice_cancels_the_first(self):
        self.resize(1.0, 1.0, 5.0)
        bpy.ops.slicewright.slice()
        time.sleep(0.02)
        self.resize(0.5, 0.5, 0.5)
        bpy.ops.slicewright.slice()                                              # no Busy: the runner waits for the lock
        self.pump()
        self.assertEqual(len(self.runtime.get(self.scene).layers["z"]), 50)      # 10 mm: the second plate

    # -- thumbnails and the Issues list -------------------------------------------------------------------

    def test_background_mode_skips_the_thumbnails_with_an_info_issue(self):
        self.slice()
        self.assertTrue(bpy.app.background)
        self.assertIn(("info", "thumbnails_skipped"), [(i["level"], i["code"]) for i in self.issues()])

    def test_unknown_preset_keys_are_warnings_the_user_can_read(self):
        self.slice()
        unknown = [i for i in self.issues() if i["code"] == "unknown_key"]
        for issue in unknown:
            self.assertEqual(issue["level"], "warning")
            self.assertTrue(issue["opt_key"] and issue["message"])


if __name__ == "__main__":
    bl_common.run("__main__")
