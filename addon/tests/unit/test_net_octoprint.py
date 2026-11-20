# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network/octoprint.py against the in-process fake OctoPrint (tests/fake_net.py)."""
from __future__ import annotations

import hashlib
import logging
import os
import socket
import threading

import fake_net
import pytest
from fake_net import FakeOctoPrint
from slicewright.core import logs, printers as core_printers, redact
from slicewright.core.network import errors, octoprint
from slicewright.core.network.nbsocket import CancelToken, Timeouts, run_blocking
from slicewright.core.network.webapi import Endpoint, Transfer
from slicewright.core.redact import SecretStr
from slicewright.core.ticking import DONE, FAILED, IDLE, TickRunner

KEY = "OCTO-KEY-0123456789abcdef"
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

def test_connection_reports_versions_state_and_that_the_key_works():
    with FakeOctoPrint(KEY) as srv:
        info = run(octoprint.test_connection(endpoint(srv)))
    assert (info.server_version, info.api_version, info.key_accepted, info.printer_state) == ("1.10.2", "0.1", True,
                                                                                           "Operational")
    assert info.summary() == "OctoPrint 1.10.2 (API 0.1), API key accepted, printer: Operational"
    assert [r.target for r in srv.requests] == ["/api/version", "/api/connection"]
    assert all(r.headers["x-api-key"] == KEY for r in srv.requests)


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_is_a_clear_auth_error_and_the_key_is_not_in_it(status):
    with FakeOctoPrint("some-other-key-value") as srv:
        srv.auth_status = status
        srv.echo_secret = True
        with pytest.raises(errors.AuthRejected) as exc:
            run(octoprint.test_connection(endpoint(srv)))
    assert exc.value.status == status and "rejected the API key" in str(exc.value)
    assert KEY not in str(exc.value) and "127.0.0.1" in str(exc.value)


def test_a_missing_key_sends_nothing():
    with FakeOctoPrint(KEY) as srv:
        with pytest.raises(errors.InvalidRequest, match="Set the API key"):
            run(octoprint.test_connection(endpoint(srv, key=None)))
    assert srv.requests == []


def test_the_connection_state_is_optional():
    with FakeOctoPrint(KEY) as srv:
        srv.connection_status = 403                     # a key without the connection permission
        info = run(octoprint.test_connection(endpoint(srv)))
        assert info.key_accepted and info.printer_state is None
        srv.connection_status = 500
        assert run(octoprint.test_connection(endpoint(srv))).printer_state is None


def test_something_else_on_the_port_is_not_octoprint():
    with FakeOctoPrint(KEY) as srv:
        srv.not_octoprint = True
        with pytest.raises(errors.NotThisServer, match="not like OctoPrint"):
            run(octoprint.test_connection(endpoint(srv)))
    with fake_net.FakeHttpServer({}) as srv404:                          # 404 on /api/version: wrong base path
        with pytest.raises(errors.HttpStatusError, match="base path") as exc:
            run(octoprint.test_connection(Endpoint(srv404.url("").rstrip("/"), SecretStr(KEY), timeouts=FAST)))
    assert exc.value.status == 404


def test_a_reverse_proxy_prefix_is_used_for_every_request():
    with FakeOctoPrint(KEY, prefix="/octoprint") as srv:
        info = run(octoprint.test_connection(endpoint(srv)))
        assert info.key_accepted
        bad = Endpoint(f"http://127.0.0.1:{srv.port}", SecretStr(KEY), timeouts=FAST)      # forgot the prefix
        with pytest.raises(errors.HttpStatusError) as exc:
            run(octoprint.test_connection(bad))
    assert exc.value.status == 404


def test_transport_errors_pass_through():
    ep = Endpoint(f"http://127.0.0.1:{fake_net.free_port()}", SecretStr(KEY), timeouts=FAST)
    with pytest.raises(errors.ConnectionRefused):
        run(octoprint.test_connection(ep))
    with fake_net.Silent() as silent:
        ep = Endpoint(f"http://127.0.0.1:{silent.port}", SecretStr(KEY), timeouts=Timeouts(connect=1, idle=0.3))
        with pytest.raises(errors.Timeout):
            run(octoprint.test_connection(ep))


# -- upload ------------------------------------------------------------------------------------------------------

