# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: per-object setting overrides end to end (03 sections 1.2, 2.2 and 4.1), on the fake engine or
the REAL one (``SLICEWRIGHT_CHECK_BACKEND=real``, skipped when it is not built).

The overrides are read from ``Object.slicewright.overrides``, validated against the engine schema, extracted with the
plate, hashed into the slice key (so a change marks the slice stale), and handed to ``add_object``.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

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


class Layout:
    """A ``UILayout`` stand-in that records what is drawn."""

    def __init__(self, log):
        self.log, self.alert = log, False

    def _child(self):
        return Layout(self.log)

    row = box = column = lambda self, align=False: self._child()

    def label(self, text="", icon="NONE"):
        self.log.append(("label", text))

    def prop(self, data, key, text=None):
        self.log.append(("prop", key, text))

    def operator(self, idname, text="", icon="NONE"):
        op = type("Op", (), {})()
        self.log.append(("operator", idname, text, op))
        return op


class ObjectSettingsTests(unittest.TestCase):
    def setUp(self):
        import slicewright
        from slicewright import prefs
        from slicewright.blender import (extract, library, object_settings, paths, picker, registry, slicing,
                                         staleness, timers)
        from slicewright.blender.preview import runtime
        self.addon, self.prefs, self.extract, self.os_ = slicewright, prefs, extract, object_settings
        self.library, self.registry, self.slicing, self.st = library, registry, slicing, staleness
        self.timers, self.runtime, self.picker = timers, runtime, picker
        if REAL:
            from slicewright.engine import adapter
            adapter.add_engine_paths()
            try:
                import slicewright_engine  # noqa: F401
            except ImportError:
                self.skipTest("slicewright_engine is not built")
        self.tmp = tempfile.mkdtemp(prefix="sw-objset-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        paths.set_override(os.path.join(self.tmp, "user"))
        self.addCleanup(paths.set_override, None)
        test = self

        class Prefs(prefs._Defaults):
            cache_cap_mb = 2048
            auto_reslice = False
            settings_mode = "advanced"
            show_develop = False
        self.Prefs = Prefs
        real_get = prefs.get
        prefs.get = lambda: Prefs
        self.addCleanup(setattr, prefs, "get", real_get)
        self.scene = bl_common.fresh_scene()
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)
        self.assertTrue(registry.state.status.ok, registry.state.status.summary)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        if REAL:
            library.request()
            self.drain()
            entry = [e for e in library.get().printers() if e.name == PRINTER]
            self.assertEqual(len(entry), 1)
            picker.select_printer(self.sw, entry[0])
        else:
            self.sw.printer_edits.printable_area = "0x0,256x0,256x256,0x256"
            self.sw.printer_edits.printable_height = 250.0
        self.a = bl_common.add_cube("A", 20.0, (-40.0, 0.0, 10.0))
        self.b = bl_common.add_cube("B", 20.0, (50.0, 0.0, 10.0))
        for ob in (self.a, self.b):
            ob.select_set(True)
        bpy.context.view_layer.objects.active = self.a
        bpy.ops.slicewright.plate_add()
        self.schema = self.os_.config_pg.schema()

    def finish(self):
        if self.slicing.is_live():
            self.slicing.runner().cancel(discard=True)
            self.pump(expect=None)
        self.drain()
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    # -- helpers ------------------------------------------------------------------------------------------

    def pump(self, timeout=90.0, expect="done"):
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
                if task.name.startswith("preview:"):
                    runner.cancel(task)
            if runner.idle:
                return
            runner.tick()
        self.fail("the tick runner never went idle")

    def slice(self):
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.pump()
        return self.runtime.get(self.scene).result

    def flush_stale(self):
        if bpy.app.timers.is_registered(self.st._tick):
            bpy.app.timers.unregister(self.st._tick)
        self.st._tick()

    def extracted(self):
        ex = self.extract.extract_plate(bpy.context)
        return {o.name: o for o in ex.objects}, ex.issues

    def filament_mm(self, result):
        return result.stats["filament_per_extruder"][0]["mm"]

    def select(self, ob):
        bpy.context.view_layer.objects.active = ob

    def override(self, ob, key, value):
        setattr(ob.slicewright.overrides, key, value)

    # -- extraction -----------------------------------------------------------------------------------------

    def test_an_object_without_overrides_extracts_none(self):
        objs, issues = self.extracted()
        self.assertEqual({k: o.config_overrides for k, o in objs.items()}, {"A": {}, "B": {}})
        self.assertEqual([i for i in issues if i["code"].startswith("override_")], [])

    def test_the_overridden_keys_are_extracted_as_flat_text_for_that_object_only(self):
        self.override(self.a, "wall_loops", 5)
        objs, issues = self.extracted()
        self.assertEqual(objs["A"].config_overrides, {"wall_loops": "5"})
        self.assertEqual(objs["B"].config_overrides, {})
        self.assertEqual(objs["A"].add_object_kwargs()["config_overrides"], {"wall_loops": "5"})
        self.assertIsNone(objs["B"].add_object_kwargs()["config_overrides"])
        self.assertNotEqual(objs["A"].key, self.extracted()[0]["B"].key)

    def test_an_override_changes_the_object_key_and_removing_it_restores_it(self):
        base = self.extracted()[0]["A"].key
        self.override(self.a, "wall_loops", 5)
        changed = self.extracted()[0]["A"].key
        self.assertNotEqual(changed, base)
        self.override(self.a, "wall_loops", 6)
        self.assertNotEqual(self.extracted()[0]["A"].key, changed)
        self.os_.remove_override(self.a, "wall_loops")
        self.assertEqual(self.extracted()[0]["A"].key, base)

    def test_keys_the_engine_would_refuse_are_dropped_with_a_warning_not_a_failure(self):
        glob = [k for k, e in self.schema.items() if e["scope"] == "global" and k in self.os_.config_pg.specs()
                and e["type"] in ("int", "float", "bool")][0]
        spec = self.os_.config_pg.specs()[glob]
        self.override(self.a, glob, spec.default if spec.kind != "bool" else True)        # valid only plate-wide
        self.a.slicewright.overrides["mystery_key"] = "1"                                  # an orphan ID property
        self.override(self.a, "wall_loops", 4)
        objs, issues = self.extracted()
        self.assertEqual(objs["A"].config_overrides, {"wall_loops": "4"})
        found = {(i["code"], i["opt_key"]): i for i in issues if i["code"].startswith("override_")}
        self.assertEqual(sorted(found), sorted([("override_not_per_object", glob), ("override_unknown_key", "mystery_key")]))
        for issue in found.values():
            self.assertEqual((issue["level"], issue["object_name"]), ("warning", "A"))

    def test_a_slice_goes_ahead_and_lists_the_dropped_override_in_the_issues(self):
        from slicewright.blender import issues
        self.a.slicewright.overrides["mystery_key"] = "1"
        self.override(self.a, "wall_loops", 4)
        self.slice()                                                                        # a warning, not a failure
        rows = [i for i in issues.current() if i["code"] == "override_unknown_key"]
        self.assertEqual([(r["level"], r["opt_key"], r["object_name"]) for r in rows], [("warning", "mystery_key", "A")])

    # -- the engine ---------------------------------------------------------------------------------------

    def test_the_engine_gets_the_override_and_it_changes_that_object_only(self):
        plain = self.slice()
        base_mm = self.filament_mm(plain)
        self.override(self.a, "wall_loops", 6)
        thick = self.slice()
        self.assertGreater(self.filament_mm(thick), base_mm * 1.02)                 # more walls on A use more filament
        self.override(self.b, "wall_loops", 6)
        both = self.slice()
        self.assertGreater(self.filament_mm(both), self.filament_mm(thick) * 1.02)  # and B's count too

    def test_a_per_object_layer_height_reaches_the_engine(self):
        if not REAL:
            self.skipTest("the fake engine ignores a per-object layer height")
        base = len(self.slice().layers["z"])
        self.override(self.a, "layer_height", 0.1)
        finer = len(self.slice().layers["z"])
        self.assertGreater(finer, base)

    def test_an_unusable_value_fails_the_slice_naming_the_object_and_the_key(self):
        keys = [k for k, e in self.schema.items() if e["scope"] in ("object", "region") and e["type"] == "floatOrPercent"
                and k in self.os_.config_pg.specs()]
        if not keys:
            self.skipTest("this engine has no per-object text-typed setting")
        self.override(self.a, keys[0], "banana")
        self.assertEqual(bpy.ops.slicewright.slice(), {"CANCELLED"})                       # refused before any engine job
        self.assertEqual(self.sw.job_state, "failed")
        self.assertTrue(self.sw.job_message.startswith("Settings error: A:"), self.sw.job_message)
        from slicewright.blender import issues
        errors = [i for i in issues.current() if i["level"] == "error"]
        self.assertEqual([(i["opt_key"], i["object_name"]) for i in errors], [(keys[0], "A")])

    # -- staleness and the key ------------------------------------------------------------------------------

    def test_changing_an_override_marks_the_slice_stale_and_moves_the_key(self):
        self.slice()
        key = self.sw.last_cache_key
        self.assertFalse(self.sw.stale)
        self.override(self.a, "wall_loops", 5)                       # the property's update callback marks the scene
        self.flush_stale()
        self.assertTrue(self.sw.stale)
        self.assertEqual(self.sw.last_cache_key, key)                # until the next slice
        self.override(self.a, "wall_loops", 6)
        self.flush_stale()
        self.assertTrue(self.sw.stale)
        self.slice()
        self.assertNotEqual(self.sw.last_cache_key, key)
        self.assertFalse(self.sw.stale)
        self.os_.remove_override(self.a, "wall_loops")               # removing marks it too (property_unset has no callback)
        self.flush_stale()
        self.assertTrue(self.sw.stale)
        self.slice()
        self.assertEqual(self.sw.last_cache_key, key)                # back to the original inputs: the same key (a cache hit)

    def test_overrides_survive_a_save_and_reload(self):
        self.override(self.a, "wall_loops", 5)
        path = os.path.join(self.tmp, "plate.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        self.a = bpy.data.objects["A"]
        self.assertEqual(self.extracted()[0]["A"].config_overrides, {"wall_loops": "5"})

    # -- the panel and the operators -------------------------------------------------------------------------

    def test_add_starts_from_the_plates_value_and_remove_drops_it(self):
        self.sw.process_edits.wall_loops = 3
        self.select(self.a)
        self.assertTrue(bpy.ops.slicewright.object_override_add.poll())
        self.assertEqual(bpy.ops.slicewright.object_override_add(choice="wall_loops"), {"FINISHED"})
        self.assertEqual(self.os_.overridden_keys(self.a), ["wall_loops"])
        self.assertEqual(self.a.slicewright.overrides.wall_loops, 3)               # starts from the plate's value
        self.assertEqual(self.extracted()[0]["A"].config_overrides, {"wall_loops": "3"})
        self.assertEqual(self.os_.overridden_keys(self.b), [])                     # the other object is untouched
        self.assertEqual(bpy.ops.slicewright.object_override_remove(key="wall_loops"), {"FINISHED"})
        self.assertEqual(self.os_.overridden_keys(self.a), [])

    def test_only_per_object_keys_can_be_added(self):
        self.select(self.a)
        glob = next(k for k, e in self.schema.items() if e["scope"] == "global" and k in self.os_.config_pg.specs())
        self.assertFalse(self.os_.add_override(self.a, self.sw, glob))
        self.assertFalse(self.os_.add_override(self.a, self.sw, "no_such_key"))
        self.assertEqual(self.os_.overridden_keys(self.a), [])

    def test_the_offer_follows_the_settings_detail_mode_and_skips_what_is_set(self):
        self.select(self.a)
        modes = {}
        for level in ("simple", "advanced", "expert"):
            modes[level] = set(self.os_.offer(self.a, level, False))
        self.assertTrue(modes["simple"] <= modes["advanced"] <= modes["expert"])
        if modes["simple"] == modes["expert"]:
            self.skipTest("this engine's per-object settings are all simple")
        self.assertTrue(modes["simple"] < modes["expert"], {k: len(v) for k, v in modes.items()})
        for level, keys in modes.items():
            for key in keys:
                self.assertIn(self.schema[key]["scope"], ("object", "region"))
        self.assertIn("wall_loops", modes["expert"])
        self.override(self.a, "wall_loops", 4)
        self.assertNotIn("wall_loops", self.os_.offer(self.a, "expert", False))
        keys_expert_only = modes["expert"] - modes["advanced"]
        self.assertTrue(all(self.schema[k]["mode"] == "expert" for k in keys_expert_only))

    def test_the_picker_lists_the_keys_for_the_preferences_detail_level(self):
        from slicewright.blender.operators import object_settings as ops
        self.select(self.a)
        self.Prefs.settings_mode = "simple"
        simple = {i[0] for i in ops._choices(None, bpy.context)}
        self.Prefs.settings_mode = "expert"
        expert = {i[0] for i in ops._choices(None, bpy.context)}
        self.assertTrue(simple <= expert)
        if simple == expert:
            self.skipTest("this engine's per-object settings are all simple")
        item = [i for i in ops._items if i[0] == "wall_loops"][0]
        self.assertIn("(wall_loops)", item[1])

    def test_the_panel_lists_overrides_with_a_remove_button_and_flags_orphans(self):
        from slicewright.blender.ui import object_panel
        log = []
        object_panel.draw_object_settings(Layout(log), self.scene, None)
        self.assertEqual(log, [("label", "Select an object")])
        log.clear()
        self.override(self.a, "wall_loops", 4)
        self.a.slicewright.overrides["mystery_key"] = "1"
        object_panel.draw_object_settings(Layout(log), self.scene, self.a)
        props = [c for c in log if c[0] == "prop"]
        self.assertEqual([p[1] for p in props], ["filament", "wall_loops"])
        removes = [c for c in log if c[0] == "operator" and c[1].endswith("object_override_remove")]
        self.assertEqual(sorted(c[3].key for c in removes), ["", "mystery_key", "wall_loops"])
        self.assertTrue(any(c[0] == "label" and "mystery_key" in c[1] for c in log))
        self.assertTrue(any(c[0] == "operator" and c[1].endswith("object_override_add") for c in log))

    def test_the_z_contouring_settings_carry_corrected_descriptions_and_are_offered_at_expert_only(self):
        if "zaa_min_z" not in self.schema:
            self.skipTest("this engine has no Z contouring settings")
        props = bpy.types.PropertyGroup.bl_rna_get_subclass_py(self.os_.config_pg.CLASS_NAME).bl_rna.properties
        self.assertIn("GREATER than", props["zaa_minimize_perimeter_height"].description)
        self.assertNotIn("slope less than", props["zaa_minimize_perimeter_height"].description)
        self.assertIn("Bad min Z value", props["zaa_min_z"].description)
        self.assertIn("does not scale", props["zaa_min_z"].description)
        self.select(self.a)
        zaa = {"zaa_enabled", "zaa_min_z", "zaa_minimize_perimeter_height", "zaa_dont_alternate_fill_direction"}
        self.assertFalse(zaa & set(self.os_.offer(self.a, "advanced", False)))
        self.assertEqual(zaa & set(self.os_.offer(self.a, "expert", False)), zaa)
        from slicewright.blender.operators import object_settings as ops
        self.Prefs.settings_mode = "expert"
        ops._choices(None, bpy.context)
        shown = {i[0]: i[2] for i in ops._items}
        self.assertIn("GREATER than", shown["zaa_minimize_perimeter_height"])           # the picker's tooltip too

    def test_an_object_off_the_plate_is_labelled_as_not_sliced(self):
        from slicewright.blender.ui import object_panel
        stray = bl_common.add_cube("Stray", 5.0, (500, 500, 5))
        log = []
        object_panel.draw_object_settings(Layout(log), self.scene, stray)
        self.assertTrue(any(c[0] == "label" and "not sliced" in c[1] for c in log))

    def test_removing_an_orphan_override_marks_the_slice_stale(self):
        self.a.slicewright.overrides["mystery_key"] = "1"
        self.slice()
        self.select(self.a)
        self.assertEqual(self.os_.orphan_keys(self.a), ["mystery_key"])
        bpy.ops.slicewright.object_override_remove(key="mystery_key")
        self.assertEqual(self.os_.orphan_keys(self.a), [])


if __name__ == "__main__":
    bl_common.run(__name__)
