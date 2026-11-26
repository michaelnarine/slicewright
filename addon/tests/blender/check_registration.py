# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: register/unregister hygiene (plan M1: "leaves no handlers or timers")."""
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402  (also puts addon/ and addon/tests/ on sys.path)

import bpy  # noqa: E402

ENV = "SLICEWRIGHT_ENGINE_MODULE"
CLASS_NAMES = ("SLICEWRIGHT_PT_diagnostics", "SLICEWRIGHT_OT_copy_diagnostics",
               "SLICEWRIGHT_AP_Preferences", "SLICEWRIGHT_PG_Scene", "SLICEWRIGHT_PG_preview", "SLICEWRIGHT_PG_FilamentSlot", "SLICEWRIGHT_PG_Object", "SLICEWRIGHT_PG_Config",
               "SLICEWRIGHT_PT_printer", "SLICEWRIGHT_PT_settings", "SLICEWRIGHT_OT_import_presets", "SLICEWRIGHT_PG_ImportItem", "SLICEWRIGHT_OT_preset_save", "SLICEWRIGHT_OT_preset_revert",
               "SLICEWRIGHT_OT_preset_diff", "SLICEWRIGHT_OT_preset_manage", "SLICEWRIGHT_OT_preset_restore_embedded", "SLICEWRIGHT_OT_settings_override", "SLICEWRIGHT_OT_load_library",
               "SLICEWRIGHT_OT_filament_add", "SLICEWRIGHT_OT_filament_remove", "SLICEWRIGHT_OT_pick_preset",
               "SLICEWRIGHT_PT_plate", "SLICEWRIGHT_OT_use_mm_scene", "SLICEWRIGHT_OT_drop_to_bed",
               "SLICEWRIGHT_PT_paint", "SLICEWRIGHT_OT_paint_assign",
               "SLICEWRIGHT_PT_slice", "SLICEWRIGHT_OT_slice", "SLICEWRIGHT_OT_slice_cancel",
               "SLICEWRIGHT_PG_Issue", "SLICEWRIGHT_PG_IgnoredKey", "SLICEWRIGHT_UL_ignored_keys", "SLICEWRIGHT_PT_issues", "SLICEWRIGHT_OT_issue_goto", "SLICEWRIGHT_OT_issue_details", "SLICEWRIGHT_OT_show_online_prefs",
               "SLICEWRIGHT_OT_filament_add_defaults",
               "SLICEWRIGHT_OT_workspace_add", "SLICEWRIGHT_OT_workspace_remove", "SLICEWRIGHT_PT_workspace", "SLICEWRIGHT_PT_settings_wide",
               "SLICEWRIGHT_PT_export", "SLICEWRIGHT_OT_export_gcode", "SLICEWRIGHT_OT_export_gcode_3mf",
               "SLICEWRIGHT_OT_export_cancel", "SLICEWRIGHT_OT_export_confirm",
               "SLICEWRIGHT_PG_Printer", "SLICEWRIGHT_OT_printer_add", "SLICEWRIGHT_OT_printer_remove",
               "SLICEWRIGHT_OT_printer_secret", "SLICEWRIGHT_OT_printer_secret_clear",
               "SLICEWRIGHT_OT_printer_trust_cert", "SLICEWRIGHT_OT_printer_forget_pin", "SLICEWRIGHT_OT_printer_test",
               "SLICEWRIGHT_PG_Send", "SLICEWRIGHT_PT_send", "SLICEWRIGHT_OT_send", "SLICEWRIGHT_OT_send_cancel",
               "SLICEWRIGHT_OT_send_confirm",
               "SLICEWRIGHT_PG_Bake", "SLICEWRIGHT_PT_bake", "SLICEWRIGHT_OT_bake", "SLICEWRIGHT_OT_bake_cancel",
               "SLICEWRIGHT_OT_bake_keep", "SLICEWRIGHT_PT_mode", "SLICEWRIGHT_OT_mode_prepare",
               "SLICEWRIGHT_OT_mode_preview", "SLICEWRIGHT_PT_object", "SLICEWRIGHT_OT_object_override_add",
               "SLICEWRIGHT_OT_object_override_remove")


