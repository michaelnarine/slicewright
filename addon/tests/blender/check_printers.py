# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: physical printers in the preferences, the secret dialog operator, and the online-access gate
(03 sections 8.5 and 8.6).

The secret must reach the (fake, in-memory) store and nothing else: the checks save the .blend and the user preferences and
search the raw bytes for it, and read every property of every printer. The online-access API is also asked for real, in
sub-processes started with ``--online-mode`` and ``--offline-mode``.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
SECRET = "SLICEWRIGHT-TEST-SECRET-9f3a7c21"
HOST = "192.168.77.123"


class MemoryStore:
    """A SecretStore that keeps everything in a dict: the 'fake backend' the secret must end up in."""

    def __new__(cls):
        from slicewright.core.network.secrets import SecretStore

        class _Memory(SecretStore):
            name = "test keychain"

            def __init__(self):
                self.items = {}
                self.refuse = False

            def available(self):
                return True

            def get(self, ref):
                return self.items.get(ref)

            def set(self, ref, secret):
                from slicewright.core.network.secrets import SecretStoreError
                if self.refuse:
                    raise SecretStoreError("keychain said no")
                self.items[ref] = secret

            def delete(self, ref):
                from slicewright.core.network.secrets import SecretStoreError
                if self.refuse:
                    raise SecretStoreError("keychain said no")
                return self.items.pop(ref, None) is not None
        return _Memory()


