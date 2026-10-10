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
               "SLICEWRIGHT_AP_Preferences", "SLICEWRIGHT_PG_Scene", "SLICEWRIGHT_PG_FilamentSlot", "SLICEWRIGHT_PG_Object", "SLICEWRIGHT_PG_Config",
               "SLICEWRIGHT_PT_printer", "SLICEWRIGHT_PT_settings", "SLICEWRIGHT_OT_import_presets", "SLICEWRIGHT_PG_ImportItem", "SLICEWRIGHT_OT_preset_save", "SLICEWRIGHT_OT_preset_revert",
               "SLICEWRIGHT_OT_preset_diff", "SLICEWRIGHT_OT_preset_manage", "SLICEWRIGHT_OT_preset_restore_embedded", "SLICEWRIGHT_OT_settings_override", "SLICEWRIGHT_OT_load_library",
               "SLICEWRIGHT_OT_filament_add", "SLICEWRIGHT_OT_filament_remove",
               "SLICEWRIGHT_PT_plate", "SLICEWRIGHT_OT_use_mm_scene", "SLICEWRIGHT_OT_drop_to_bed",
               "SLICEWRIGHT_PT_paint", "SLICEWRIGHT_OT_paint_assign")


def installed(name: str) -> bool:
    """Is a class with this name currently registered with Blender (any base type)?"""
    return any(base.bl_rna_get_subclass_py(name) is not None for base in (
        bpy.types.Panel, bpy.types.Operator, bpy.types.PropertyGroup, bpy.types.AddonPreferences))


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
        self.assertEqual(bl_common.handler_snapshot(), self.before)
        self.assertEqual([h for h in logging.getLogger("slicewright").handlers
                          if getattr(h, "_slicewright_handler", False)], [])
        from slicewright.blender import registry
        self.assertEqual(registry.state.timers, [])
        self.assertIsNone(registry.state.status)
        from slicewright.blender import bed_draw
        self.assertFalse(bed_draw.is_registered())
        from slicewright.blender import volume
        self.assertFalse(volume.is_registered())
        from slicewright.blender.paint import overlay
        self.assertFalse(overlay.is_registered())

    def test_register_with_the_fake_engine_registers_everything(self):
        self.addon.register()
        for name in CLASS_NAMES:
            self.assertTrue(installed(name), f"{name} not registered")
        self.assertTrue(hasattr(bpy.types.Scene, "slicewright"))
        self.assertEqual(bpy.context.scene.slicewright.mode, "PREPARE")
        from slicewright.blender import registry
        self.assertTrue(registry.state.status.ok)
        self.assertIn(registry.state.status.module_name, ("fake_engine",))
        self.assertEqual(len(bpy.app.handlers.load_pre), len(self.before["load_pre"]) + 1)

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
        self.addon.register()
        from slicewright.blender import registry
        self.assertFalse(registry.state.status.ok)
        self.assertIn("cannot import", registry.state.status.error)
        self.assertTrue(installed("SLICEWRIGHT_PT_diagnostics"))
        self.assertTrue(installed("SLICEWRIGHT_AP_Preferences"))
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
