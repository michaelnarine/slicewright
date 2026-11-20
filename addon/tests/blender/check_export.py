# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: exporting the last slice (03 section 8.1), on the fake engine or the REAL one.

``SLICEWRIGHT_CHECK_BACKEND=real`` selects the built ``slicewright_engine`` and the Bambu Lab A1 (skipped when it is not
built); the default is the fake. The same tests run on both: the export code only sees a result object.

Timers never fire in ``-b`` (03 section 7.9a): the tests pump ``slicing.poll_job`` (and ``exporting.poll_export``) and
drive the tick runner (cache writes and loads) with ``drain``. Background Blender has no window, so ``INVOKE_DEFAULT`` of
an operator that opens a dialog runs it at once with the dialog's defaults: the popup chains are walked that way.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402

BACKEND = os.environ.get("SLICEWRIGHT_CHECK_BACKEND", "fake")
REAL = BACKEND == "real"
BUILD = os.path.join(bl_common.TESTS_DIR, os.pardir, os.pardir, "engine", "build", "engine", "python")
PRINTER = "Bambu Lab A1 0.4 nozzle"
if REAL:
    if not os.environ.get("SLICEWRIGHT_ENGINE_PATH") and os.path.isdir(os.path.join(BUILD, "slicewright_engine")):
        os.environ["SLICEWRIGHT_ENGINE_PATH"] = os.path.abspath(BUILD)
    os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "slicewright_engine"
else:
    os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"


def read(path):
    with open(path, "rb") as f:
        return f.read()


