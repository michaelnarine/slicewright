# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the session's secret store (03 section 8.5): backend choice, the file fallback under the user
config directory, redaction in the add-on's logs, and a clean unregister."""
import json
import logging
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
SECRET = "ACCESS-0123456789-abcdef"


class SecretsRuntimeTests(unittest.TestCase):
    def setUp(self):
        import slicewright
        from slicewright.blender import paths, secrets_rt
        from slicewright.core import redact
        self.addon, self.paths, self.rt, self.redact = slicewright, paths, secrets_rt, redact
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        paths.set_override(self.tmp.name)
        self.addCleanup(paths.set_override, None)
        self.addCleanup(self.addon.unregister)
        self.addon.register()

    def test_the_file_lives_under_the_user_dir_and_nothing_is_created_by_looking(self):
        path = self.rt.secrets_file()
        self.assertEqual(path, os.path.join(self.tmp.name, "secrets", "secrets.json"))
        self.rt.manager()
        self.rt.status_lines()
        self.assertFalse(os.path.exists(path))                        # no file until a secret is stored

    def test_file_fallback_round_trip_is_0600_and_warns_once(self):
        from slicewright.core.network import secrets as core
        mgr = core.default_manager(self.rt.secrets_file(), platform="linux")
        self.rt.set_manager(mgr)
        ref = mgr.new_ref()
        mgr.set(ref, SECRET)
        self.assertEqual(mgr.get(ref), SECRET)
        path = self.rt.secrets_file()
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertIn("unencrypted", mgr.take_warning())
        self.assertIsNone(mgr.take_warning())
        lines = self.rt.status_lines()
        self.assertTrue(all(warn for _, warn in lines))
        self.assertIn("unencrypted file", lines[0][0])
        self.assertTrue(all(SECRET not in text for text, _ in lines))

    def test_keychain_status_line_is_not_a_warning(self):
        from slicewright.core.network import secrets as core
        fake = core.MacKeychainStore(runner=lambda argv, stdin: (44, ""), platform="darwin")
        self.rt.set_manager(core.SecretManager(fake, core.FileSecretStore(self.rt.secrets_file())))
        text, warn = self.rt.status_lines()[0]
        self.assertFalse(warn)
        self.assertIn("macOS keychain", text)

    def test_the_addon_log_never_holds_a_secret(self):
        from slicewright.core import logs
        log_dir = os.path.join(self.tmp.name, "logs")
        logs.setup("DEBUG", log_dir)
        self.addCleanup(logs.teardown)
        from slicewright.core.network import secrets as core
        mgr = core.default_manager(self.rt.secrets_file(), platform="linux")
        mgr.set("r1", SECRET)                                       # registers it with the redaction layer
        logs.get_logger("net").error("connect failed: access_code=%s and %s", "87654321", SECRET)
        for handler in logging.getLogger("slicewright").handlers:
            handler.flush()
        with open(os.path.join(log_dir, logs.LOG_FILE_NAME), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("connect failed", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("87654321", text)

    def test_preferences_draw_reports_the_backend(self):
        from unittest import mock

        from slicewright import prefs
        from slicewright.core.network import secrets as core
        self.rt.set_manager(core.default_manager(self.rt.secrets_file(), platform="linux"))
        layout = mock.MagicMock()
        box = layout.box.return_value
        prefs.SLICEWRIGHT_AP_Preferences.draw(mock.MagicMock(layout=layout), bpy.context)
        labels = [c.kwargs.get("text", "") for c in box.label.call_args_list]
        self.assertIn("Secrets", labels)
        self.assertTrue(any("unencrypted file" in t for t in labels), labels)

    def test_unregister_forgets_the_manager_and_every_remembered_secret(self):
        from slicewright.core.network import secrets as core
        mgr = core.default_manager(self.rt.secrets_file(), platform="linux")
        self.rt.set_manager(mgr)
        mgr.set("r1", SECRET)
        self.assertGreater(self.redact.known_count(), 0)
        self.addon.unregister()
        self.assertIsNone(self.rt._rt.manager)
        self.assertEqual(self.redact.known_count(), 0)

    def test_the_secret_file_is_not_in_any_saved_property(self):
        """Nothing in the scene or the preferences changes when a secret is stored."""
        from slicewright.core.network import secrets as core
        mgr = core.default_manager(self.rt.secrets_file(), platform="linux")
        mgr.set("r1", SECRET)
        path = os.path.join(self.tmp.name, "t.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        with open(path, "rb") as f:
            self.assertNotIn(SECRET.encode(), f.read())
        with open(self.rt.secrets_file(), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["secrets"], {"r1": SECRET})


if __name__ == "__main__":
    bl_common.run("__main__")
