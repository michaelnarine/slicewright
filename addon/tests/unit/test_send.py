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


def test_octoprint_and_moonraker_take_plain_gcode_by_default():
    for kind in p.KINDS:
        assert send.default_format(kind) == send.GCODE
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
    assert send.send_blocker(rec(p.BAMBU_LAN, options={"serial": "01P00A123456789"})) == (
        "Sending to Bambu printers is not available yet")
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


def test_the_generators_dispatch_by_kind_and_refuse_bambu(tmp_path):
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
    bambu = rec(p.BAMBU_LAN, options={"serial": "01P00A123456789"})
    with pytest.raises(ValueError, match="not available yet"):
        send.test_gen(bambu, ep(octo, KEY))
    with pytest.raises(ValueError, match="not available yet"):
        send.upload_gen(bambu, ep(octo, KEY), str(f), "a.gcode")
    assert os.path.exists(f)
