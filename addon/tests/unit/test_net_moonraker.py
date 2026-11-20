# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network/moonraker.py against the in-process fake Moonraker (tests/fake_net.py)."""
from __future__ import annotations

import logging
import threading

import fake_net
import pytest
from fake_net import FakeMoonraker
from slicewright.core import printers as core_printers, redact
from slicewright.core.network import errors, moonraker
from slicewright.core.network.nbsocket import CancelToken, Timeouts, run_blocking
from slicewright.core.network.webapi import Endpoint, Transfer
from slicewright.core.redact import SecretStr
from slicewright.core.ticking import DONE, TickRunner

KEY = "MOON-KEY-0123456789abcdef"
FAST = Timeouts(connect=3.0, idle=3.0)


def endpoint(srv, key=KEY, **kw):
    kw.setdefault("timeouts", FAST)
    kw.setdefault("upload_timeouts", FAST)
    return Endpoint(f"http://127.0.0.1:{srv.port}{srv.prefix}", SecretStr(key) if key else None, **kw)


def gcode(tmp_path, size=100_000, name="part.gcode"):
    path = tmp_path / name
    path.write_bytes((b"G1 X1 Y1 E0.1\n" * (size // 14 + 1))[:size])
    return str(path)


def run(gen):
    return run_blocking(gen)


# -- test_connection ---------------------------------------------------------------------------------------------

def test_connection_reports_versions_klippy_state_and_the_key():
    with FakeMoonraker(KEY) as srv:
        info = run(moonraker.test_connection(endpoint(srv)))
    assert (info.server_version, info.api_version, info.klippy_state, info.ready, info.key_sent) == (
        "v0.9.3-24-g1b2c3d4", "1.0.5", "ready", True, True)
    assert info.summary() == "Moonraker v0.9.3-24-g1b2c3d4 (API 1.0.5), API key accepted, Klipper: ready"
    assert [r.target for r in srv.requests] == ["/server/info", "/printer/info"]
    assert all(r.headers["x-api-key"] == KEY for r in srv.requests)


def test_without_a_configured_key_no_key_header_is_sent(tmp_path):
    with FakeMoonraker(None) as srv:
        info = run(moonraker.test_connection(endpoint(srv, key=None)))
        run(moonraker.upload(endpoint(srv, key=None), gcode(tmp_path), "a.gcode"))
    assert not info.key_sent and "no API key set" in info.summary()
    assert all("x-api-key" not in r.headers for r in srv.requests)


def test_klipper_not_ready_is_reported_not_an_error():
    with FakeMoonraker(KEY) as srv:
        srv.klippy_state = "shutdown"
        info = run(moonraker.test_connection(endpoint(srv)))        # /printer/info answers 503: only the nicety is lost
    assert (info.klippy_state, info.ready, info.state_message) == ("shutdown", False, "")
    assert info.summary().endswith("Klipper: shutdown")
    with FakeMoonraker(KEY) as srv:
        srv.klippy_state = "error"
        assert "Klipper: error" in run(moonraker.test_connection(endpoint(srv))).summary()


@pytest.mark.parametrize("key,sent,expect", [("other-key-value", KEY, "rejected the API key"),
                                             ("other-key-value", None, "has no API key set")])
def test_401_is_a_clear_auth_error(key, sent, expect):
    with FakeMoonraker(key) as srv:
        srv.echo_secret = True
        with pytest.raises(errors.AuthRejected, match=expect) as exc:
            run(moonraker.test_connection(endpoint(srv, key=sent)))
    assert exc.value.status == 401 and KEY not in str(exc.value)


def test_something_else_on_the_port_is_not_moonraker():
    with FakeMoonraker(KEY) as srv:
        srv.not_moonraker = True
        with pytest.raises(errors.NotThisServer, match="not like Moonraker"):
            run(moonraker.test_connection(endpoint(srv)))
    with FakeMoonraker(KEY, prefix="/klipper") as srv:                 # forgot the proxy prefix
        with pytest.raises(errors.HttpStatusError, match="base path"):
            run(moonraker.test_connection(Endpoint(f"http://127.0.0.1:{srv.port}", SecretStr(KEY), timeouts=FAST)))
        assert run(moonraker.test_connection(endpoint(srv))).ready


def test_transport_errors_pass_through():
    ep = Endpoint(f"http://127.0.0.1:{fake_net.free_port()}", None, timeouts=FAST)
    with pytest.raises(errors.ConnectionRefused):
        run(moonraker.test_connection(ep))


# -- upload ------------------------------------------------------------------------------------------------------

def test_upload_sends_root_gcodes_and_the_file_and_nothing_else(tmp_path):
    path = gcode(tmp_path, 700_000)
    transfer = Transfer()
    with FakeMoonraker(KEY) as srv:
        result = run(moonraker.upload(endpoint(srv), path, "my part.gcode", transfer=transfer))
    up = srv.uploads[0]
    assert up.content == open(path, "rb").read() and up.filename == "my part.gcode"
    assert up.fields == {"root": "gcodes"}                              # no print, no path
    assert (result.name, result.path, result.size, result.printing) == ("my part.gcode", "my part.gcode", 700_000, False)
    rec = srv.requests[-1]
    assert (rec.method, rec.target) == ("POST", "/server/files/upload") and rec.headers["x-api-key"] == KEY
    assert (transfer.phase, transfer.sent) == ("done", transfer.total)


def test_upload_to_a_folder_and_print_only_when_asked(tmp_path):
    with FakeMoonraker(KEY) as srv:
        run(moonraker.upload(endpoint(srv), gcode(tmp_path), "a.gcode", subfolder="cases/new"))
        assert not srv.print_requested and srv.printing_file is None
        result = run(moonraker.upload(endpoint(srv), gcode(tmp_path), "b.gcode", start_print=True))
    assert [u.fields for u in srv.uploads] == [{"root": "gcodes", "path": "cases/new"}, {"root": "gcodes", "print": "true"}]
    assert result.printing and result.summary() == "Sent b.gcode to Moonraker and started the print"


def test_truthy_but_not_true_never_starts_a_print(tmp_path):
    with FakeMoonraker(KEY) as srv:
        run(moonraker.upload(endpoint(srv), gcode(tmp_path), "a.gcode", start_print="yes"))   # type: ignore[arg-type]
    assert "print" not in srv.uploads[0].fields


def test_a_print_that_moonraker_did_not_start_is_reported_as_not_started(tmp_path):
    with FakeMoonraker(KEY) as srv:
        srv.klippy_state = "shutdown"
        result = run(moonraker.upload(endpoint(srv), gcode(tmp_path), "a.gcode", start_print=True))
    assert srv.print_requested and not result.printing and "started" not in result.summary()


def test_both_documented_answer_shapes_are_read(tmp_path):
    with FakeMoonraker(KEY) as srv:
        srv.wrap_in_result = True
        wrapped = run(moonraker.upload(endpoint(srv), gcode(tmp_path), "a.gcode", subfolder="x"))
    assert wrapped.path == "x/a.gcode"


def test_an_unparseable_success_body_claims_nothing(tmp_path):
    with FakeMoonraker(KEY) as srv:
        srv._stored = lambda up: fake_net.Reply(201, "Created", body=b"<html>proxy</html>")
        result = run(moonraker.upload(endpoint(srv), gcode(tmp_path), "a.gcode", start_print=True))
    assert (result.path, result.printing) == ("a.gcode", False)


def test_replacing_the_file_being_printed_is_403_with_a_hint_that_is_not_only_about_the_key(tmp_path):
    with FakeMoonraker(KEY) as srv:
        srv.printing_file = "a.gcode"
        with pytest.raises(errors.AuthRejected, match="currently being printed") as exc:
            run(moonraker.upload(endpoint(srv), gcode(tmp_path), "a.gcode"))
    assert exc.value.status == 403


@pytest.mark.parametrize("status,kind", [(401, errors.AuthRejected), (403, errors.AuthRejected), (409, errors.Conflict),
                                         (500, errors.HttpStatusError), (503, errors.HttpStatusError),
                                         (400, errors.HttpStatusError)])
def test_error_statuses_after_the_body(tmp_path, status, kind):
    with FakeMoonraker(KEY) as srv:
        srv.upload_status = status
        srv.echo_secret = True
        with pytest.raises(kind) as exc:
            run(moonraker.upload(endpoint(srv), gcode(tmp_path), "a.gcode"))
    assert exc.value.status == status and KEY not in str(exc.value) and srv.uploads == []


def test_413_before_the_body_stops_the_upload_early(tmp_path):
    transfer = Transfer()
    with FakeMoonraker(KEY) as srv:
        srv.upload_early_status = 413
        with pytest.raises(errors.PayloadTooLarge):
            run(moonraker.upload(endpoint(srv), gcode(tmp_path, 3_000_000), "big.gcode", transfer=transfer))
    assert transfer.sent < transfer.total


def test_a_wrong_key_on_upload_is_rejected_before_the_body_is_read(tmp_path):
    with FakeMoonraker("another-key-value-xyz") as srv:
        with pytest.raises(errors.AuthRejected):
            run(moonraker.upload(endpoint(srv), gcode(tmp_path, 2_000_000), "a.gcode"))
    assert srv.uploads == []


def test_connection_dropped_mid_upload_and_a_server_that_stops_reading(tmp_path):
    with FakeMoonraker(KEY) as srv:
        srv.abort_after = 200_000
        with pytest.raises(errors.ConnectionLost):
            run(moonraker.upload(endpoint(srv), gcode(tmp_path, 2_000_000), "a.gcode"))
    with FakeMoonraker(KEY) as srv:
        srv.read_delay = 3.0
        with pytest.raises(errors.Timeout):
            run(moonraker.upload(endpoint(srv, upload_timeouts=Timeouts(connect=2, idle=0.4)),
                                 gcode(tmp_path, 4_000_000), "a.gcode"))


def test_bad_names_and_folders_are_refused_before_anything_is_sent(tmp_path):
    path = gcode(tmp_path)
    with FakeMoonraker(KEY) as srv:
        for name in ("", "a/b.gcode", ".hidden.gcode", "x\ny.gcode"):
            with pytest.raises(errors.InvalidRequest):
                run(moonraker.upload(endpoint(srv), path, name))
        for folder in ("../x", "a//b", "a\\b"):
            with pytest.raises(errors.InvalidRequest):
                run(moonraker.upload(endpoint(srv), path, "a.gcode", subfolder=folder))
    assert srv.requests == []


def test_a_proxy_prefix_and_a_non_ascii_name(tmp_path):
    with FakeMoonraker(KEY, prefix="/klipper") as srv:
        run(moonraker.upload(endpoint(srv), gcode(tmp_path), "Gehäuse.gcode"))
    assert srv.uploads[0].filename == "Gehäuse.gcode" and srv.requests[-1].target == "/klipper/server/files/upload"


def test_cancel_mid_upload_and_closing_the_generator(tmp_path):
    token = CancelToken()
    transfer = Transfer()
    with FakeMoonraker(KEY, read_delay=0.02) as srv:
        gen = moonraker.upload(endpoint(srv, cancel=token), gcode(tmp_path, 4_000_000), "a.gcode", transfer=transfer)
        with pytest.raises(errors.Cancelled):
            while True:
                next(gen)
                if transfer.sent > 300_000:
                    token.cancel()
    assert 0 < transfer.sent < transfer.total and srv.uploads == []
    transfer = Transfer()
    with FakeMoonraker(KEY, read_delay=0.02) as srv:
        gen = moonraker.upload(endpoint(srv), gcode(tmp_path, 4_000_000), "a.gcode", transfer=transfer)
        while transfer.sent < 300_000:
            next(gen)
        gen.close()
        for _ in range(100):
            if srv.connections_closed_by_client:
                break
            threading.Event().wait(0.02)
    assert srv.connections_closed_by_client == 1 and srv.uploads == []


def test_the_upload_runs_under_the_tick_runner(tmp_path):
    got = []
    with FakeMoonraker(KEY) as srv:
        runner = TickRunner()
        task = runner.submit("send", moonraker.upload(endpoint(srv), gcode(tmp_path, 3_000_000), "a.gcode"),
                             on_done=got.append)
        runner.run_until_idle()
    assert task.state == DONE and got[0].size == 3_000_000


# -- secrets -----------------------------------------------------------------------------------------------------

def test_the_secret_never_appears_in_urls_errors_logs_or_results(tmp_path, caplog):
    redact.register(KEY)
    seen: list[str] = []
    with FakeMoonraker(KEY) as srv:
        srv.echo_secret = True
        ep = endpoint(srv)
        assert KEY not in repr(ep)
        with caplog.at_level(logging.DEBUG):
            seen.append(run(moonraker.test_connection(ep)).summary())
            result = run(moonraker.upload(ep, gcode(tmp_path), "a.gcode"))
            seen += [repr(result), result.summary()]
            for status in (401, 403, 409, 500):
                srv.upload_status = status
                with pytest.raises(errors.NetError) as exc:
                    run(moonraker.upload(ep, gcode(tmp_path), "a.gcode"))
                seen += [str(exc.value), repr(exc.value)]
            srv.upload_status = None
            srv.api_key = "different-key-123456"
            with pytest.raises(errors.AuthRejected) as exc:
                run(moonraker.test_connection(ep))
            seen += [str(exc.value), repr(exc.value)]
        assert all(KEY not in r.target for r in srv.requests)
    assert all(KEY not in text for text in seen)
    assert KEY not in caplog.text
    redact.clear()


def test_the_endpoint_from_a_record_uses_the_default_port_and_base_path():
    rec = core_printers.PrinterRecord(id="m1", kind=core_printers.MOONRAKER, host="voron.local", options={"base_path": "/k"})
    ep = Endpoint(core_printers.base_url(rec), None)
    assert ep.url("/server/info") == "http://voron.local:7125/k/server/info"