def test_upload_streams_the_file_with_the_documented_fields(tmp_path):
    path = gcode(tmp_path, 700_000)
    transfer = Transfer()
    with FakeOctoPrint(KEY) as srv:
        result = run(octoprint.upload(endpoint(srv), path, "my part.gcode", transfer=transfer))
    up = srv.uploads[0]
    assert up.content == open(path, "rb").read() and up.sha256 == hashlib.sha256(up.content).hexdigest()
    assert up.filename == "my part.gcode" and up.fields == {}              # nothing but the file: no select, no print
    assert (result.name, result.path, result.size, result.selected, result.printing) == (
        "my part.gcode", "my part.gcode", 700_000, False, False)
    assert result.summary() == "Sent my part.gcode to OctoPrint"
    rec = srv.requests[-1]
    assert (rec.method, rec.target) == ("POST", "/api/files/local") and rec.headers["x-api-key"] == KEY
    assert rec.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert (transfer.phase, transfer.sent, transfer.total) == ("done", transfer.total, transfer.total)
    assert transfer.total > 700_000


def test_upload_to_a_folder_and_select(tmp_path):
    with FakeOctoPrint(KEY) as srv:
        result = run(octoprint.upload(endpoint(srv), gcode(tmp_path), "a.gcode", subfolder="/cases/new/", select=True))
    assert srv.uploads[0].fields == {"path": "cases/new", "select": "true"}
    assert (result.path, result.selected, result.printing) == ("cases/new/a.gcode", True, False)


def test_a_print_starts_only_when_asked_for(tmp_path):
    with FakeOctoPrint(KEY) as srv:
        run(octoprint.upload(endpoint(srv), gcode(tmp_path), "a.gcode"))
        run(octoprint.upload(endpoint(srv), gcode(tmp_path), "b.gcode", select=True))
        result = run(octoprint.upload(endpoint(srv), gcode(tmp_path), "c.gcode", start_print=True))
    assert [u.fields.get("print") for u in srv.uploads] == [None, None, "true"]
    assert [u.fields.get("select") for u in srv.uploads] == [None, "true", "true"]
    assert result.printing and result.selected and result.summary() == "Sent c.gcode to OctoPrint and started the print"
    assert srv.printed == "c.gcode"


def test_truthy_but_not_true_never_starts_a_print(tmp_path):
    with FakeOctoPrint(KEY) as srv:
        run(octoprint.upload(endpoint(srv), gcode(tmp_path), "a.gcode", start_print=1))     # type: ignore[arg-type]
    assert "print" not in srv.uploads[0].fields


def test_printer_busy_is_a_409_with_a_clear_message(tmp_path):
    with FakeOctoPrint(KEY) as srv:
        srv.busy = True
        srv.echo_secret = True
        with pytest.raises(errors.Conflict, match="busy") as exc:
            run(octoprint.upload(endpoint(srv), gcode(tmp_path), "a.gcode", start_print=True))
        assert exc.value.status == 409 and KEY not in str(exc.value)
        run(octoprint.upload(endpoint(srv), gcode(tmp_path), "a.gcode"))       # a plain upload is still fine
    assert len(srv.uploads) == 1


@pytest.mark.parametrize("status,kind", [(401, errors.AuthRejected), (403, errors.AuthRejected), (409, errors.Conflict),
                                         (415, errors.HttpStatusError), (500, errors.HttpStatusError),
                                         (503, errors.HttpStatusError), (400, errors.HttpStatusError)])
def test_error_statuses_after_the_body(tmp_path, status, kind):
    with FakeOctoPrint(KEY) as srv:
        srv.upload_status = status
        srv.echo_secret = True
        with pytest.raises(kind) as exc:
            run(octoprint.upload(endpoint(srv), gcode(tmp_path), "a.gcode"))
    assert exc.value.status == status and KEY not in str(exc.value) and srv.uploads == []


def test_413_before_the_body_stops_the_upload_early(tmp_path):
    path = gcode(tmp_path, 3_000_000)
    transfer = Transfer()
    with FakeOctoPrint(KEY) as srv:
        srv.upload_early_status = 413
        with pytest.raises(errors.PayloadTooLarge, match="too large") as exc:
            run(octoprint.upload(endpoint(srv), path, "big.gcode", transfer=transfer))
    assert exc.value.status == 413 and transfer.sent < transfer.total


