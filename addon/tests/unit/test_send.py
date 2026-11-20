# SPDX-License-Identifier: GPL-3.0-or-later
"""core/send.py: which printers are offered, the file name on the printer, and the dispatch to the clients."""
from __future__ import annotations

import os

import pytest
from fake_net import FakeMoonraker, FakeOctoPrint
from slicewright.core import printers as p
from slicewright.core import send
from slicewright.core.network import errors
from slicewright.core.network.nbsocket import Timeouts, run_blocking
from slicewright.core.network.webapi import Endpoint, Transfer
from slicewright.core.redact import SecretStr

KEY = "SEND-KEY-0123456789abcdef"


def rec(kind=p.OCTOPRINT, **kw):
    base = dict(kind=kind, host="octopi.local", secret_saved=kind != p.MOONRAKER or kw.get("secret_saved", False))
    base.update(kw)
    return p.new_record(**base)


def test_only_printers_paired_with_the_preset_or_unpaired_are_offered_unless_all_are_asked_for():
    a, b, c = rec(name="A", preset="mine"), rec(name="B", preset="other"), rec(name="C")
    assert [r.name for r in send.eligible_printers([a, b, c], "mine")] == ["A", "C"]
    assert [r.name for r in send.eligible_printers([a, b, c], "other")] == ["B", "C"]
    assert [r.name for r in send.eligible_printers([a, b, c], "mine", show_all=True)] == ["A", "B", "C"]
    assert send.eligible_printers([], "mine") == []


def test_octoprint_and_moonraker_take_plain_gcode_by_default_and_a_bambu_printer_a_gcode_3mf():
    for kind in (p.OCTOPRINT, p.MOONRAKER):
        assert send.default_format(kind) == send.GCODE
    assert send.default_format(p.BAMBU_LAN) == send.THREEMF
    assert send.resolve_format(p.BAMBU_LAN, send.AUTO) == send.THREEMF
    assert send.resolve_format(p.BAMBU_LAN, send.GCODE) == send.GCODE          # chosen: refused later by options_blocker
    assert send.SUPPORTED == (p.OCTOPRINT, p.MOONRAKER, p.BAMBU_LAN)
    assert send.resolve_format(p.OCTOPRINT, send.AUTO) == send.GCODE
    assert send.resolve_format(p.MOONRAKER, "") == send.GCODE
    assert send.resolve_format(p.OCTOPRINT, send.THREEMF) == send.THREEMF


@pytest.mark.parametrize("engine,fmt,typed,expected", [
    ("part_0.2mm_PLA_20m.gcode", send.GCODE, "", "part_0.2mm_PLA_20m.gcode"),
    ("part_0.2mm_PLA_20m.gcode", send.THREEMF, "", "part_0.2mm_PLA_20m.gcode.3mf"),
    ("part.gcode", send.GCODE, "my case", "my case.gcode"),
    ("part.gcode", send.GCODE, "my case.gco", "my case.gco"),
    ("part.gcode", send.THREEMF, "my case", "my case.gcode.3mf"),
    ("part.gcode", send.GCODE, "a/b:c", "a_b_c.gcode"),
    ("part.gcode", send.GCODE, "  ", "part.gcode"),
    ("part.gcode", send.GCODE, "CON", "_CON.gcode"),
])
def test_the_file_name_on_the_printer(engine, fmt, typed, expected):
    assert send.target_filename(engine, fmt, typed) == expected


def test_what_stops_a_send_is_said_in_words():
    assert send.send_blocker(None) == "Choose a printer to send to"
    assert send.send_blocker(rec(host="")) == "Enter the printer's host name or IP address"
    assert send.send_blocker(rec(secret_saved=False)) == "Set the API key"
    assert send.send_blocker(rec(p.MOONRAKER)) is None                      # the Moonraker key is optional
    bambu = rec(p.BAMBU_LAN, options={"serial": "01P00A123456789"})
    assert send.send_blocker(bambu) is None
    assert send.send_blocker(rec(p.BAMBU_LAN)) == "A Bambu printer needs its serial number to be sent to"
    assert send.send_blocker(rec(p.BAMBU_LAN, options={"serial": "01P00A123456789"}, secret_saved=False)) == (
        "Set the access code")
    assert send.options_blocker(bambu, send.THREEMF) is None
    assert send.options_blocker(bambu, send.GCODE) == "A Bambu printer takes a .gcode.3mf file: choose that file type"
    assert send.options_blocker(bambu, send.THREEMF, "cases") == "A Bambu printer stores files in its root: clear the Folder field"
    assert send.options_blocker(rec(), send.GCODE, "cases") is None and send.options_blocker(None, send.GCODE) is None
    assert send.start_print_blocker(bambu, send.THREEMF) is None
    assert send.start_print_blocker(bambu, send.GCODE) == "A Bambu printer prints only a .gcode.3mf file"
    assert send.send_blocker(rec()) is None
    assert send.start_print_blocker(rec(), send.THREEMF) == "Starting a print needs a plain G-code file for this printer"
    assert send.start_print_blocker(rec(), send.GCODE) is None


def test_the_issue_for_a_failed_send_has_the_code_of_the_failure_and_no_secret():
    exc = errors.AuthRejected(f"OctoPrint at http://h rejected the API key (HTTP 403)", status=403)
    issue = send.issue_for(exc, "Voron", "a.gcode")
    assert issue["level"] == "error" and issue["code"] == "send_auth_rejected"
    assert issue["message"] == "Sending a.gcode to Voron failed: OctoPrint at http://h rejected the API key (HTTP 403)"
    assert send.issue_for(RuntimeError("boom"), "V", "a.gcode")["code"] == "send_error"
    assert {"opt_key", "object_name"} <= set(issue)


