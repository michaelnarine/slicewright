# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: sending the last slice to a printer (03 section 8), on the fake engine or the REAL one, against
the in-process fake OctoPrint and Moonraker servers of ``tests/fake_net.py``. No real device, no real credential, no scan.

``SLICEWRIGHT_CHECK_BACKEND=real`` selects the built ``slicewright_engine`` and the Bambu Lab A1 (skipped when it is not
built); the default is the fake. Timers never fire in ``-b``: the tests pump ``slicing.poll_job``, ``exporting.poll_export``
and the tick runner (the upload is a task on it). Blender's online access is off in a background session, so the tests
install an "allowed" provider for their loopback servers, and switch it off to see the gate.
"""
import glob
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

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

import fake_net  # noqa: E402

KEY = "SLICEWRIGHT-TEST-SECRET-octo-0123456789"
MKEY = "SLICEWRIGHT-TEST-SECRET-moon-0123456789"
BCODE = "SLICEWRIGHT-TEST-SECRET-bambu-0123456789"
SERIAL = "01P00A123456789"


def read(path):
    with open(path, "rb") as f:
        return f.read()


class Layout:
    """A recording stand-in for ``UILayout``."""

    def __init__(self, log):
        self.log, self.alert, self.enabled, self.scale_y = log, False, True, 1.0

    def progress(self, factor=0.0, type="BAR", text=""):
        self.log.append(("progress", round(factor, 2), text))

    def column(self, align=False):
        return Layout(self.log)

    def row(self, align=False):
        return Layout(self.log)

    def label(self, text="", icon="NONE"):
        self.log.append(("label", text, icon))

    def prop(self, data, name, text=None, icon="NONE", placeholder=None):
        self.log.append(("prop", name))

    def operator(self, idname, text="", icon="NONE", emboss=True):
        self.log.append(("operator", idname, text, icon, self.enabled))
        return type("Op", (), {})()


def labels(log):
    return [e[1] for e in log if e[0] == "label"]


class SendTests(unittest.TestCase):
    def setUp(self):
        import slicewright
        from slicewright import prefs
        from slicewright.blender import (connections, exporting, library, online, paths, picker, printers, printers_pg,
                                         registry, secrets_rt, sending, slicing, staleness, timers)
        from slicewright.blender.preview import runtime
        from slicewright.core.network import access
        from slicewright.core.network import secrets as core_secrets
        self.addon, self.prefs_mod, self.exporting, self.library = slicewright, prefs, exporting, library
        self.registry, self.slicing, self.st, self.timers, self.runtime, self.picker = (
            registry, slicing, staleness, timers, runtime, picker)
        self.sending, self.connections, self.printers, self.online, self.access = (
            sending, connections, printers, online, access)
        self.pg_mod, self.rt = printers_pg, secrets_rt
        if REAL:
            from slicewright.engine import adapter
            adapter.add_engine_paths()
            try:
                import slicewright_engine  # noqa: F401
            except ImportError:
                self.skipTest("slicewright_engine is not built")
        self.tmp = tempfile.mkdtemp(prefix="sw-send-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        paths.set_override(os.path.join(self.tmp, "user"))
        self.addCleanup(paths.set_override, None)
        self.leftovers = set(glob.glob(os.path.join(tempfile.gettempdir(), "slicewright-send-*")))
        self.scene = bl_common.fresh_scene()
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)
        self.assertTrue(self.registry.state.status.ok, self.registry.state.status.summary)
        entry = bpy.context.preferences.addons.new()                      # real preferences (the printers live there)
        entry.module = prefs._package()
        self.addCleanup(bpy.context.preferences.addons.remove, entry)
        self.prefs = bpy.context.preferences.addons[prefs._package()].preferences
        self.items = {}
        store = type("Store", (core_secrets.SecretStore,), {
            "name": "test keychain", "available": lambda s: True, "get": lambda s, r: self.items.get(r),
            "set": lambda s, r, v: self.items.__setitem__(r, v), "delete": lambda s, r: self.items.pop(r, None) is not None})()
        self.rt.set_manager(core_secrets.SecretManager(store, core_secrets.FileSecretStore(os.path.join(self.tmp, "f.json"))))
        self.allow(True)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        self.wm = bpy.context.window_manager
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

    def finish(self):
        if self.slicing.is_live():
            self.slicing.runner().cancel(discard=True)
            self.pump(expect=None)
        self.drain()
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)
        left = set(glob.glob(os.path.join(tempfile.gettempdir(), "slicewright-send-*"))) - self.leftovers
        self.assertEqual(left, set(), "a send left its temp directory behind")

    # -- helpers ------------------------------------------------------------------------------------------

    def allow(self, yes):
        """The gate: Blender's own answer is "no" in -b; ``allow(True)`` lets the loopback fake servers be reached."""
        self.access.set_provider((lambda: self.access.ALLOW) if yes else self.online.blender_access)

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

    def pump_export(self, timeout=120.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.exporting.poll_export() is None:
                return
            time.sleep(0.002)
        self.fail("the re-slice never finished")

    def drain(self, timeout=30.0, *, until_sent=False):
        """Run the tick runner until it is idle (the preview's GPU upload cannot run in -b, so it is dropped)."""
        runner = self.timers.runner
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for task in runner.pending():
                if task.name.startswith("preview:"):
                    runner.cancel(task)
            if runner.idle and not (until_sent and self.sending.is_live()):
                return
            runner.tick()
            time.sleep(0.001)
        self.fail("the tick runner never went idle")

    def drop_previews(self):
        for task in self.timers.runner.pending():
            if task.name.startswith("preview:"):                  # the GPU upload cannot run in -b (03 7.9a)
                self.timers.runner.cancel(task)

    def slice(self):
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.pump()
        self.drop_previews()

    def result(self):
        return self.runtime.get(self.scene).result

    def hit(self):
        self.slice()
        first = read(self.result().gcode_path)
        self.drain()
        self.slice()
        self.drain()
        self.assertEqual(self.exporting.source_for(self.scene).kind, "cached")
        return first

    def flush_stale(self):
        if bpy.app.timers.is_registered(self.st._tick):
            bpy.app.timers.unregister(self.st._tick)
        self.st._tick()

    def edit_plate(self):
        self.cube.location.x += 15.0
        bpy.context.view_layer.update()
        self.flush_stale()
        self.assertTrue(self.sw.stale)

    def add_printer(self, kind, srv=None, *, secret=None, https=False, tls="verify", name=None, preset="", host="127.0.0.1"):
        self.assertEqual(bpy.ops.slicewright.printer_add(kind=kind), {"FINISHED"})
        pg = self.prefs.printers[-1]
        pg.host, pg.preset = host, preset
        if srv is not None:
            pg.port = srv.port
            pg.base_path = srv.prefix
        pg.use_https, pg.tls_mode = https, tls
        if name:
            pg.name = name
        if secret:
            self.assertEqual(bpy.ops.slicewright.printer_secret("EXEC_DEFAULT", printer_id=pg.id, secret=secret),
                             {"FINISHED"})
        return pg.name, pg.id

    def add_bambu(self, srv, *, secret=BCODE, name="Bambu", serial=SERIAL):
        """A Bambu LAN printer record pointed at a ``FakeFtpsServer`` (its certificate is not trusted yet)."""
        _, pid = self.add_printer("bambu_lan", None, secret=secret, name=name)
        pg = self.printers.find(pid)
        pg.port, pg.serial = srv.port, serial
        return pg.name, pid

    def trust(self, pid):
        self.assertEqual(bpy.ops.slicewright.printer_trust_cert("EXEC_DEFAULT", printer_id=pid), {"FINISHED"})

    def certs(self):
        if not fake_net.have_openssl():
            self.skipTest("openssl is needed for a throw-away certificate")
        import pathlib
        return fake_net.make_self_signed(pathlib.Path(self.tmp), "bambu-a"), fake_net.make_self_signed(pathlib.Path(self.tmp), "bambu-b")

    def choose(self, name, **kw):
        send = self.wm.slicewright_send
        send.printer = name
        for key, value in kw.items():
            setattr(send, key, value)
        return send

    def invoke_send(self, **kw):
        """What clicking the button does. In -b ``INVOKE_DEFAULT`` runs ``execute`` straight away (no window, no popups), so
        the ``invoke`` method is called itself; the popups it opens run their ``execute`` at once (they answer "yes")."""
        from slicewright.blender.operators import send as send_ops
        op = SimpleNamespace(allow_stale=kw.get("allow_stale", False), confirm_print=kw.get("confirm_print", False),
                             execute=lambda context: {"FINISHED"})
        return send_ops.SLICEWRIGHT_OT_send.invoke(op, bpy.context, None)

    def send_and_wait(self, **kw):
        self.assertEqual(bpy.ops.slicewright.send(**kw), {"FINISHED"})
        self.drain(until_sent=True)

    def last(self):
        return self.sending.message(self.scene)

    def issues(self):
        from slicewright.blender import issues
        return [i for i in issues.current() if i["code"].startswith("send_")]

    # -- the gate and the reasons -------------------------------------------------------------------------

    def test_with_online_access_off_everything_is_greyed_with_the_reason_and_nothing_is_sent(self):
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, pid = self.add_printer("octoprint", srv, secret=KEY)
            self.slice()
            self.choose(name)
            self.allow(False)
            self.assertFalse(bpy.ops.slicewright.send.poll())
            self.assertFalse(bpy.ops.slicewright.printer_test.poll())
            ok, why = self.sending.availability(self.scene)
            self.assertEqual((ok, why), (False, self.online.OFF_PREFERENCE))
            with self.assertRaises(RuntimeError):
                bpy.ops.slicewright.send()
            with self.assertRaises(RuntimeError):
                bpy.ops.slicewright.printer_test("EXEC_DEFAULT", printer_id=pid)
            self.assertIsNotNone(self.connections.start_test(pid))
            self.assertEqual(self.connections.state(pid).level, "ERROR")
            self.assertEqual(srv.requests, [])
            from slicewright.blender.ui import send_panel
            log = []
            send_panel.draw_send(Layout(log), bpy.context)
            self.assertIn("Network features are off", labels(log))
            self.assertTrue(all(not e[4] for e in log if e[0] == "operator" and e[1] == "slicewright.printer_test"))

    def test_the_reasons_the_button_is_greyed_out_are_specific(self):
        reasons = lambda: self.sending.availability(self.scene)       # noqa: E731
        self.assertEqual(reasons(), (False, "Slice the plate to send it"))
        self.slice()
        self.assertEqual(reasons(), (False, "Choose a printer to send to"))
        name, pid = self.add_printer("octoprint", name="Bare")        # no host-less: no secret
        self.choose(name)
        self.assertEqual(reasons()[1], "Set the API key")
        self.assertEqual(self.prefs.printers[-1].secret_saved, False)
        bame, bid = self.add_printer("bambu_lan", name="Bambu", secret="12345678")
        self.choose(bame)
        self.assertEqual(reasons(), (False, "A Bambu printer needs its serial number to be sent to"))
        self.assertFalse(bpy.ops.slicewright.send.poll())
        self.printers.find(bid).serial = SERIAL
        self.assertEqual(reasons(), (True, ""))                       # a .gcode.3mf is the Bambu default
        self.choose(bame, file_format="gcode")
        self.assertEqual(reasons(), (False, "A Bambu printer takes a .gcode.3mf file: choose that file type"))
        self.choose(bame, file_format="auto", subfolder="cases")
        self.assertEqual(reasons(), (False, "A Bambu printer stores files in its root: clear the Folder field"))
        self.choose(bame, subfolder="", start_print=True)
        self.assertEqual(reasons(), (True, ""))                       # a Bambu print needs the .gcode.3mf it gets by default
        self.choose(bame, file_format="gcode")
        self.assertEqual(reasons()[1], "A Bambu printer takes a .gcode.3mf file: choose that file type")
        self.choose(bame, file_format="auto", start_print=False)
        self.assertEqual(self.sending.state().file_format, "auto")
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        if self.slicing.is_live():
            self.assertEqual(reasons()[1], "Wait for the slice to finish")
        self.pump()

    def test_only_printers_paired_with_the_current_preset_are_offered_unless_all_are_asked_for(self):
        self.add_printer("octoprint", name="Paired", preset=self.sw.printer_id or "mine")
        self.add_printer("octoprint", name="Elsewhere", preset="some other preset")
        self.add_printer("moonraker", name="Unpaired")
        offered = lambda **kw: [r.name for r in self.sending.eligible(self.scene, **kw)]   # noqa: E731
        self.sw.printer_id = "mine" if not self.sw.printer_id else self.sw.printer_id
        self.assertEqual(offered(), ["Paired", "Unpaired"])
        self.assertEqual(offered(show_all=True), ["Paired", "Elsewhere", "Unpaired"])
        self.assertEqual(self.sending.chosen(self.scene), None)        # two to choose from: nothing is picked for you
        self.choose("Elsewhere")
        self.assertEqual(self.sending.chosen(self.scene).name, "Elsewhere")     # a named printer is the choice

    # -- sending G-code ------------------------------------------------------------------------------------

    def test_gcode_goes_to_octoprint_byte_for_byte_under_the_engines_name(self):
        from slicewright.blender import issues
        self.slice()
        gcode = read(self.result().gcode_path)
        engine_name = self.exporting.engine_name(self.exporting.source_for(self.scene))
        saved_dir = self.prefs.last_export_dir
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, pid = self.add_printer("octoprint", srv, secret=KEY, name="Octo")
            self.choose(name)
            self.assertEqual(self.sending.availability(self.scene), (True, ""))
            self.assertEqual(bpy.ops.slicewright.send(), {"FINISHED"})
            self.assertTrue(self.sending.is_live())
            self.assertFalse(bpy.ops.slicewright.send.poll())                  # no second send meanwhile
            self.assertEqual(self.sending.availability(self.scene)[1], "Wait for the send to finish")
            self.drain(until_sent=True)
        self.assertFalse(self.sending.is_live())
        self.assertEqual(len(srv.uploads), 1)
        up = srv.uploads[0]
        self.assertEqual((up.content, up.filename, up.fields), (gcode, engine_name, {}))
        level, text = self.last()
        self.assertEqual(level, "INFO")
        self.assertTrue(text.startswith(f"Sent {engine_name} to OctoPrint") and text.endswith("(Octo)"), text)
        self.assertEqual(srv.requests[-1].headers["x-api-key"], KEY)
        self.assertEqual(self.issues(), [])
        self.assertIsNone(self.exporting.message(self.scene))                  # the Export panel is not involved ...
        self.assertEqual(self.prefs.last_export_dir, saved_dir)                # ... and no export folder is remembered
        self.assertEqual(list(self.items.values()), [KEY])
        self.assertIsNone(self.sending.progress(self.scene))
        self.assertTrue(bpy.ops.slicewright.send.poll())                        # and it can be done again

    def test_gcode_goes_to_moonraker_with_and_without_a_key(self):
        self.slice()
        gcode = read(self.result().gcode_path)
        with fake_net.FakeMoonraker(MKEY) as keyed, fake_net.FakeMoonraker(None) as open_:
            n1, _ = self.add_printer("moonraker", keyed, secret=MKEY, name="Klipper")
            n2, _ = self.add_printer("moonraker", open_, name="Open")
            self.choose(n1)
            self.send_and_wait()
            self.choose(n2)
            self.send_and_wait()
        self.assertEqual([u.content for u in keyed.uploads + open_.uploads], [gcode, gcode])
        self.assertEqual(keyed.uploads[0].fields, {"root": "gcodes"})
        self.assertEqual(keyed.requests[-1].headers["x-api-key"], MKEY)
        self.assertNotIn("x-api-key", open_.requests[-1].headers)
        self.assertEqual(self.last()[0], "INFO")

    def test_a_typed_name_a_folder_and_a_reverse_proxy_prefix(self):
        self.slice()
        with fake_net.FakeOctoPrint(KEY, prefix="/octoprint") as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY)
            self.choose(name, filename="my case", subfolder="/cases/new/")
            self.send_and_wait()
            self.assertEqual((srv.uploads[0].filename, srv.uploads[0].fields), ("my case.gcode", {"path": "cases/new"}))
            self.assertEqual(srv.requests[-1].target, "/octoprint/api/files/local")
            self.choose(name, filename="a/b", subfolder="")                 # a folder part becomes a harmless "_"
            self.send_and_wait()
            self.assertEqual(srv.uploads[1].filename, "a_b.gcode")
            self.choose(name, filename="fine", subfolder="../up")           # a folder that climbs out is refused up front
            self.assertEqual(bpy.ops.slicewright.send(), {"CANCELLED"})
            self.assertFalse(self.sending.is_live())
            self.assertEqual(len(srv.uploads), 2)

    # -- starting a print ------------------------------------------------------------------------------------

    def test_a_print_starts_only_after_the_confirmation(self):
        self.slice()
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY, name="Octo")
            self.choose(name, start_print=True)
            self.assertEqual(bpy.ops.slicewright.send(), {"CANCELLED"})        # a script call without confirm_print: refused
            self.assertEqual((srv.requests, srv.uploads), ([], []))
            self.assertEqual(self.invoke_send(), {"CANCELLED"})                    # the button: popup answered "yes" in -b
            self.drain(until_sent=True)
            self.assertEqual(srv.uploads[0].fields, {"select": "true", "print": "true"})
            self.assertEqual(srv.printed, srv.uploads[0].filename)
            level, text = self.last()
            self.assertEqual(level, "INFO")
            self.assertIn("started the print", text)
        self.assertEqual(self.sending.state().start_print, True)

    def test_the_print_popup_names_the_printer_and_the_file_and_the_stale_popup_comes_first(self):
        from slicewright.blender.operators import send as send_ops
        self.slice()
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY, name="Voron Bench")
            self.choose(name, start_print=True, filename="benchy")
            shown = {}
            wm = SimpleNamespace(invoke_confirm=lambda op, event, **kw: shown.update(kw) or {"RUNNING_MODAL"})
            context = SimpleNamespace(scene=self.scene, window_manager=wm)
            op = SimpleNamespace(reason="PRINT")
            send_ops.SLICEWRIGHT_OT_send_confirm.invoke(op, context, None)
            self.assertEqual(shown["title"], "Start printing?")
            self.assertIn("benchy.gcode", shown["message"])
            self.assertIn("Voron Bench", shown["message"])
            self.assertIn("START PRINTING", shown["message"])
            self.assertEqual(shown["confirm_text"], "Upload and start print")
            self.assertEqual(srv.requests, [])
            self.edit_plate()                                                 # stale AND print: still refused until allowed
            self.assertEqual(bpy.ops.slicewright.send("EXEC_DEFAULT", confirm_print=True), {"CANCELLED"})
            self.assertEqual(srv.requests, [])
            stale = SimpleNamespace(reason="STALE")
            send_ops.SLICEWRIGHT_OT_send_confirm.invoke(stale, context, None)
            self.assertEqual(shown["title"], "Preview out of date")
            self.assertEqual(shown["confirm_text"], "Send anyway")

    def test_a_busy_printer_refuses_and_the_message_says_so(self):
        self.slice()
        with fake_net.FakeOctoPrint(KEY) as srv:
            srv.busy = True
            name, _ = self.add_printer("octoprint", srv, secret=KEY)
            self.choose(name, start_print=True)
            self.assertEqual(bpy.ops.slicewright.send("EXEC_DEFAULT", confirm_print=True), {"FINISHED"})
            self.drain(until_sent=True)
        level, text = self.last()
        self.assertEqual(level, "ERROR")
        self.assertIn("busy", text)
        self.assertEqual([i["code"] for i in self.issues()], ["send_conflict"])
        self.assertEqual(srv.uploads, [])

    # -- an out-of-date preview ------------------------------------------------------------------------------

    def test_an_out_of_date_slice_is_not_sent_without_confirmation(self):
        self.slice()
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY)
            self.choose(name)
            self.edit_plate()
            self.assertEqual(bpy.ops.slicewright.send("EXEC_DEFAULT"), {"CANCELLED"})
            self.assertEqual(srv.requests, [])
            self.assertEqual(bpy.ops.slicewright.send("EXEC_DEFAULT", allow_stale=True), {"FINISHED"})
            self.drain(until_sent=True)
            self.assertEqual(len(srv.uploads), 1)
            self.edit_plate()
            self.assertEqual(self.invoke_send(), {"CANCELLED"})                               # popup answered yes in -b
            self.drain(until_sent=True)
            self.assertEqual(len(srv.uploads), 2)

    # -- cancelling and failing -----------------------------------------------------------------------------

    def test_cancel_mid_upload_closes_the_connection_and_the_printer_keeps_nothing(self):
        from slicewright.core import export as core_export
        real = core_export.atomic_write

        def padded(dest, write, **kw):                    # a 6 MB file: more than the sockets can buffer, so it is mid-stream
            size = real(dest, write, **kw)
            with open(dest, "ab") as f:
                f.write(b"; padding to make the upload long\n" * 180_000)
            return size
        self.slice()
        patcher = mock.patch.object(core_export, "atomic_write", padded)
        patcher.start()
        self.addCleanup(patcher.stop)
        with fake_net.FakeOctoPrint(KEY, read_delay=0.05, read_chunk=16384) as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY)
            self.choose(name)
            self.assertEqual(bpy.ops.slicewright.send(), {"FINISHED"})
            for _ in range(5):
                self.timers.runner.tick()
                time.sleep(0.005)
            self.assertTrue(self.sending.is_live())
            shown = self.sending.progress(self.scene)
            self.assertTrue(shown is not None and 0.0 < shown[0] < 1.0 and shown[1].startswith("Sending "), shown)
            from slicewright.blender.ui import send_panel
            log = []
            send_panel.draw_send(Layout(log), bpy.context)
            self.assertTrue(any(e[0] == "progress" for e in log))
            self.assertIn(("operator", "slicewright.send_cancel", "", "X", True), log)
            self.assertEqual(bpy.ops.slicewright.send_cancel(), {"FINISHED"})
            self.drain(until_sent=True)
            for _ in range(600):
                if srv.connections_closed_by_client:
                    break
                time.sleep(0.01)
        self.assertEqual(srv.connections_closed_by_client, 1)
        self.assertEqual(srv.uploads, [])
        self.assertEqual(self.last(), ("INFO", "Send cancelled"))
        self.assertFalse(self.sending.is_live())
        self.assertEqual(self.issues(), [])

    def test_failures_land_in_the_status_line_and_the_issues_list_without_the_key(self):
        self.slice()
        cases = []
        with fake_net.FakeOctoPrint("a-different-key-value") as bad_key, \
                fake_net.FakeOctoPrint(KEY) as too_big, fake_net.FakeOctoPrint(KEY) as broken, \
                fake_net.FakeOctoPrint(KEY) as dropped:
            bad_key.echo_secret = too_big.echo_secret = broken.echo_secret = True
            too_big.upload_early_status = 413
            broken.upload_status = 500
            dropped.abort_after = 1000
            for label, srv, expect_text, code in (
                    ("wrong key", bad_key, "rejected the API key", "send_auth_rejected"),
                    ("too big", too_big, "too large", "send_too_large"),
                    ("server error", broken, "HTTP 500", "send_http_status"),
                    ("dropped", dropped, "connection", "send_lost")):
                name, _ = self.add_printer("octoprint", srv, secret=KEY, name=label)
                self.choose(name)
                self.send_and_wait()
                level, text = self.last()
                self.assertEqual(level, "ERROR", label)
                self.assertIn(expect_text, text, label)
                self.assertTrue(text.startswith("Not sent: "), text)
                self.assertEqual([i["code"] for i in self.issues()], [code], label)
                cases.append(text + repr(self.issues()))
            gone = fake_net.free_port()
            name, pid = self.add_printer("octoprint", None, secret=KEY, name="nothing there")
            self.printers.find(pid).port = gone
            self.choose(name)
            self.send_and_wait()
            self.assertIn("refused", self.last()[1])
            cases.append(self.last()[1])
        for text in cases:
            self.assertNotIn(KEY, text)
        for failed in (bad_key, too_big, broken, dropped):
            self.assertEqual(failed.uploads, [])
        self.assertFalse(self.sending.is_live())

    def test_a_missing_secret_in_the_keychain_fails_before_anything_is_sent(self):
        self.slice()
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, pid = self.add_printer("octoprint", srv, secret=KEY)
            self.items.clear()                                              # the keychain lost it
            self.choose(name)
            result = bpy.ops.slicewright.send()
            self.assertEqual(result, {"CANCELLED"})
            self.assertEqual(srv.requests, [])
        self.assertIn("Set the API key", self.last()[1])
        self.assertFalse(self.sending.is_live())

    def test_the_gate_closing_after_the_start_still_stops_the_upload(self):
        self.slice()
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY)
            self.choose(name)
            self.assertEqual(bpy.ops.slicewright.send(), {"FINISHED"})
            self.allow(False)                                               # the user turns online access off meanwhile
            self.drain(until_sent=True)
        self.assertEqual(srv.requests, [])
        self.assertEqual(self.last()[0], "ERROR")
        self.assertIn("Allow Online Access", self.last()[1])

    # -- the .gcode.3mf ------------------------------------------------------------------------------------

    def test_a_3mf_from_a_live_result_is_sent_without_touching_the_export_panel(self):
        self.slice()
        saved_dir = self.prefs.last_export_dir
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY)
            self.choose(name, file_format="3mf")
            self.send_and_wait()
        up = srv.uploads[0]
        self.assertTrue(up.filename.endswith(".gcode.3mf"), up.filename)
        import io
        with zipfile.ZipFile(io.BytesIO(up.content)) as z:
            self.assertIn("Metadata/plate_1.gcode", z.namelist())
        self.assertEqual(self.last()[0], "INFO")
        self.assertIsNone(self.exporting.message(self.scene))
        self.assertEqual(self.prefs.last_export_dir, saved_dir)

    def test_after_a_cache_hit_the_3mf_send_slices_again_first_and_can_be_cancelled(self):
        self.cube.scale = (1.0, 1.0, 4.0 if REAL else 1.0)
        self.cube.location.z = 10.0 * self.cube.scale.z
        bpy.context.view_layer.update()
        first = self.hit()
        with fake_net.FakeMoonraker(None) as srv:
            name, _ = self.add_printer("moonraker", srv)
            self.choose(name, file_format="3mf")
            self.assertEqual(bpy.ops.slicewright.send(), {"FINISHED"})
            self.assertTrue(self.exporting.is_live() and self.sending.is_live())
            self.assertEqual(self.sending.availability(self.scene)[1], "Wait for the send to finish")
            self.assertIsNotNone(self.sending.progress(self.scene))
            self.assertEqual(bpy.ops.slicewright.send_cancel(), {"FINISHED"})        # cancel the re-slice
            self.pump_export()
            self.assertEqual(self.last(), ("INFO", "Send cancelled"))
            self.assertEqual(srv.requests, [])
            self.assertFalse(self.sending.is_live())
            self.assertIsNone(self.exporting.message(self.scene))
            self.assertEqual(bpy.ops.slicewright.send(), {"FINISHED"})
            self.pump_export()
            self.drain(until_sent=True)
        self.assertEqual(self.last()[0], "INFO")
        up = srv.uploads[0]
        self.assertTrue(up.filename.endswith(".gcode.3mf"))
        import io
        with zipfile.ZipFile(io.BytesIO(up.content)) as z:
            self.assertEqual(len(z.read("Metadata/plate_1.gcode")), len(first))
        self.assertIsNone(self.exporting.message(self.scene))

    def test_starting_a_print_from_a_3mf_is_refused_by_the_panel(self):
        self.slice()
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY)
            self.choose(name, file_format="3mf", start_print=True)
            self.assertEqual(self.sending.availability(self.scene),
                             (False, "Starting a print needs a plain G-code file for this printer"))

    # -- certificates ---------------------------------------------------------------------------------------

    def test_an_unknown_certificate_is_trusted_by_the_user_and_the_send_then_works(self):
        from slicewright.core.network import tls
        if not fake_net.have_openssl():
            self.skipTest("openssl is needed for a throw-away certificate")
        import pathlib
        import ssl
        cert, key = fake_net.make_self_signed(pathlib.Path(self.tmp), "dev")
        self.slice()
        with fake_net.FakeOctoPrint(KEY, tls_files=(cert, key)) as srv:
            name, pid = self.add_printer("octoprint", srv, secret=KEY, https=True, tls="pin")
            self.choose(name)
            self.send_and_wait()
            self.assertEqual(srv.requests, [])                              # nothing sent to an untrusted device
            problem = self.printers.tls_problem(pid)
            self.assertEqual(problem.kind, "unknown")
            self.assertIn("not trusted yet", self.last()[1])
            self.assertEqual(problem.fingerprint, tls.fingerprint_of(ssl.PEM_cert_to_DER_cert(open(cert).read())))
            from slicewright.blender.ui import send_panel
            log = []
            send_panel.draw_send(Layout(log), bpy.context)
            self.assertIn("slicewright.printer_trust_cert", [e[1] for e in log if e[0] == "operator"])
            self.assertEqual(bpy.ops.slicewright.printer_trust_cert("EXEC_DEFAULT", printer_id=pid), {"FINISHED"})
            self.send_and_wait()                                            # retried: now it goes through
            self.assertEqual(len(srv.uploads), 1)
            self.assertEqual(self.last()[0], "INFO")

    # -- Bambu LAN mode (FTPS) ------------------------------------------------------------------------------

    def test_a_bambu_3mf_goes_over_ftps_to_a_printer_with_a_trusted_certificate(self):
        """One send per slice here on purpose: the real engine's ``write_gcode_3mf`` called a second time on the same result
        writes a file without ``Metadata/plate_1.gcode`` (found while writing this test; an engine matter, noted in 03 8.4)."""
        import io
        from slicewright.core.network import tls
        import ssl
        (cert, key), _ = self.certs()
        self.slice()
        with fake_net.FakeFtpsServer(BCODE, tls_files=(cert, key), require_session_reuse=True) as srv:
            name, pid = self.add_bambu(srv)
            self.printers.pin_store(self.prefs).set(pid, tls.fingerprint_of(ssl.PEM_cert_to_DER_cert(open(cert).read())))
            self.choose(name)
            self.assertEqual(self.sending.format_of(self.sending.chosen(self.scene)), "3mf")
            self.send_and_wait()
        self.assertEqual(self.last()[0], "INFO", self.last())
        self.assertTrue(self.last()[1].startswith("Sent ") and "storage" in self.last()[1] and "(Bambu)" in self.last()[1])
        (stored,) = srv.stored
        self.assertTrue(stored.clean_end and stored.session_reused)
        self.assertTrue(stored.name.endswith(".gcode.3mf"), stored.name)
        with zipfile.ZipFile(io.BytesIO(stored.content)) as z:
            self.assertIn("Metadata/plate_1.gcode", z.namelist())
        self.assertEqual(srv.commands[:5], ["USER bblp", "PASS ***", "PBSZ 0", "PROT P", "TYPE I"])
        self.assertFalse(any(c.startswith(("MKD", "CWD")) for c in srv.commands))        # the root, no folder
        self.assertEqual(self.issues(), [])
        self.assertEqual(self.last()[1].count(BCODE), 0)
        self.assertEqual(set(self.items.values()), {BCODE})                              # only the keychain holds it

    def test_a_refused_access_code_stops_with_the_developer_mode_message_and_leaks_nothing(self):
        import ssl
        from slicewright.core.network import tls
        (cert, key), _ = self.certs()
        self.slice()
        with fake_net.FakeFtpsServer("another-code-0123456789", tls_files=(cert, key)) as srv:
            name, pid = self.add_bambu(srv)
            self.choose(name)
            self.send_and_wait()                                                   # unknown certificate first
            self.assertEqual((srv.commands, bytes(srv.raw_received)), ([], b""))      # nothing, not even USER, before trust
            problem = self.printers.tls_problem(pid)
            self.assertEqual(problem.kind, "unknown")
            self.assertEqual(problem.fingerprint, tls.fingerprint_of(ssl.PEM_cert_to_DER_cert(open(cert).read())))
            self.assertIn("not trusted yet", self.last()[1])
            self.trust(pid)
            self.send_and_wait()
        level, text = self.last()
        self.assertEqual(level, "ERROR")
        self.assertIn("refused the login", text)
        self.assertIn("Developer Mode", text)
        self.assertEqual([i["code"] for i in self.issues()], ["send_login_rejected"])
        self.assertEqual(srv.logins, [("bblp", False)])                                # one attempt: no retry, no fallback
        self.assertFalse(any(c.startswith(("PBSZ", "STOR")) for c in srv.commands))
        for shown in (text, repr(self.issues())):
            self.assertNotIn(BCODE, shown)
            self.assertNotIn("another-code-0123456789", shown)

    def test_a_different_certificate_than_the_trusted_one_gets_neither_the_access_code_nor_the_file(self):
        import ssl
        from slicewright.core.network import tls
        (cert_a, _key_a), (cert_b, key_b) = self.certs()
        self.slice()
        with fake_net.FakeFtpsServer(BCODE, tls_files=(cert_b, key_b)) as imposter:
            name, pid = self.add_bambu(imposter)
            self.printers.pin_store(self.prefs).set(pid, tls.fingerprint_of(ssl.PEM_cert_to_DER_cert(open(cert_a).read())))
            self.choose(name)
            self.send_and_wait()
            problem = self.printers.tls_problem(pid)
            self.assertEqual(problem.kind, "mismatch")
            level, text = self.last()
            self.assertEqual(level, "ERROR")
            self.assertIn("different certificate", text)
            self.assertEqual([i["code"] for i in self.issues()], ["send_tls_pin_mismatch"])
            self.assertEqual((imposter.commands, imposter.logins, bytes(imposter.raw_received), imposter.stored), ([], [], b"", []))
            log = []
            from slicewright.blender.ui import send_panel
            send_panel.draw_send(Layout(log), bpy.context)
            ops = [e[1] for e in log if e[0] == "operator"]
            self.assertIn("slicewright.printer_forget_pin", ops)
            self.assertNotIn("slicewright.printer_trust_cert", ops)                   # never offered over a pin
            name2, pid2 = self.add_bambu(imposter, name="Other")
            self.choose(name2)
            self.connections.start_test(pid2)
            self.drain()
        self.assertNotIn(BCODE.encode(), bytes(imposter.raw_received))

    def test_cancel_mid_upload_to_a_bambu_printer_aborts_the_data_connection(self):
        from slicewright.core import send as core_send
        (cert, key), _ = self.certs()
        self.slice()
        real = core_send.upload_gen

        def padded(record, ep, file_path, *a, **kw):              # 6 MB: more than the sockets can buffer
            with open(file_path, "ab") as f:
                f.write(b"\0" * 6_000_000)
            return real(record, ep, file_path, *a, **kw)
        patcher = mock.patch.object(core_send, "upload_gen", padded)
        patcher.start()
        self.addCleanup(patcher.stop)
        with fake_net.FakeFtpsServer(BCODE, tls_files=(cert, key), read_delay=0.01) as srv:
            name, pid = self.add_bambu(srv)
            self.choose(name)
            self.send_and_wait()
            self.trust(pid)
            self.assertEqual(bpy.ops.slicewright.send(), {"FINISHED"})
            for _ in range(500):                                        # login, PASV, STOR and the TLS handshakes first
                self.timers.runner.tick()
                shown = self.sending.progress(self.scene)
                if shown is not None and shown[0] > 0.0:
                    break
                time.sleep(0.005)
            self.assertTrue(self.sending.is_live())
            self.assertTrue(shown is not None and 0.0 < shown[0] < 1.0 and shown[1].startswith("Sending "), shown)
            self.assertEqual(bpy.ops.slicewright.send_cancel(), {"FINISHED"})
            self.drain(until_sent=True)
            for _ in range(800):
                if srv.aborted_transfers:
                    break
                time.sleep(0.01)
        self.assertEqual(srv.aborted_transfers, 1)
        self.assertTrue(all(not s.clean_end for s in srv.stored))                       # a truncation, never an end-of-file
        self.assertEqual(self.last(), ("INFO", "Send cancelled"))
        self.assertFalse(self.sending.is_live())
        self.assertEqual(self.issues(), [])

    def test_the_bambu_connection_test_stops_at_an_untrusted_certificate_before_sending_anything(self):
        (cert, key), _ = self.certs()
        with fake_net.FakeFtpsServer(BCODE, tls_files=(cert, key)) as srv:
            _, pid = self.add_bambu(srv)
            self.assertEqual(bpy.ops.slicewright.printer_test("EXEC_DEFAULT", printer_id=pid), {"FINISHED"})
            self.drain()
            state = self.connections.state(pid)
            self.assertEqual(state.level, "ERROR")
            self.assertIn("not trusted yet", state.text)
            self.assertEqual((srv.commands, bytes(srv.raw_received)), ([], b""))
            self.assertEqual(self.printers.tls_problem(pid).kind, "unknown")

    def test_the_panel_says_bambu_mode_is_unsupported_hides_the_folder_and_offers_the_ams_choices_with_a_print(self):
        from slicewright.blender.ui import send_panel
        with fake_net.FakeFtpsServer(BCODE, tls_files=self.certs()[0]) as srv:
            name, _ = self.add_bambu(srv)
            self.choose(name)
            log = []

            class Recording(Layout):
                def column(self, align=False):
                    sub = Recording(self.log)
                    sub.enabled = self.enabled
                    return sub
                row = column

                def prop(self, data, name, text=None, icon="NONE", placeholder=None):
                    self.log.append(("prop", name, self.enabled))
            send_panel.draw_send(Recording(log), bpy.context)
        self.assertIn("Bambu LAN mode (unsupported by Bambu)", labels(log))
        props = {e[1]: e[2] for e in log if e[0] == "prop"}
        self.assertNotIn("subfolder", props)
        self.assertEqual(props["start_print"], True)
        self.assertNotIn("bambu_use_ams", props)                                         # only once a print is asked for
        with fake_net.FakeFtpsServer(BCODE, tls_files=self.certs()[0]) as srv:
            self.choose(name, start_print=True)
            log = []
            send_panel.draw_send(Recording(log), bpy.context)
        self.assertTrue({"bambu_use_ams", "bambu_bed_leveling"} <= {e[1] for e in log if e[0] == "prop"})

    # -- Bambu LAN mode (MQTT: status and starting a print) ----------------------------------------------------

    def bambu_pair(self, **broker_kw):
        """A fake FTPS server and MQTT broker with one throw-away certificate, the printer record pointed at them with its
        certificate already trusted, quick print timings, and the MQTT port (fixed at 8883 for a real printer) redirected."""
        import dataclasses
        import ssl
        from slicewright.core import send as core_send
        from slicewright.core.network import tls
        (cert, key), _ = self.certs()
        ftps = fake_net.FakeFtpsServer(BCODE, tls_files=(cert, key))
        broker = fake_net.FakeMqttBroker(BCODE, serial=SERIAL, tls_files=(cert, key), **broker_kw)
        self.addCleanup(ftps.close)
        self.addCleanup(broker.close)
        name, pid = self.add_bambu(ftps)
        self.printers.pin_store(self.prefs).set(pid, tls.fingerprint_of(ssl.PEM_cert_to_DER_cert(open(cert).read())))
        if not hasattr(self, "_real_bambu"):                        # the originals, kept across several pairs in one test
            self._real_bambu = (core_send.bambu_endpoint, self.sending._bambu_options)
        real, real_options = self._real_bambu
        patcher = mock.patch.object(core_send, "bambu_endpoint", lambda *a, **kw: real(*a, mqtt_port=broker.port, **kw))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(self.sending, "_bambu_options", lambda *a: dataclasses.replace(
            real_options(*a), settle=0.0, status_wait=1.0, confirm_wait=1.5))
        patcher.start()
        self.addCleanup(patcher.stop)
        return name, pid, ftps, broker

    def project_file(self, broker):
        return [p.json()["print"] for p in broker.published if "print" in p.json()]

    def test_a_bambu_print_starts_only_after_the_confirmation_and_is_reported_only_from_the_printers_report(self):
        if not len(self.sw.filaments):                                              # (the real printer preset brings its own)
            self.sw.filaments.add()
        self.sw.filaments[0].ams_slot = 2
        self.slice()
        name, pid, ftps, broker = self.bambu_pair()
        self.choose(name, start_print=True, bambu_use_ams=True)
        self.assertEqual(bpy.ops.slicewright.send(), {"CANCELLED"})                 # a script call without confirm_print
        self.assertEqual((ftps.commands, broker.connects, bytes(ftps.raw_received)), ([], [], b""))
        self.assertEqual(self.invoke_send(), {"CANCELLED"})                         # the button: the popup answers "yes" in -b
        self.drain(until_sent=True)
        level, text = self.last()
        self.assertEqual(level, "INFO", text)
        self.assertIn("the printer reports it is printing it", text)
        self.assertEqual(len(ftps.stored), 1)
        (cmd,) = self.project_file(broker)
        self.assertEqual(cmd["command"], "project_file")
        self.assertEqual(cmd["url"], f"ftp:///{ftps.stored[0].name}")
        self.assertTrue(cmd["use_ams"] and cmd["bed_leveling"])
        self.assertEqual(cmd["ams_mapping"][0], 2)                                  # the slot's own AMS tray
        self.assertEqual(len(cmd["ams_mapping"]), len(self.sw.filaments))
        self.assertEqual(self.issues(), [])
        self.assertEqual(broker.started_file, ftps.stored[0].name[:-len(".gcode.3mf")])

    def test_a_bambu_print_the_printer_does_not_confirm_is_a_warning_and_the_file_is_kept(self):
        self.slice()
        cases = []
        for script, expect in (({"start": "silent"}, "did not confirm"), ({"start": "reject"}, "refused the print command"),
                               ({"gcode_state": "RUNNING"}, "busy (RUNNING)")):
            self.sending.set_message(self.scene, None)
            name, pid, ftps, broker = self.bambu_pair()
            for key, value in script.items():
                setattr(broker, key, value)
            self.choose(name, start_print=True)
            self.send_and_wait(confirm_print=True)
            level, text = self.last()
            self.assertEqual(level, "WARNING", text)
            self.assertIn("The printer did not start the print", text)
            self.assertIn(expect, text)
            self.assertEqual(len(ftps.stored), 1)
            cases.append(text)
            self.assertEqual(bpy.ops.slicewright.printer_remove("EXEC_DEFAULT", printer_id=pid), {"FINISHED"})
        for text in cases:
            self.assertNotIn(BCODE, text)

    def test_a_refused_mqtt_login_after_the_upload_stops_and_points_to_developer_mode(self):
        self.slice()
        name, pid, ftps, broker = self.bambu_pair()
        broker.access_code = "another-code-0123456789"
        self.choose(name, start_print=True)
        self.send_and_wait(confirm_print=True)
        level, text = self.last()
        self.assertEqual(level, "WARNING", text)
        self.assertIn("Developer Mode", text)
        self.assertEqual(len(ftps.stored), 1)
        self.assertEqual(len(broker.connects), 1)                                    # no retry
        self.assertEqual(broker.published, [])
        self.assertNotIn(BCODE, text)

    def test_the_bambu_print_popup_names_the_printer_and_the_file(self):
        from slicewright.blender.operators import send as send_ops
        self.slice()
        name, pid, ftps, broker = self.bambu_pair()
        self.printers.find(pid).name = "Workshop A1"
        self.choose("Workshop A1", start_print=True, filename="benchy")
        shown = {}
        wm = SimpleNamespace(invoke_confirm=lambda op, event, **kw: shown.update(kw) or {"RUNNING_MODAL"})
        context = SimpleNamespace(scene=self.scene, window_manager=wm)
        send_ops.SLICEWRIGHT_OT_send_confirm.invoke(SimpleNamespace(reason="PRINT"), context, None)
        self.assertEqual(shown["title"], "Start printing?")
        self.assertIn("benchy.gcode.3mf", shown["message"])
        self.assertIn("Workshop A1", shown["message"])
        self.assertIn("START PRINTING", shown["message"])
        self.assertEqual((ftps.commands, broker.connects), ([], []))

    def test_the_bambu_connection_test_reports_state_and_firmware_and_a_wrong_serial_number(self):
        name, pid, ftps, broker = self.bambu_pair()
        self.assertEqual(bpy.ops.slicewright.printer_test("EXEC_DEFAULT", printer_id=pid), {"FINISHED"})
        self.drain()
        state = self.connections.state(pid)
        self.assertEqual(state.level, "INFO", state.text)
        self.assertEqual(state.text, "Bambu LAN mode (unsupported by Bambu): access code accepted (FTPS and MQTT), certificate "
                         "trusted, printer state IDLE, firmware 01.08.00.00, storage reachable (2 entries in its root)")
        self.printers.find(pid).serial = "01P00AWRONG0000"                            # a serial the printer does not know
        broker.answer_pushall = broker.answer_version = False
        with mock.patch("slicewright.core.network.bambu.PrintOptions.status_wait", 0.3):
            self.assertEqual(bpy.ops.slicewright.printer_test("EXEC_DEFAULT", printer_id=pid), {"FINISHED"})
            self.drain()
        self.assertIn("sent no status for serial number 01P00AWRONG0000", self.connections.state(pid).text)

    def test_a_bambu_login_refused_by_the_test_says_developer_mode_and_nothing_leaks(self):
        name, pid, ftps, broker = self.bambu_pair()
        ftps.password = "other-code-0123456789"
        bpy.ops.slicewright.printer_test("EXEC_DEFAULT", printer_id=pid)
        self.drain()
        state = self.connections.state(pid)
        self.assertEqual(state.level, "ERROR")
        self.assertIn("Developer Mode", state.text)
        self.assertNotIn(BCODE, state.text)
        self.assertEqual(len(ftps.logins), 1)

    # -- the connection test --------------------------------------------------------------------------------

    def test_the_connection_test_reports_what_the_printer_said_and_why_it_failed(self):
        with fake_net.FakeOctoPrint(KEY) as ok, fake_net.FakeOctoPrint("another-key-value-0123") as bad, \
                fake_net.FakeMoonraker(None) as moon:
            ok.state = "Printing"
            _, a = self.add_printer("octoprint", ok, secret=KEY)
            _, b = self.add_printer("octoprint", bad, secret=KEY)
            _, c = self.add_printer("moonraker", moon)
            for pid in (a, b, c):
                self.assertEqual(bpy.ops.slicewright.printer_test("EXEC_DEFAULT", printer_id=pid), {"FINISHED"})
                self.assertTrue(self.connections.is_running(pid))
                self.assertFalse(self.connections.start_test(pid) is None)       # not twice at once
            self.drain()
        self.assertEqual(self.connections.state(a).level, "INFO")
        self.assertEqual(self.connections.state(a).text,
                         "OctoPrint 1.10.2 (API 0.1), API key accepted, printer: Printing")
        self.assertEqual(self.connections.state(b).level, "ERROR")
        self.assertIn("rejected the API key", self.connections.state(b).text)
        self.assertNotIn(KEY, self.connections.state(b).text)
        self.assertEqual(self.connections.state(c).level, "INFO")
        self.assertIn("Klipper: ready", self.connections.state(c).text)
        self.assertEqual([r.headers["x-api-key"] for r in ok.requests], [KEY, KEY])

    def test_the_connection_test_is_also_available_from_the_printers_box_and_shows_its_result(self):
        from slicewright.blender.ui import printers_box
        with fake_net.FakeOctoPrint(KEY) as srv:
            _, pid = self.add_printer("octoprint", srv, secret=KEY)
            bpy.ops.slicewright.printer_test("EXEC_DEFAULT", printer_id=pid)
            self.drain()
        box = mock.MagicMock()
        printers_box.draw(box, self.prefs)
        text = " ".join(str(c) for c in box.mock_calls)
        self.assertIn("printer_test", text)
        self.assertIn("API key accepted", text)

    # -- the panel ------------------------------------------------------------------------------------------

    def test_the_panel_draws_the_choices_the_reason_and_the_last_result(self):
        from slicewright.blender.ui import send_panel
        log = []
        send_panel.draw_send(Layout(log), bpy.context)
        self.assertIn("Add a printer in the add-on preferences first", labels(log))
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY)
            self.choose(name)
            log.clear()
            send_panel.draw_send(Layout(log), bpy.context)
            self.assertIn("Slice the plate to send it", labels(log))
            self.assertTrue({"printer", "file_format", "filename", "subfolder", "start_print"} <= {e[1] for e in log if e[0] == "prop"})
            ops = [e for e in log if e[0] == "operator"]
            self.assertEqual([e[1] for e in ops], ["slicewright.send", "slicewright.printer_test"])
            self.slice()
            self.sending.set_message(self.scene, "ERROR", "Not sent: boom")
            log.clear()
            send_panel.draw_send(Layout(log), bpy.context)
            self.assertIn(("label", "Not sent: boom", "ERROR"), log)
            self.assertNotIn("Slice the plate to send it", labels(log))

    # -- life cycle -----------------------------------------------------------------------------------------

    def test_a_file_load_drops_a_send_in_flight_and_its_temp_directory(self):
        self.slice()
        path = os.path.join(self.tmp, "t.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        with fake_net.FakeOctoPrint(KEY, read_delay=0.05, read_chunk=2048) as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY)
            self.choose(name)
            self.assertEqual(bpy.ops.slicewright.send(), {"FINISHED"})
            self.assertTrue(glob.glob(os.path.join(tempfile.gettempdir(), "slicewright-send-*")) )
            bpy.ops.wm.open_mainfile(filepath=path)
            self.assertFalse(self.sending.is_live())
            self.assertIsNone(self.timers.runner.get("send"))
            self.assertEqual(srv.uploads, [])
        self.assertEqual(set(glob.glob(os.path.join(tempfile.gettempdir(), "slicewright-send-*"))) - self.leftovers, set())

    def test_the_choices_live_on_the_window_manager_and_never_in_the_file(self):
        self.slice()
        with fake_net.FakeOctoPrint(KEY) as srv:
            name, _ = self.add_printer("octoprint", srv, secret=KEY)
            self.choose(name, start_print=True, filename="zzz-marker")
        path = os.path.join(self.tmp, "t.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        self.assertNotIn(b"zzz-marker", read(path))
        self.assertFalse(hasattr(self.scene, "slicewright_send"))
        self.assertTrue(hasattr(bpy.types.WindowManager, "slicewright_send"))
        self.addon.unregister()
        self.assertFalse(hasattr(bpy.types.WindowManager, "slicewright_send"))


if __name__ == "__main__":
    bl_common.run("__main__")