def test_a_wrong_key_on_upload_is_rejected_before_the_body_is_read(tmp_path):
    with FakeOctoPrint("another-key-value-xyz") as srv:
        with pytest.raises(errors.AuthRejected):
            run(octoprint.upload(endpoint(srv), gcode(tmp_path, 2_000_000), "a.gcode"))
    assert srv.uploads == []


def test_connection_dropped_in_the_middle_of_an_upload(tmp_path):
    with FakeOctoPrint(KEY) as srv:
        srv.abort_after = 200_000
        with pytest.raises(errors.ConnectionLost):
            run(octoprint.upload(endpoint(srv), gcode(tmp_path, 2_000_000), "a.gcode"))
    assert srv.uploads == []


def test_a_server_that_goes_silent_while_receiving_times_out(tmp_path):
    with FakeOctoPrint(KEY, read_delay=0.0) as srv:
        srv.read_delay = 3.0                               # one 64 KB chunk per 3 s
        with pytest.raises(errors.Timeout) as exc:
            run(octoprint.upload(endpoint(srv, upload_timeouts=Timeouts(connect=2, idle=0.4)),
                                 gcode(tmp_path, 4_000_000), "a.gcode"))
    assert exc.value.phase == "idle"


def test_bad_names_and_folders_are_refused_before_anything_is_sent(tmp_path):
    path = gcode(tmp_path)
    with FakeOctoPrint(KEY) as srv:
        for name in ("", "  ", "a/b.gcode", "a\\b.gcode", ".hidden.gcode", "x\ny.gcode", "x" * 300):
            with pytest.raises(errors.InvalidRequest):
                run(octoprint.upload(endpoint(srv), path, name))
        for folder in ("../x", "a/../b", "a//b", "a\\b", ".hid/x", "x\ty"):
            with pytest.raises(errors.InvalidRequest):
                run(octoprint.upload(endpoint(srv), path, "a.gcode", subfolder=folder))
        with pytest.raises(errors.UploadError):
            run(octoprint.upload(endpoint(srv), str(tmp_path / "missing.gcode"), "a.gcode"))
    assert srv.requests == []


def test_a_non_ascii_name_survives(tmp_path):
    with FakeOctoPrint(KEY) as srv:
        run(octoprint.upload(endpoint(srv), gcode(tmp_path), "Gehäuse_Ø20.gcode"))
    assert srv.uploads[0].filename == "Gehäuse_Ø20.gcode"


def test_an_unparseable_success_body_is_not_a_failure_and_claims_nothing(tmp_path):
    with FakeOctoPrint(KEY) as srv:
        orig = srv._stored
        srv._stored = lambda up: fake_net.Reply(201, "Created", body=b"<html>proxy page</html>")
        result = run(octoprint.upload(endpoint(srv), gcode(tmp_path), "a.gcode", start_print=True))
        srv._stored = orig
    assert (result.path, result.selected, result.printing) == ("a.gcode", False, False)


def test_the_upload_can_be_cancelled_mid_way_and_the_file_is_not_stored(tmp_path):
    token = CancelToken()
    transfer = Transfer()
    with FakeOctoPrint(KEY, read_delay=0.02) as srv:
        gen = octoprint.upload(endpoint(srv, cancel=token), gcode(tmp_path, 4_000_000), "a.gcode", transfer=transfer)
        with pytest.raises(errors.Cancelled):
            while True:
                next(gen)
                if transfer.sent > 300_000:
                    token.cancel()
    assert 0 < transfer.sent < transfer.total and srv.uploads == []


def test_closing_the_generator_mid_upload_aborts_it(tmp_path):
    transfer = Transfer()
    with FakeOctoPrint(KEY, read_delay=0.02) as srv:
        gen = octoprint.upload(endpoint(srv), gcode(tmp_path, 4_000_000), "a.gcode", transfer=transfer)
        while transfer.sent < 300_000:
            next(gen)
        gen.close()                                         # what cancelling a TickRunner task does
        for _ in range(100):
            if srv.connections_closed_by_client:
                break
            threading.Event().wait(0.02)
    assert srv.connections_closed_by_client == 1 and srv.uploads == []