def test_progress_text_follows_the_phase():
    t = Transfer()
    assert send.progress_text(t, "a.gcode") == "Connecting to send a.gcode…"
    t.update(1_500_000, 4_000_000)
    assert send.progress_text(t, "a.gcode") == "Sending a.gcode: 1.5 MB of 4.0 MB"
    t.update(4000, 4000)
    assert send.progress_text(t, "a.gcode") == "Waiting for the printer to accept a.gcode…"
    assert send.size_text(500) == "1 kB" and send.size_text(2_500_000) == "2.5 MB"


def test_clean_temp_dir_removes_only_what_it_is_given(tmp_path):
    d = tmp_path / "slicewright-send-x"
    d.mkdir()
    (d / "send.gcode").write_bytes(b"x")
    keep = tmp_path / "keep.txt"
    keep.write_text("x")
    send.clean_temp_dir(str(d))
    assert not d.exists() and keep.exists()
    send.clean_temp_dir(str(d))                        # again, and with nothing: never raises
    send.clean_temp_dir(None)


def test_the_generators_dispatch_by_kind(tmp_path, certs):
    f = tmp_path / "a.gcode"
    f.write_bytes(b"G1 X1\n" * 100)
    ep = lambda srv, key: Endpoint(f"http://127.0.0.1:{srv.port}", SecretStr(key) if key else None,   # noqa: E731
                                   timeouts=Timeouts(3, 3), upload_timeouts=Timeouts(3, 3))
    with FakeOctoPrint(KEY) as octo, FakeMoonraker(None) as moon:
        r1 = rec(p.OCTOPRINT)
        r2 = rec(p.MOONRAKER)
        assert run_blocking(send.test_gen(r1, ep(octo, KEY))).product == "OctoPrint"
        assert run_blocking(send.test_gen(r2, ep(moon, None))).product == "Moonraker"
        assert run_blocking(send.upload_gen(r1, ep(octo, KEY), str(f), "a.gcode", subfolder="x")).path == "x/a.gcode"
        assert run_blocking(send.upload_gen(r2, ep(moon, None), str(f), "a.gcode", start_print=True)).printing
    with pytest.raises(ValueError, match="unsupported"):
        send.test_gen(rec(kind="nope"), ep(octo, KEY))
    with pytest.raises(ValueError, match="unsupported"):
        send.upload_gen(rec(kind="nope"), ep(octo, KEY), str(f), "a.gcode")
    assert os.path.exists(f)


def test_the_ams_mapping_has_one_tray_per_filament_slot_and_minus_one_for_unused_ones():
    assert send.ams_mapping([-1, -1, -1]) == (0, 1, 2)                       # automatic: the slot's own index
    assert send.ams_mapping([3, -1, 0], [True, True, True]) == (3, 1, 0)
    assert send.ams_mapping([3, -1, 0], [True, False, True]) == (3, -1, 0)   # a filament the plate does not use
    assert send.ams_mapping([2, 1], [False]) == (-1, 1)                      # a short "used" list: the rest count as used
    assert send.ams_mapping([]) == ()


def test_the_bambu_generators_use_a_pinning_endpoint_built_from_the_record(tmp_path, certs):
    import ssl
    from fake_net import FakeFtpsServer, FakeMqttBroker
    from slicewright.core.network import bambu
    from slicewright.core.network.tls import fingerprint_of
    f = tmp_path / "a.gcode.3mf"
    f.write_bytes(b"PK" * 5000)
    record = rec(p.BAMBU_LAN, port=0, options={"serial": "01P00A123456789"})
    store = p.record_pin_store(record)
    store.set(record.id, fingerprint_of(ssl.PEM_cert_to_DER_cert(open(certs[0][0]).read())))
    quick = bambu.PrintOptions(settle=0.0, status_wait=1.0, confirm_wait=1.5)
    with FakeFtpsServer("AC-12345678", tls_files=certs[0]) as srv, \
            FakeMqttBroker("AC-12345678", serial="01P00A123456789", tls_files=certs[0]) as broker:
        record.port = srv.port
        ep = send.bambu_endpoint(record, SecretStr("AC-12345678"), store, timeouts=Timeouts(3, 3),
                                 upload_timeouts=Timeouts(3, 3))
        assert (ep.host, ep.port, ep.serial, ep.tls.mode, ep.tls.pin_key) == ("octopi.local", srv.port, "01P00A123456789",
                                                                              "pin", record.id)
        ep = bambu.BambuEndpoint("127.0.0.1", ep.tls, ep.access_code, ep.serial, srv.port, broker.port,
                                 timeouts=Timeouts(3, 3), upload_timeouts=Timeouts(3, 3))
        info = run_blocking(send.test_gen(record, ep))
        assert info.product == "Bambu printer" and info.data_ok and info.state == "IDLE"
        result = run_blocking(send.upload_gen(record, ep, str(f), "a.gcode.3mf"))
        assert (result.name, result.size, result.printing) == ("a.gcode.3mf", 10000, False)
        assert broker.connects[1:] == []                                     # the test connected once; the upload not at all
        started = run_blocking(send.upload_gen(record, ep, str(f), "b.gcode.3mf", start_print=True, print_options=quick))
        assert started.printing and started.selected
    assert [s.name for s in srv.stored] == ["a.gcode.3mf", "b.gcode.3mf"]
    assert srv.stored[0].content == b"PK" * 5000
    with pytest.raises(ValueError):                                          # never an endpoint that does not pin
        bambu.BambuEndpoint("h", p.TlsPolicy(mode="verify"), None)