class PrinterTests(unittest.TestCase):
    def setUp(self):
        import slicewright
        from slicewright import prefs
        from slicewright.blender import paths, printers, printers_pg, secrets_rt
        from slicewright.core.network import secrets as core_secrets
        self.addon, self.prefs_mod, self.printers, self.pg_mod, self.rt = slicewright, prefs, printers, printers_pg, secrets_rt
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        paths.set_override(self.tmp.name)
        self.addCleanup(paths.set_override, None)
        self.addCleanup(self.addon.unregister)
        self.addon.register()
        self.store = MemoryStore()
        self.manager = core_secrets.SecretManager(self.store, core_secrets.FileSecretStore(os.path.join(self.tmp.name, "f.json")))
        self.rt.set_manager(self.manager)
        entry = bpy.context.preferences.addons.new()
        entry.module = prefs._package()
        self.addCleanup(bpy.context.preferences.addons.remove, entry)
        self.prefs = bpy.context.preferences.addons[prefs._package()].preferences

    def add(self, kind="octoprint"):
        before = len(self.prefs.printers)
        self.assertEqual(bpy.ops.slicewright.printer_add(kind=kind), {"FINISHED"})
        self.assertEqual(len(self.prefs.printers), before + 1)
        return self.prefs.printers[-1]

    def set_secret(self, pg, secret=SECRET):
        return bpy.ops.slicewright.printer_secret("EXEC_DEFAULT", printer_id=pg.id, secret=secret)

    # -- records ---------------------------------------------------------------------------------------------

    def test_add_edit_and_remove(self):
        a, b = self.add("moonraker"), self.add("bambu_lan")
        self.assertEqual((a.kind, b.kind), ("moonraker", "bambu_lan"))
        self.assertEqual((a.name, b.name), ("Printer", "Printer 2"))
        self.assertTrue(a.id and a.id != b.id and a.secret_ref and a.secret_ref != b.secret_ref)
        self.assertFalse(a.secret_saved)
        a.host, a.port, a.use_https, a.base_path, a.preset = HOST, 7126, True, "/klipper", "Some Preset"
        a.tls_mode = "pin"
        record = self.pg_mod.to_record(a)
        self.assertEqual((record.host, record.port, record.preset), (HOST, 7126, "Some Preset"))
        self.assertEqual(record.options, {"https": True, "tls": "pin", "base_path": "/klipper"})
        self.assertEqual(self.printers.problems(a), [])
        b.serial = " 01p00a123456789 "
        self.assertEqual(self.pg_mod.to_record(b).options, {"serial": "01P00A123456789"})
        a_id, b_id = a.id, b.id                       # (property group references go stale when the collection changes)
        self.assertEqual(bpy.ops.slicewright.printer_remove("EXEC_DEFAULT", printer_id=a_id), {"FINISHED"})
        self.assertEqual([p.id for p in self.prefs.printers], [b_id])
        self.assertEqual(bpy.ops.slicewright.printer_remove("EXEC_DEFAULT", printer_id="nope"), {"CANCELLED"})

    def test_problems_are_reported_for_bad_input(self):
        a, b = self.add(), self.add()
        a.host = "http://octopi.local:5000"
        b.name = a.name.lower()
        self.assertTrue(any(p.field == "host" for p in self.printers.problems(a)))
        self.assertTrue(any(p.field == "name" for p in self.printers.problems(b)))
        a.host = "octopi.local"
        self.assertEqual([p.field for p in self.printers.problems(a) if p.field != "name"], [])
        b.name = "Other"
        self.assertEqual(self.printers.problems(a), [])
        self.assertTrue(any(p.field == "secret" for p in self.printers.problems(a, complete=True)))

    # -- the secret ------------------------------------------------------------------------------------------

    def test_the_secret_goes_to_the_store_and_the_operator_property_is_blank_afterwards(self):
        pg = self.add()
        self.assertEqual(self.set_secret(pg), {"FINISHED"})
        self.assertEqual(self.store.items, {pg.secret_ref: SECRET})
        self.assertTrue(pg.secret_saved)
        last = bpy.context.window_manager.operator_properties_last("slicewright.printer_secret")
        self.assertEqual(getattr(last, "secret", ""), "")                 # SKIP_SAVE: not remembered
        # no property of the printer holds it, and the raw ID-property storage does not either
        for prop in pg.bl_rna.properties:
            value = getattr(pg, prop.identifier, None)
            self.assertNotIn(SECRET, str(value), prop.identifier)
        self.assertEqual([k for k in pg.keys() if SECRET in str(pg[k])], [])

    def test_nothing_that_is_saved_contains_the_secret(self):
        pg = self.add()
        pg.host = HOST
        self.set_secret(pg)
        blend = os.path.join(self.tmp.name, "scene.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend)
        self.assertNotIn(SECRET.encode(), open(blend, "rb").read())
        # the preferences are saved by a child Blender whose config directory is a temporary one: save_userpref would
        # overwrite the real user's preferences otherwise (the child refuses to run if the directory is not ours)
        root = os.path.join(self.tmp.name, "child-user")
        os.makedirs(root)
        env = {**os.environ, "BLENDER_USER_RESOURCES": root, "SLICEWRIGHT_TEST_CONFIG_ROOT": root,
               "SLICEWRIGHT_TEST_HOST": HOST, "SLICEWRIGHT_TEST_SECRET_VALUE": SECRET}
        child = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prefs_child.py")
        done = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", "--python-exit-code", "1", "--python", child],
                              capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(done.returncode, 0, done.stderr[-1500:] + done.stdout[-1500:])
        self.assertNotIn(SECRET, done.stdout + done.stderr)
        prefs_file = os.path.join(root, "config", "userpref.blend")
        self.assertTrue(os.path.exists(prefs_file), os.listdir(root))
        raw_prefs = open(prefs_file, "rb").read()
        self.assertIn(HOST.encode(), raw_prefs)                           # the record IS persisted (so the grep means something)
        self.assertNotIn(SECRET.encode(), raw_prefs)
        for where, _dirs, files in os.walk(root):                         # nor in anything the add-on or Blender wrote
            for name in files:
                self.assertNotIn(SECRET.encode(), open(os.path.join(where, name), "rb").read(), name)
        for where, _dirs, files in os.walk(self.tmp.name):
            for name in files:
                self.assertNotIn(SECRET.encode(), open(os.path.join(where, name), "rb").read(), name)

    def test_bad_or_failed_saves_leave_the_record_unsaved_and_say_why(self):
        pg = self.add()
        for bad in ("", "line\nbreak"):
            with self.assertRaisesRegex(RuntimeError, "Enter the access code"):
                self.set_secret(pg, bad)
        self.assertFalse(pg.secret_saved)
        self.store.refuse = True
        with self.assertRaisesRegex(RuntimeError, "Not saved: keychain said no"):
            self.set_secret(pg)
        self.assertFalse(pg.secret_saved)
        self.assertEqual(self.store.items, {})

    def test_replace_and_forget(self):
        pg = self.add()
        self.set_secret(pg)
        self.set_secret(pg, "second-secret-value")
        self.assertEqual(self.store.items, {pg.secret_ref: "second-secret-value"})
        self.assertEqual(bpy.ops.slicewright.printer_secret_clear("EXEC_DEFAULT", printer_id=pg.id), {"FINISHED"})
        self.assertEqual((self.store.items, pg.secret_saved), ({}, False))
        self.set_secret(pg)
        self.store.refuse = True
        with self.assertRaisesRegex(RuntimeError, "Not removed: keychain said no"):
            bpy.ops.slicewright.printer_secret_clear("EXEC_DEFAULT", printer_id=pg.id)
        self.assertTrue(pg.secret_saved)

    def test_removing_a_printer_deletes_its_secret_and_a_refusing_store_keeps_both(self):
        a, b = self.add(), self.add()
        self.set_secret(a)
        self.set_secret(b, "other-secret-value")
        self.store.refuse = True
        with self.assertRaisesRegex(RuntimeError, "The printer was not removed: keychain said no"):
            bpy.ops.slicewright.printer_remove("EXEC_DEFAULT", printer_id=a.id)
        self.assertEqual(len(self.prefs.printers), 2)
        self.store.refuse = False
        self.assertEqual(bpy.ops.slicewright.printer_remove("EXEC_DEFAULT", printer_id=a.id), {"FINISHED"})
        self.assertEqual(list(self.store.items.values()), ["other-secret-value"])

    def test_the_file_fallback_warns_once_when_the_first_secret_is_saved(self):
        import json
        from slicewright.core.network import secrets as core
        path = os.path.join(self.tmp.name, "secrets.json")
        file_mgr = core.default_manager(path, platform="linux")
        self.rt.set_manager(file_mgr)
        pg = self.add()
        self.assertFalse(os.path.exists(path))
        self.set_secret(pg)                                                # the operator reports the warning ...
        self.assertTrue(json.load(open(path))["warned"])                   # ... and consumed the one-time flag
        self.assertIsNone(file_mgr.take_warning())
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertEqual(file_mgr.get(pg.secret_ref), SECRET)              # (the file IS the store here, by design)

    # -- UI --------------------------------------------------------------------------------------------------

    def test_the_printers_box_draws_the_state_and_the_reason_network_is_off(self):
        from slicewright.blender.ui import printers_box
        pg = self.add("bambu_lan")
        pg.host = "bad host"
        self.set_secret(pg)
        box = mock.MagicMock()
        printers_box.draw(box, self.prefs)
        texts = " ".join(str(c) for c in box.mock_calls)
        self.assertIn("Network features are off", texts)                   # -b starts with online access off
        self.assertIn("Allow Online Access", texts)
        self.assertIn("Access code: saved", texts)
        self.assertIn("unsupported by Bambu", texts)
        self.assertIn("host", texts.lower())
        self.assertNotIn(SECRET, texts)

    # -- the online-access gate -----------------------------------------------------------------------------

    def test_gate_reads_blender_and_says_why(self):
        from slicewright.blender import online
        from slicewright.core.network import access
        self.assertIs(access._state.provider, online.blender_access)
        result = access.network_allowed()
        self.assertFalse(result.allowed)                                   # factory startup, background: off
        self.assertEqual(result.reason, online.OFF_PREFERENCE)
        for online_access, override, expect_allowed, reason in (
                (True, True, True, ""), (True, False, True, ""), (False, True, False, online.OFF_COMMAND_LINE),
                (False, False, False, online.OFF_PREFERENCE)):
            fake = SimpleNamespace(app=SimpleNamespace(online_access=online_access, online_access_override=override))
            with mock.patch.object(online, "bpy", fake):
                got = access.network_allowed()
            self.assertEqual((got.allowed, got.reason), (expect_allowed, reason))
        with mock.patch.object(online, "bpy", SimpleNamespace(app=SimpleNamespace())):
            self.assertEqual(access.network_allowed().reason, online.NOT_REPORTED)

    def test_no_connection_is_attempted_while_blender_says_offline(self):
        from slicewright.core.network.access import OnlineAccessDenied
        from slicewright.core.network.httpclient import request
        from slicewright.core.network.nbsocket import run_blocking
        with self.assertRaises(OnlineAccessDenied) as ctx:
            run_blocking(request("GET", "http://127.0.0.1:9/"))
        self.assertIn("Allow Online Access", str(ctx.exception))

    def test_poll_helper_greys_an_operator_with_the_reason(self):
        from slicewright.blender import online

        class SLICEWRIGHT_OT_probe_net(bpy.types.Operator):
            bl_idname = "slicewright_probe.net"
            bl_label = "Probe"

            @classmethod
            def poll(cls, context):
                return online.poll_network(cls)

            def execute(self, context):
                return {"FINISHED"}
        bpy.utils.register_class(SLICEWRIGHT_OT_probe_net)
        self.addCleanup(bpy.utils.unregister_class, SLICEWRIGHT_OT_probe_net)
        self.assertFalse(bpy.ops.slicewright_probe.net.poll())
        with mock.patch.object(online, "bpy", SimpleNamespace(app=SimpleNamespace(online_access=True))):
            self.assertTrue(bpy.ops.slicewright_probe.net.poll())

    def test_the_real_api_in_both_modes(self):
        """Ask Blender itself, started with each flag, what the online-access API says."""
        probe = ("import bpy,sys;sys.stdout.write('RESULT %s %s' % (bpy.app.online_access, bpy.app.online_access_override))")
        seen = {}
        for flag in ("", "--online-mode", "--offline-mode"):
            cmd = [bpy.app.binary_path, "-b", "--factory-startup"] + ([flag] if flag else []) + ["--python-expr", probe]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
            seen[flag] = re.search(r"RESULT (True|False) (True|False)", out).group(0)[len("RESULT "):]
        self.assertEqual(seen, {"": "False False", "--online-mode": "True True", "--offline-mode": "False True"})

    def test_unregister_removes_the_provider_and_the_classes(self):
        from slicewright.core.network import access
        self.addon.unregister()
        self.assertIsNone(access._state.provider)
        self.assertFalse(access.network_allowed().allowed)
        for name in ("SLICEWRIGHT_OT_printer_add", "SLICEWRIGHT_OT_printer_remove", "SLICEWRIGHT_OT_printer_secret",
                     "SLICEWRIGHT_OT_printer_secret_clear"):
            self.assertIsNone(bpy.types.Operator.bl_rna_get_subclass_py(name))
        self.assertIsNone(bpy.types.PropertyGroup.bl_rna_get_subclass_py("SLICEWRIGHT_PG_Printer"))


if __name__ == "__main__":
    bl_common.run("__main__")
