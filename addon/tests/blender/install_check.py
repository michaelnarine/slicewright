# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender after installing the built extension zip (see addon-ci.yml).

Not part of the pytest run: it needs ``extension install-file --enable`` first. It checks the
real extension path: the package name is ``bl_ext.<repo>.slicewright``, the log directory
resolves through ``extension_path_user``, registration works with the preferences class bound
to that package, and the add-on can be disabled again without leaving anything behind.
"""
import logging
import sys
import unittest

import addon_utils
import bpy


def module_name():
    for mod in addon_utils.modules():
        if mod.__name__.endswith(".slicewright"):
            return mod.__name__
    raise AssertionError("the slicewright extension is not installed")


class InstalledExtensionTests(unittest.TestCase):
    def test_enabled_extension_registers_and_unregisters_cleanly(self):
        name = module_name()
        self.assertTrue(name.startswith("bl_ext."), name)
        addon_utils.enable(name, default_set=True, persistent=False)
        self.assertIn(name, bpy.context.preferences.addons)
        pkg = sys.modules[name]
        from importlib import import_module
        registry = import_module(name + ".blender.registry")
        status = registry.state.status
        self.assertIsNotNone(status)
        self.assertFalse(status.ok)            # no engine is bundled yet
        self.assertIn("cannot import", status.error)
        self.assertTrue(registry.state.log_dir and "slicewright" in registry.state.log_dir)
        prefs = bpy.context.preferences.addons[name].preferences
        self.assertEqual(prefs.log_level, "WARNING")
        prefs.log_level = "DEBUG"
        self.assertEqual(logging.getLogger("slicewright").level, logging.DEBUG)
        self.assertTrue(hasattr(bpy.types, "SLICEWRIGHT_PT_diagnostics"))
        addon_utils.disable(name, default_set=True)
        self.assertFalse(hasattr(bpy.types, "SLICEWRIGHT_PT_diagnostics"))
        self.assertEqual([h for h in logging.getLogger("slicewright").handlers
                          if getattr(h, "_slicewright_handler", False)], [])
        self.assertIsNotNone(pkg)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules["__main__"])
    ok = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite).wasSuccessful()
    sys.stdout.flush()
    sys.exit(0 if ok else 1)