class ExportTests(unittest.TestCase):
    def setUp(self):
        import slicewright
        from slicewright import prefs
        from slicewright.blender import exporting, library, paths, picker, registry, slicing, staleness, timers
        from slicewright.blender.preview import runtime
        self.addon, self.prefs, self.exporting, self.library = slicewright, prefs, exporting, library
        self.registry, self.slicing, self.st, self.timers, self.runtime, self.picker = (
            registry, slicing, staleness, timers, runtime, picker)
        if REAL:
            from slicewright.engine import adapter
            adapter.add_engine_paths()
            try:
                import slicewright_engine  # noqa: F401
            except ImportError:
                self.skipTest("slicewright_engine is not built")
        self.tmp = tempfile.mkdtemp(prefix="sw-export-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        paths.set_override(os.path.join(self.tmp, "user"))
        self.addCleanup(paths.set_override, None)

        class Prefs(prefs._Defaults):
            cache_cap_mb = 2048
            auto_reslice = False
            last_export_dir = ""
        self.Prefs = Prefs
        real_get = prefs.get
        prefs.get = lambda: Prefs
        self.addCleanup(setattr, prefs, "get", real_get)
        self.scene = bl_common.fresh_scene()
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)
        self.assertTrue(self.registry.state.status.ok, self.registry.state.status.summary)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        if REAL:
            self.library.request()
            self.drain()
            entry = [e for e in self.library.get().printers() if e.name == PRINTER]
            self.assertEqual(len(entry), 1)
            self.picker.select_printer(self.sw, entry[0])
        else:
            self.sw.printer_edits.printable_area = "0x0,256x0,256x256,0x256"
            self.sw.printer_edits.printable_height = 250.0
        self.cube = bl_common.add_cube("Cube", 20.0, (100.0, 100.0, 10.0))
        self.cube.select_set(True)
        bpy.ops.slicewright.plate_add()
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(self.out)

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
            time.sleep(0.002)
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

    def slice(self):
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.pump()

    def result(self):
        return self.runtime.get(self.scene).result

    def flush_stale(self):
        if bpy.app.timers.is_registered(self.st._tick):
            bpy.app.timers.unregister(self.st._tick)
        self.st._tick()

    def save_as(self, name):
        path = os.path.join(self.tmp, name)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return path

    def dest(self, name="plate.gcode"):
        return os.path.join(self.out, name)

    def export(self, name="plate.gcode", **kw):
        dest = self.dest(name)
        self.assertEqual(bpy.ops.slicewright.export_gcode(filepath=dest, **kw), {"FINISHED"})
        return dest

    def edit_plate(self):
        self.cube.location.x += 15.0
        bpy.context.view_layer.update()
        self.flush_stale()
        self.assertTrue(self.sw.stale)

    # -- nothing to export ---------------------------------------------------------------------------------

    def test_export_is_disabled_until_there_is_a_slice(self):
        from slicewright.blender.ui import export_panel
        self.assertFalse(bpy.ops.slicewright.export_gcode.poll())
        self.assertEqual(self.exporting.availability(self.scene, "gcode"), (False, "Slice the plate to export it"))
        with self.assertRaises(RuntimeError):
            bpy.ops.slicewright.export_gcode(filepath=self.dest())
        self.assertEqual(os.listdir(self.out), [])
        log = []
        export_panel.draw_export(Layout(log), self.scene)
        self.assertIn(("label", "Slice the plate to export it", "INFO"), log)
        self.assertEqual([e for e in log if e[0] == "operator"], [("operator", "slicewright.export_gcode", "", "EXPORT")])

    def test_export_is_disabled_while_a_slice_runs_and_enabled_when_it_ends(self):
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        if self.slicing.is_live():                                          # a very fast engine may be done already
            self.assertFalse(bpy.ops.slicewright.export_gcode.poll())
            self.assertEqual(self.exporting.availability(self.scene, "gcode")[1], "Wait for the slice to finish")
        self.pump()
        self.assertTrue(bpy.ops.slicewright.export_gcode.poll())

    # -- the G-code ------------------------------------------------------------------------------------------

    def test_the_exported_gcode_is_the_engines_gcode_byte_for_byte(self):
        self.slice()
        dest = self.export()
        self.assertEqual(read(dest), read(self.result().gcode_path))
        self.assertGreater(os.path.getsize(dest), 1000)
        self.assertEqual(os.listdir(self.out), ["plate.gcode"])                # no temp file left behind
        level, text = self.exporting.message(self.scene)
        self.assertEqual(level, "INFO")
        self.assertTrue(text.startswith("Exported plate.gcode"), text)

    def test_the_default_name_comes_from_the_engine_and_the_blend_folder(self):
        self.slice()
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(proj, "Benchy.blend"))
        path = self.exporting.default_filepath(self.scene, "gcode")
        self.assertEqual(os.path.dirname(path), proj)
        self.assertEqual(os.path.basename(path), self.result().output_filename("Benchy"))
        self.assertTrue(path.endswith(".gcode") and os.path.basename(path).startswith("Benchy"))

    def test_a_dot_in_the_blend_name_is_part_of_the_name_not_an_extension(self):
        self.slice()
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(self.tmp, "my.part.v2.blend"))
        name = os.path.basename(self.exporting.default_filepath(self.scene, "gcode"))
        self.assertTrue(name.startswith("my.part.v2"), name)
        self.assertEqual(name, self.result().output_filename("my.part.v2"))
        self.assertTrue(name.endswith(".gcode") and not name.endswith(".gcode.gcode"))

    def test_an_unsaved_file_defaults_to_untitled_in_the_last_export_folder(self):
        self.slice()
        self.assertEqual(bpy.data.filepath, "")
        self.Prefs.last_export_dir = self.out
        path = self.exporting.default_filepath(self.scene, "gcode")
        self.assertEqual(os.path.dirname(path), self.out)
        self.assertTrue(os.path.basename(path).startswith("untitled"), path)

    def test_exporting_remembers_the_folder_in_the_preferences(self):
        self.slice()
        self.export()
        self.assertEqual(self.prefs.get().last_export_dir, self.out)

    def test_the_default_name_survives_a_broken_filename_format(self):
        self.slice()
        source = self.exporting.source_for(self.scene)

        class Broken:
            def __getattr__(self, name):
                return getattr(source.result, name)

            def output_filename(self, base):
                raise ValueError("bad format")
        source.result = Broken()
        self.assertEqual(self.exporting.engine_name(source), "untitled.gcode")

    # -- refusing to overwrite ----------------------------------------------------------------------------------

    def test_an_existing_file_is_not_overwritten_without_confirmation(self):
        self.slice()
        dest = self.dest()
        with open(dest, "wb") as f:
            f.write(b"precious")
        self.assertEqual(bpy.ops.slicewright.export_gcode(filepath=dest), {"CANCELLED"})
        self.assertEqual(read(dest), b"precious")
        self.assertEqual(os.listdir(self.out), ["plate.gcode"])
        level, text = self.exporting.message(self.scene)
        self.assertEqual(level, "ERROR")
        self.assertIn("already exists", text)
        self.assertEqual(bpy.ops.slicewright.export_gcode(filepath=dest, overwrite=True), {"FINISHED"})
        self.assertEqual(read(dest), read(self.result().gcode_path))

    def test_the_dialogs_execute_asks_before_replacing_and_the_answer_decides(self):
        """What ``invoke`` marks ``interactive`` runs the confirm popup when the file exists. Background Blender answers
        every popup "yes" (and runs ``execute`` for ``INVOKE_DEFAULT``), so this walks the whole chain to the replace."""
        self.slice()
        dest = self.dest()
        with open(dest, "wb") as f:
            f.write(b"precious")
        self.assertEqual(bpy.ops.slicewright.export_gcode("INVOKE_DEFAULT", filepath=dest, interactive=True),
                         {"CANCELLED"})                        # the dialog's own run ends; the popup's run writes
        self.assertEqual(read(dest), read(self.result().gcode_path))
        self.assertEqual(os.listdir(self.out), ["plate.gcode"])

    def test_the_overwrite_popup_replaces_the_file_when_accepted(self):
        self.slice()
        dest = self.dest()
        with open(dest, "wb") as f:
            f.write(b"precious")
        self.assertEqual(bpy.ops.slicewright.export_confirm(target="export_gcode", reason="OVERWRITE",
                                                            filepath=dest), {"FINISHED"})
        self.assertEqual(read(dest), read(self.result().gcode_path))
        self.assertEqual(os.listdir(self.out), ["plate.gcode"])

    # -- failures --------------------------------------------------------------------------------------------

    def test_a_missing_folder_is_reported_and_nothing_is_written(self):
        self.slice()
        bad = os.path.join(self.tmp, "no", "such", "dir", "plate.gcode")
        self.assertEqual(bpy.ops.slicewright.export_gcode(filepath=bad), {"CANCELLED"})
        level, text = self.exporting.message(self.scene)
        self.assertEqual(level, "ERROR")
        self.assertIn("does not exist", text)
        self.assertFalse(os.path.exists(os.path.dirname(bad)))

    def test_a_failing_write_leaves_neither_a_file_nor_a_temp_nor_damage(self):
        self.slice()
        dest = self.dest()
        with open(dest, "wb") as f:
            f.write(b"old contents")
        source = self.exporting.source_for(self.scene)
        real = self.result()

        def boom(path):
            with open(path, "wb") as f:
                f.write(b"half a file")
            raise OSError("disk full")
        wrapper = type("R", (), {"write_gcode": staticmethod(boom), "__getattr__": lambda s, n: getattr(real, n)})()
        original = self.exporting.source_for
        self.exporting.source_for = lambda scene: self.exporting.Source("live", wrapper, source.key)
        self.addCleanup(setattr, self.exporting, "source_for", original)
        self.assertEqual(bpy.ops.slicewright.export_gcode(filepath=dest, overwrite=True), {"CANCELLED"})
        self.assertEqual(read(dest), b"old contents")
        self.assertEqual(os.listdir(self.out), ["plate.gcode"])
        level, text = self.exporting.message(self.scene)
        self.assertEqual(level, "ERROR")
        self.assertIn("disk full", text)

    def test_the_message_is_dropped_when_a_file_is_loaded(self):
        self.slice()
        self.export()
        self.assertIsNotNone(self.exporting.message(self.scene))
        path = self.save_as("a.blend")
        bpy.ops.wm.open_mainfile(filepath=path)
        self.assertIsNone(self.exporting.message(bpy.context.scene))

    # -- the source of the G-code ---------------------------------------------------------------------------------

    def test_after_a_cache_hit_the_cached_gcode_is_exported(self):
        self.slice()
        first = read(self.result().gcode_path)
        key = self.sw.last_cache_key
        self.drain()                                                          # the cache write lands
        self.assertTrue(self.slicing.result_cache().has(key))
        self.slice()                                                          # a hit: no engine job
        self.drain()
        self.assertTrue(getattr(self.result(), "from_cache", False))
        self.assertEqual(self.exporting.source_for(self.scene).kind, "cached")
        dest = self.export()
        self.assertEqual(read(dest), first)                                   # the same G-code as the live slice gave

    def test_the_default_name_after_a_cache_hit_equals_the_live_name(self):
        self.slice()
        live = os.path.basename(self.exporting.default_filepath(self.scene, "gcode"))
        self.drain()
        self.slice()
        self.drain()
        self.assertEqual(self.exporting.source_for(self.scene).kind, "cached")
        self.assertEqual(os.path.basename(self.exporting.default_filepath(self.scene, "gcode")), live)

    def test_after_reloading_the_file_the_cached_gcode_is_exported(self):
        self.slice()
        first = read(self.result().gcode_path)
        live_name = os.path.basename(self.exporting.default_filepath(self.scene, "gcode"))
        self.drain()
        path = self.save_as("plate.blend")
        bpy.ops.wm.open_mainfile(filepath=path)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        self.drain()
        self.assertEqual((self.sw.job_state, self.sw.job_message), ("done", "Restored from cache"))
        self.assertEqual(self.exporting.source_for(self.scene).kind, "cached")
        dest = self.export()
        self.assertEqual(read(dest), first)
        self.assertEqual(os.path.basename(self.exporting.default_filepath(self.scene, "gcode"))
                         .replace("plate", "untitled"), live_name)

    def test_the_cache_entry_is_exported_when_the_preview_is_not_loaded(self):
        self.slice()
        first = read(self.result().gcode_path)
        self.drain()
        self.runtime.put(self.scene, None)                                    # no preview (e.g. it failed to load)
        source = self.exporting.source_for(self.scene)
        self.assertEqual(source.kind, "entry")
        self.assertEqual(read(self.export()), first)

    def test_nothing_is_offered_while_the_cached_preview_is_still_loading(self):
        self.slice()
        self.drain()
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})          # a hit: the preview loads on the ticks
        self.assertFalse(self.slicing.is_live())
        if any(t.name.startswith("cache-load:") for t in self.timers.runner.pending()):
            self.assertEqual(self.exporting.availability(self.scene, "gcode"), (False, "Loading the cached slice…"))
            self.assertFalse(bpy.ops.slicewright.export_gcode.poll())
        self.drain()
        self.assertTrue(bpy.ops.slicewright.export_gcode.poll())

    def test_a_missing_cache_entry_after_a_reload_means_nothing_to_export(self):
        self.slice()
        self.drain()
        path = self.save_as("plate.blend")
        self.slicing.result_cache().clear()
        bpy.ops.wm.open_mainfile(filepath=path)
        self.drain()
        scene = bpy.context.scene
        self.assertIsNone(self.exporting.source_for(scene))
        self.assertFalse(bpy.ops.slicewright.export_gcode.poll())

    # -- an out-of-date preview ----------------------------------------------------------------------------------

    def test_an_out_of_date_preview_is_not_exported_without_confirmation(self):
        self.slice()
        old = read(self.result().gcode_path)
        self.edit_plate()
        self.assertEqual(bpy.ops.slicewright.export_gcode(filepath=self.dest()), {"CANCELLED"})
        self.assertEqual(os.listdir(self.out), [])
        dest = self.export(allow_stale=True)
        self.assertEqual(read(dest), old)                                     # the OLD slice, as the popup says

    def test_an_edit_the_throttle_has_not_checked_yet_still_counts_as_stale(self):
        self.slice()
        self.cube.location.x += 15.0
        bpy.context.view_layer.update()                                      # marks the scene dirty; the timer has not run
        self.assertEqual(bpy.ops.slicewright.export_gcode(filepath=self.dest()), {"CANCELLED"})
        self.assertTrue(self.sw.stale)

    def test_the_stale_popup_hands_on_to_the_dialog_with_allow_stale_set(self):
        self.slice()
        old = read(self.result().gcode_path)
        self.edit_plate()
        self.Prefs.last_export_dir = self.out
        # "Export anyway" in the popup runs the dialog again with allow_stale; background Blender runs it with its defaults
        self.assertEqual(bpy.ops.slicewright.export_confirm(target="export_gcode", reason="STALE"),
                         {"FINISHED"})
        written = [n for n in os.listdir(self.out) if n.endswith(".gcode")]
        self.assertEqual(len(written), 1, os.listdir(self.out))
        self.assertEqual(read(os.path.join(self.out, written[0])), old)

    def test_the_confirm_operator_ignores_an_unknown_target(self):
        self.assertEqual(bpy.ops.slicewright.export_confirm(target="nope", reason="STALE"), {"CANCELLED"})

    def test_panel_says_when_the_preview_is_out_of_date_and_what_the_last_export_did(self):
        from slicewright.blender.ui import export_panel
        self.slice()
        self.export()
        self.edit_plate()
        log = []
        export_panel.draw_export(Layout(log), self.scene)
        labels = [e for e in log if e[0] == "label"]
        self.assertIn(("label", export_panel.STALE_HINT, "INFO"), labels)
        self.assertTrue(any(e[1].startswith("Exported plate.gcode") and e[2] == "CHECKMARK" for e in labels), labels)
        self.exporting.set_message(self.scene, "ERROR", "G-code not exported: boom")
        log.clear()
        export_panel.draw_export(Layout(log), self.scene)
        self.assertIn(("label", "G-code not exported: boom", "ERROR"), log)


class Layout:
    """A recording stand-in for ``UILayout``."""

    def __init__(self, log):
        self.log, self.alert = log, False

    def column(self, align=False):
        return self

    def row(self, align=False):
        return self

    def label(self, text="", icon="NONE"):
        self.log.append(("label", text, icon))

    def operator(self, idname, text="", icon="NONE", emboss=True):
        self.log.append(("operator", idname, text, icon))
        return type("Op", (), {})()


if __name__ == "__main__":
    bl_common.run("__main__")