def installed(name: str) -> bool:
    """Is a class with this name currently registered with Blender (any base type)?"""
    return any(base.bl_rna_get_subclass_py(name) is not None for base in (
        bpy.types.Panel, bpy.types.Operator, bpy.types.PropertyGroup, bpy.types.AddonPreferences,
        bpy.types.UIList))


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        os.environ[ENV] = "fake_engine"
        import slicewright
        self.addon = slicewright
        self.before = bl_common.handler_snapshot()
        self.addCleanup(self.assert_clean)
        self.addCleanup(self.addon.unregister)

    def assert_clean(self):
        """After unregister: nothing of ours is left anywhere."""
        self.addon.unregister()
        for name in CLASS_NAMES:
            self.assertFalse(installed(name), f"{name} still registered")
        self.assertFalse(hasattr(bpy.types.Scene, "slicewright"))
        self.assertFalse(hasattr(bpy.types.Object, "slicewright"))
        self.assertFalse(hasattr(bpy.types.Scene, "slicewright_preview"))
        self.assertFalse(hasattr(bpy.types.WindowManager, "slicewright_issues"))
        self.assertFalse(hasattr(bpy.types.WindowManager, "slicewright_issues_index"))
        self.assertFalse(hasattr(bpy.types.WindowManager, "slicewright_send"))
        self.assertFalse(hasattr(bpy.types.WindowManager, "slicewright_bake"))
        self.assertEqual(bl_common.handler_snapshot(), self.before)
        self.assertEqual([h for h in logging.getLogger("slicewright").handlers
                          if getattr(h, "_slicewright_handler", False)], [])
        from slicewright.blender import registry
        self.assertEqual(registry.state.timers, [])
        self.assertIsNone(registry.state.status)
        from slicewright.blender import bed_draw
        self.assertFalse(bed_draw.is_registered())
        from slicewright.blender import mode as mode_mod
        self.assertFalse(mode_mod.header_installed())                                 # the viewport header entry (03 1.1)
        from slicewright.blender import volume
        self.assertFalse(volume.is_registered())
        from slicewright.blender.paint import overlay
        self.assertFalse(overlay.is_registered())
        from slicewright.blender import slicing
        self.assertIsNone(slicing._rt.runner)
        self.assertFalse(bpy.app.timers.is_registered(slicing.poll_job))
        self.assertEqual(slicing._keymaps, [])
        self.assertNotIn(slicing._on_load_pre, registry.state.load_pre_hooks)
        self.assertNotIn(slicing._on_load_post, registry.state.load_post_hooks)       # the cache reconnect (03 6.4)
        self.assertEqual((slicing._rt.cache, slicing._rt.notes), (None, {}))
        from slicewright.blender import recovery
        self.assertIsNone(recovery._throttle)                                         # no recovery-copy timing left
        from slicewright.blender import staleness
        self.assertFalse(staleness._rt.active)
        self.assertFalse(bpy.app.timers.is_registered(staleness._tick))
        self.assertFalse(bpy.app.timers.is_registered(staleness._auto_tick))
        self.assertNotIn(staleness._on_file_load, registry.state.load_post_hooks)
        from slicewright.blender import exporting
        self.assertEqual((exporting._rt.messages, exporting._rt.job), ({}, None))
        self.assertFalse(bpy.app.timers.is_registered(exporting.poll_export))
        self.assertNotIn(exporting._on_load_pre, registry.state.load_pre_hooks)
        from slicewright.blender import baking
        self.assertEqual((baking._rt.job, baking._rt.messages, baking._rt.estimates), (None, {}, {}))
        self.assertNotIn(baking._on_load_pre, registry.state.load_pre_hooks)
        from slicewright.blender import connections, sending
        self.assertEqual((sending._rt.job, sending._rt.messages, connections._tests), (None, {}, {}))
        self.assertNotIn(sending._on_load_pre, registry.state.load_pre_hooks)
        self.assertNotIn(sending._clear_status, registry.state.timers)
        from slicewright.blender import printers as printers_mod
        self.assertEqual(printers_mod._problems, {})
        from slicewright.core.network import access
        self.assertIsNone(access._state.provider)                                     # the online-access provider
        self.assertFalse(access.network_allowed().allowed)
        from slicewright.blender import secrets_rt
        from slicewright.core import redact
        self.assertIsNone(secrets_rt._rt.manager)
        self.assertEqual(redact.known_count(), 0)
        from slicewright.blender import issues
        self.assertEqual((registry.state.plate_issues, issues.scene_name()), ([], None))
        self.assertNotIn(issues._on_file_load, registry.state.load_post_hooks)

    def test_the_slicer_tab_panels_follow_the_workflow_order_and_open_sensibly(self):
        """03 section 1.1: Slice, Mode, Plate, Printer, Settings, Object, Paint, Preview, Issues, Export, Bake, Send, engine."""
        from slicewright.names import PANEL_ORDER, TAB_NAME, panel_order
        self.addon.register()
        panels = {key: getattr(bpy.types, f"SLICEWRIGHT_PT_{key}") for key in PANEL_ORDER}
        for key, cls in panels.items():
            self.assertEqual(cls.bl_category, TAB_NAME, key)
            self.assertEqual(cls.bl_order, panel_order(key), key)
        self.assertEqual(sorted(panels, key=lambda k: panels[k].bl_order), list(PANEL_ORDER))
        self.assertEqual(PANEL_ORDER, ("slice", "mode", "plate", "printer", "settings", "object", "paint", "preview",
                                       "issues", "export", "bake", "send", "workspace", "diagnostics"))
        closed = {k for k, c in panels.items() if "DEFAULT_CLOSED" in getattr(c, "bl_options", ())}
        self.assertEqual(closed, {"settings", "object", "paint", "bake", "send", "workspace", "diagnostics"})
        self.assertTrue({"slice", "plate", "printer"} <= set(panels) - closed)           # a first-time user sees these open

    def test_register_with_the_fake_engine_registers_everything(self):
        self.addon.register()
        for name in CLASS_NAMES:
            self.assertTrue(installed(name), f"{name} not registered")
        self.assertTrue(hasattr(bpy.types.Scene, "slicewright"))
        self.assertEqual(bpy.context.scene.slicewright.mode, "PREPARE")
        from slicewright.blender import mode as mode_mod
        self.assertTrue(mode_mod.header_installed())
        from slicewright.blender import registry
        self.assertTrue(registry.state.status.ok)
        self.assertTrue(hasattr(bpy.types.WindowManager, "slicewright_issues"))
        self.assertIn(registry.state.status.module_name, ("fake_engine",))
        self.assertEqual(len(bpy.app.handlers.load_pre), len(self.before["load_pre"]) + 1)
        from slicewright.blender import handlers
        self.assertEqual(bpy.app.handlers.load_post.count(handlers._load_post), 1)
        for name, fn in (("depsgraph_update_post", handlers._depsgraph_update_post),     # stale preview (03 6.3)
                         ("undo_post", handlers._undo_redo_post), ("redo_post", handlers._undo_redo_post)):
            self.assertEqual(getattr(bpy.app.handlers, name).count(fn), 1, name)
        from slicewright.blender import staleness
        self.assertTrue(staleness._rt.active)
        from slicewright.blender import exporting, sending, slicing
        self.assertEqual(registry.state.load_post_hooks.count(slicing._on_load_post), 1)
        from slicewright import prefs
        self.assertIn("printers", prefs.SLICEWRIGHT_AP_Preferences.__annotations__)
        from slicewright.blender import online
        from slicewright.core.network import access
        self.assertIs(access._state.provider, online.blender_access)
        self.assertEqual((prefs._Defaults.cache_cap_mb, prefs._Defaults.auto_reslice), (2048, False))
        self.assertEqual(registry.state.load_pre_hooks.count(exporting._on_load_pre), 1)
        self.assertEqual(registry.state.load_pre_hooks.count(sending._on_load_pre), 1)
        from slicewright.blender import baking
        self.assertEqual(registry.state.load_pre_hooks.count(baking._on_load_pre), 1)
        self.assertTrue(hasattr(bpy.types.WindowManager, "slicewright_bake"))
        self.assertEqual(bpy.context.window_manager.slicewright_bake.kind, "CURVES")
        self.assertFalse(bpy.context.window_manager.slicewright_bake.travel)              # travel is off by default
        self.assertTrue(hasattr(bpy.types.WindowManager, "slicewright_send"))
        self.assertEqual(bpy.context.window_manager.slicewright_send.start_print, False)     # never on by default
        for name in ("cache_cap_mb", "auto_reslice", "last_export_dir"):
            self.assertIn(name, prefs.SLICEWRIGHT_AP_Preferences.__annotations__)

    def test_scene_state_is_one_property_tree_with_runtime_job_fields(self):
        from slicewright.engine import jobs
        self.addon.register()
        sw = bpy.context.scene.slicewright
        self.assertFalse(hasattr(bpy.context.scene, "slicewright_preview"))
        self.assertEqual(sw.preview.view_type, "feature")             # the preview group hangs off the scene group
        self.assertEqual((sw.job_state, sw.job_progress, sw.job_message), (jobs.IDLE, 0.0, ""))
        self.assertEqual((sw.stale, sw.last_cache_key), (False, ""))
        self.assertEqual([i.identifier for i in sw.bl_rna.properties["job_state"].enum_items],
                         [k for k, _ in jobs.JOB_STATES])

    def test_file_load_resets_job_state_but_keeps_the_cache_key(self):
        import tempfile
        self.addon.register()
        sw = bpy.context.scene.slicewright
        sw.job_state, sw.job_progress, sw.job_message, sw.stale = "slicing", 0.5, "Generating", True
        sw.last_cache_key = "abc123"
        sw.preview.view_type = "speed"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            sw = bpy.context.scene.slicewright
            self.assertEqual((sw.job_state, sw.job_progress, sw.job_message, sw.stale), ("idle", 0.0, "", False))
            self.assertEqual(sw.last_cache_key, "abc123")
            self.assertEqual(sw.preview.view_type, "speed")           # saved settings survive
            sw.preview.property_unset("view_type")                    # the scene outlives this test
            sw.property_unset("last_cache_key")

    def test_unregister_leaves_no_handlers_or_timers(self):
        from slicewright.blender import registry
        self.addon.register()

        def tick():
            return 1.0
        registry.register_timer(tick, first_interval=5.0)
        self.assertTrue(bpy.app.timers.is_registered(tick))
        self.addon.unregister()
        self.assertFalse(bpy.app.timers.is_registered(tick))
        self.assert_clean()

    def test_register_unregister_cycles(self):
        for _ in range(3):
            self.addon.register()
            self.addon.unregister()
        self.assert_clean()

    def test_a_second_register_replaces_the_first(self):
        self.addon.register()
        self.addon.register()
        self.assertEqual(len(bpy.app.handlers.load_pre), len(self.before["load_pre"]) + 1)

    def test_missing_engine_registers_only_the_diagnostics(self):
        os.environ[ENV] = "slicewright_engine"        # not installed in this Python
        saved = os.environ.get("SLICEWRIGHT_ENGINE_PATH")          # the test runner points it at a built engine
        os.environ["SLICEWRIGHT_ENGINE_PATH"] = ""                 # empty: no extra directory and no source-checkout fallback
        if saved is not None:
            self.addCleanup(os.environ.__setitem__, "SLICEWRIGHT_ENGINE_PATH", saved)
        else:
            self.addCleanup(os.environ.pop, "SLICEWRIGHT_ENGINE_PATH", None)
        self.addon.register()
        from slicewright.blender import registry
        self.assertFalse(registry.state.status.ok)
        self.assertIn("cannot import", registry.state.status.error)
        self.assertTrue(installed("SLICEWRIGHT_PT_diagnostics"))
        self.assertTrue(installed("SLICEWRIGHT_AP_Preferences"))
        self.assertTrue(installed("SLICEWRIGHT_PG_Printer"))          # printers need no engine
        self.assertTrue(installed("SLICEWRIGHT_OT_printer_secret"))
        self.assertTrue(installed("SLICEWRIGHT_OT_printer_test"))             # (it needs no engine)
        self.assertFalse(installed("SLICEWRIGHT_PT_send"))
        self.assertFalse(installed("SLICEWRIGHT_PT_bake"))
        self.assertFalse(hasattr(bpy.types.WindowManager, "slicewright_send"))
        self.assertFalse(installed("SLICEWRIGHT_PG_Scene"))
        self.assertFalse(hasattr(bpy.types.Scene, "slicewright"))

    def test_incompatible_engine_api_registers_only_the_diagnostics(self):
        import fake_engine.api as fake_api
        old = fake_api.API_VERSION
        fake_api.API_VERSION = (2, 0)
        self.addCleanup(setattr, fake_api, "API_VERSION", old)
        self.addon.register()
        from slicewright.blender import registry
        status = registry.state.status
        self.assertFalse(status.ok)
        self.assertEqual((status.found_api, status.required_api), ((2, 0), (1, 0)))
        self.assertFalse(installed("SLICEWRIGHT_PG_Scene"))
        self.assertTrue(installed("SLICEWRIGHT_PT_diagnostics"))

    def test_a_failing_stage_rolls_everything_back(self):
        import slicewright.blender.handlers as handlers

        def boom():
            raise RuntimeError("stage failed")
        self.addCleanup(setattr, handlers, "register", handlers.register)
        handlers.register = boom
        with self.assertRaises(RuntimeError):
            self.addon.register()
        self.assert_clean()

    def test_diagnostics_text_and_operator_work(self):
        self.addon.register()
        from slicewright.blender.operators.diagnostics import gather_text
        text = gather_text()
        for needle in ("Blender: 5.", "Engine OK: True", "Found API: 1.0", "Required API: 1.0"):
            self.assertIn(needle, text)
        self.assertEqual(bpy.ops.slicewright.copy_diagnostics(), {"FINISHED"})

    def test_extension_log_dir_is_none_outside_an_extension(self):
        self.assertIsNone(self.addon._log_dir())


if __name__ == "__main__":
    bl_common.run("__main__")