def test_the_upload_runs_under_the_tick_runner_without_blocking_the_budget(tmp_path):
    """While the file is being sent no step may take as long as the 20 ms tick budget, even with a 3 MB file. (Steps after
    the last byte are not timed: the fake server parses the whole body in this very process then and holds the GIL.)"""
    import time
    worst = 0.0
    steps = 0
    transfer = Transfer()

    def timed(gen):
        nonlocal worst, steps
        try:
            while True:
                t = time.perf_counter()
                value = next(gen)
                if transfer.phase in ("connecting", "uploading"):
                    worst = max(worst, time.perf_counter() - t)
                steps += 1
                yield value
        except StopIteration as stop:
            return stop.value
    got = []
    with FakeOctoPrint(KEY) as srv:
        runner = TickRunner()
        task = runner.submit("send", timed(octoprint.upload(endpoint(srv), gcode(tmp_path, 3_000_000), "a.gcode",
                                                             transfer=transfer)), on_done=got.append)
        runner.run_until_idle()
    assert task.state == DONE and got and got[0].size == 3_000_000 and steps > 5
    assert worst < 0.02, worst


# -- secrets -----------------------------------------------------------------------------------------------------

def test_the_secret_never_appears_in_urls_errors_logs_or_results(tmp_path, caplog):
    redact.register(KEY)
    seen: list[str] = []
    with FakeOctoPrint(KEY) as srv:
        srv.echo_secret = True
        ep = endpoint(srv)
        assert KEY not in repr(ep) and KEY not in str(ep)
        with caplog.at_level(logging.DEBUG):
            run(octoprint.test_connection(ep))
            result = run(octoprint.upload(ep, gcode(tmp_path), "a.gcode", select=True))
            seen += [repr(result), result.summary()]
            for status in (401, 409, 500):
                srv.upload_status = status
                with pytest.raises(errors.NetError) as exc:
                    run(octoprint.upload(ep, gcode(tmp_path), "a.gcode"))
                seen += [str(exc.value), repr(exc.value)]
            srv.upload_status = None
            srv.auth_status = 401
            srv.api_key = "different-key-123456"
            with pytest.raises(errors.AuthRejected) as exc:
                run(octoprint.test_connection(ep))
            seen += [str(exc.value), repr(exc.value)]
        assert all(KEY not in r.target for r in srv.requests)                  # never in a URL
    assert all(KEY not in text for text in seen)
    assert KEY not in caplog.text and KEY not in "".join(r.getMessage() for r in caplog.records)
    redact.clear()


def test_the_endpoint_built_from_a_printer_record_uses_its_base_path_and_policy():
    rec = core_printers.PrinterRecord(id="p1", kind=core_printers.OCTOPRINT, host="octopi.local", port=0,
                                      options={"https": True, "tls": "pin", "base_path": "/op"})
    store = core_printers.record_pin_store(rec)
    ep = Endpoint(core_printers.base_url(rec), SecretStr(KEY), tls=core_printers.tls_policy(rec, store))
    assert ep.url("/api/version") == "https://octopi.local/op/api/version"
    assert ep.tls.mode == "pin" and ep.tls.pin_key == "p1"


def test_over_https_the_key_is_sent_only_after_the_pinned_certificate_matched(certs, tmp_path):
    from slicewright.core.network.tls import MemoryPinStore, TlsPolicy, fingerprint_of
    import ssl
    (cert_a, key_a), (cert_b, key_b) = certs
    fp_a = fingerprint_of(ssl.PEM_cert_to_DER_cert(open(cert_a).read()))
    store = MemoryPinStore()
    policy = TlsPolicy(mode="pin", pin_key="p1", pin_store=store)
    with FakeOctoPrint(KEY, tls_files=(cert_a, key_a)) as srv:
        ep = Endpoint(f"https://127.0.0.1:{srv.port}", SecretStr(KEY), tls=policy, timeouts=FAST, upload_timeouts=FAST)
        with pytest.raises(errors.TlsUnknownCertificate) as unknown:
            run(octoprint.test_connection(ep))
        assert unknown.value.fingerprint == fp_a and srv.requests == []          # no byte, so no key, was sent
        store.set("p1", fp_a)                                                     # the user confirmed
        assert run(octoprint.test_connection(ep)).key_accepted
        run(octoprint.upload(ep, gcode(tmp_path), "a.gcode"))
    with FakeOctoPrint(KEY, tls_files=(cert_b, key_b)) as imposter:               # same pin, another certificate
        ep = Endpoint(f"https://127.0.0.1:{imposter.port}", SecretStr(KEY), tls=policy, timeouts=FAST)
        with pytest.raises(errors.TlsPinMismatch):
            run(octoprint.test_connection(ep))
        assert imposter.requests == []
