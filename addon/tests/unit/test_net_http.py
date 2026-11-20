# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network: the HTTP client against in-process fake servers, blocking and under the tick runner."""
from __future__ import annotations

import time

import fake_net
import pytest
from fake_net import FakeHttpServer, Reply
from slicewright.core.network import errors
from slicewright.core.network.httpclient import Limits, request
from slicewright.core.network.multipart import BytesBody, MultipartBody
from slicewright.core.network.nbsocket import CancelToken, Timeouts, fetch_fingerprint, run_blocking
from slicewright.core.network.tls import (MemoryPinStore, TlsPolicy, fingerprint_of, format_fingerprint,
                                          normalize_fingerprint)
from slicewright.core.ticking import CANCELLED, DONE, FAILED, IDLE, TickRunner

FAST = Timeouts(connect=3.0, idle=3.0)


def get(url, **kw):
    return run_blocking(request("GET", url, timeouts=kw.pop("timeouts", FAST), **kw))


def upload_body(tmp_path, size, name="part.gcode"):
    f = tmp_path / name
    f.write_bytes(bytes(range(256)) * (size // 256))
    return MultipartBody([("path", "x"), ("print", "false")], file_field="file", file_path=str(f), filename=name)


# -- plain HTTP --------------------------------------------------------------------------------------------------

def test_get_json_and_request_shape():
    with FakeHttpServer({("GET", "/api/version"): Reply(body=b'{"api": "0.1"}', headers={"X-N": "1"})}) as srv:
        r = get(srv.url("/api/version?q=1"), headers={"X-Api-Key": "k"})
    assert (r.status, r.json(), r.header("x-n"), r.ok) == (200, {"api": "0.1"}, "1", True)
    assert r.url == f"http://127.0.0.1:{srv.port}"
    rec = srv.requests[0]
    assert (rec.method, rec.target) == ("GET", "/api/version?q=1")
    assert rec.headers["x-api-key"] == "k" and rec.headers["connection"] == "close"
    assert rec.headers["host"] == f"127.0.0.1:{srv.port}" and rec.headers["user-agent"] == "Slicewright"


def test_status_errors_are_answers_not_exceptions_until_asked():
    with FakeHttpServer({}) as srv:
        r = get(srv.url("/nope"))
        assert r.status == 404
        with pytest.raises(errors.HttpStatusError) as exc:
            r.raise_for_status()
    assert exc.value.status == 404 and "404" in str(exc.value)


def test_chunked_and_close_delimited_bodies():
    with FakeHttpServer({("GET", "/c"): Reply(body=b"chunked body here", chunked=True),
                         ("GET", "/e"): Reply(body=b"until close", no_content_length=True)}) as srv:
        assert get(srv.url("/c")).body == b"chunked body here"
        assert get(srv.url("/e")).body == b"until close"


def test_dripped_reply_is_assembled_from_partial_reads():
    reply = Reply(body=b'{"k": "' + b"v" * 300 + b'"}', drip=7, drip_interval=0.001)
    with FakeHttpServer({("GET", "/d"): reply}) as srv:
        assert get(srv.url("/d")).json() == {"k": "v" * 300}


def test_post_bytes_body():
    with FakeHttpServer({("POST", "/j"): Reply(201, "Created")}) as srv:
        r = run_blocking(request("POST", srv.url("/j"), body=BytesBody(b'{"a":1}', "application/json"), timeouts=FAST))
    assert r.status == 201
    rec = srv.requests[0]
    assert rec.body == b'{"a":1}' and rec.headers["content-type"] == "application/json"
    assert rec.headers["content-length"] == "7"


# -- failures ----------------------------------------------------------------------------------------------------

def test_connection_refused():
    with pytest.raises(errors.ConnectionRefused) as exc:
        get(f"http://127.0.0.1:{fake_net.free_port()}/")
    assert "refused" in str(exc.value) and exc.value.host == "127.0.0.1"


def test_resolve_error_with_an_injected_resolver():
    import socket

    def resolver(host, port):
        raise socket.gaierror(8, "nodename nor servname provided")
    with pytest.raises(errors.ResolveError, match="printer.invalid"):
        get("http://printer.invalid:5000/", resolver=resolver)


def test_a_numeric_host_never_calls_the_resolver():
    def resolver(host, port):
        raise AssertionError("resolver used for an IP literal")
    with FakeHttpServer({("GET", "/"): Reply(body=b"ok")}) as srv:
        assert get(srv.url("/"), resolver=resolver).body == b"ok"


def test_idle_timeout_when_the_server_is_silent():
    with fake_net.Silent() as srv:
        t0 = time.monotonic()
        with pytest.raises(errors.Timeout) as exc:
            get(f"http://127.0.0.1:{srv.port}/", timeouts=Timeouts(connect=2, idle=0.3))
    assert exc.value.phase == "idle" and time.monotonic() - t0 < 2


def test_total_timeout_caps_a_slow_drip():
    with FakeHttpServer({("GET", "/s"): Reply(body=b"x" * 400, drip=1, drip_interval=0.02)}) as srv:
        with pytest.raises(errors.Timeout) as exc:
            get(srv.url("/s"), timeouts=Timeouts(connect=2, idle=2, total=0.2))
    assert exc.value.phase == "total"


def test_tcp_connect_timeout_when_nothing_answers(monkeypatch):
    """A blackholed address cannot be made on loopback: a stub socket whose connect never completes."""
    import errno as _errno
    import select as _select

    from slicewright.core.network import nbsocket

    class Stub:
        def connect_ex(self, addr):
            return _errno.EINPROGRESS

    monkeypatch.setattr(_select, "select", lambda *a: ([], [], []))
    now = [0.0]

    def clock():
        now[0] += 0.2
        return now[0]
    gen = nbsocket._tcp_connect(Stub(), ("10.255.255.1", 80), "10.255.255.1", 80, 1.0, CancelToken(), clock, FAST)
    with pytest.raises(errors.Timeout) as exc:
        run_blocking(gen, sleep=0)
    assert exc.value.phase == "connect" and "10.255.255.1:80" in str(exc.value)


def test_tls_handshake_timeout_when_the_peer_accepts_but_never_answers():
    with fake_net.Silent() as srv:
        t0 = time.monotonic()
        with pytest.raises(errors.Timeout) as exc:
            get(f"https://127.0.0.1:{srv.port}/", timeouts=Timeouts(connect=0.3, idle=5))
    assert exc.value.phase == "connect" and time.monotonic() - t0 < 2


def test_server_closing_without_an_answer():
    with FakeHttpServer({("GET", "/x"): Reply(close_without_reply=True)}) as srv:
        with pytest.raises(errors.ConnectionLost, match="without answering"):
            get(srv.url("/x"))


def test_garbage_reply_is_a_protocol_error():
    with FakeHttpServer({("GET", "/x"): Reply(raw=b"\x16\x03\x01 not http at all\r\n\r\n")}) as srv:
        with pytest.raises(errors.ProtocolError):
            get(srv.url("/x"))


def test_oversized_header_and_body_are_rejected():
    big_header = Reply(headers={"X-Pad": "a" * 20000}, body=b"hi")
    big_body = Reply(body=b"b" * 50_000)
    with FakeHttpServer({("GET", "/h"): big_header, ("GET", "/b"): big_body,
                         ("GET", "/c"): Reply(body=b"c" * 50_000, chunked=True)}) as srv:
        with pytest.raises(errors.LimitExceeded):
            get(srv.url("/h"), limits=Limits(max_line_bytes=1000))
        with pytest.raises(errors.LimitExceeded, match="announced"):
            get(srv.url("/b"), limits=Limits(max_body_bytes=1000))
        with pytest.raises(errors.LimitExceeded):
            get(srv.url("/c"), limits=Limits(max_body_bytes=1000))
        assert get(srv.url("/b"), limits=Limits(max_body_bytes=100_000)).body == b"b" * 50_000


def test_invalid_requests_are_refused_before_connecting():
    with pytest.raises(errors.InvalidRequest):
        get("http://u:p@127.0.0.1:1/")
    with pytest.raises(errors.InvalidRequest):
        get("http://127.0.0.1:1/", headers={"X": "a\r\nb: c"})
    with pytest.raises(ValueError):
        get("http://127.0.0.1:1/", redirects="always")


# -- redirects ---------------------------------------------------------------------------------------------------

def test_redirects_are_not_followed_by_default():
    with FakeHttpServer({("GET", "/a"): Reply(302, "Found", {"Location": "/b"}), ("GET", "/b"): Reply(body=b"B")}) as s:
        r = get(s.url("/a"))
    assert (r.status, len(s.requests)) == (302, 1)


def test_same_host_redirects_are_followed():
    with FakeHttpServer({("GET", "/a"): Reply(302, "Found", {"Location": "/b?x=1"}),
                         ("GET", "/b"): Reply(body=b"B")}) as s:
        r = get(s.url("/a"), redirects="same-host", headers={"X-Api-Key": "k"})
    assert (r.status, r.body) == (200, b"B") and s.requests[1].target == "/b?x=1"
    assert s.requests[1].headers["x-api-key"] == "k"             # same host: allowed to carry it


def test_redirect_to_another_host_downgrade_port_and_loops_are_refused():
    with FakeHttpServer({("GET", "/other"): Reply(302, "Found", {"Location": "http://example.org/x?token=secret"}),
                         ("GET", "/port"): Reply(302, "Found", {"Location": "http://127.0.0.1:1/"}),
                         ("GET", "/loop"): Reply(302, "Found", {"Location": "/loop"})}) as s:
        with pytest.raises(errors.RedirectRefused) as exc:
            get(s.url("/other"), redirects="same-host")
        assert exc.value.location == "http://example.org" and "secret" not in str(exc.value)
        with pytest.raises(errors.RedirectRefused, match="another port"):
            get(s.url("/port"), redirects="same-host")
        with pytest.raises(errors.RedirectRefused, match="more than 3"):
            get(s.url("/loop"), redirects="same-host")


def test_post_is_not_replayed_after_302_but_is_after_307(tmp_path):
    with FakeHttpServer({("POST", "/u302"): Reply(302, "Found", {"Location": "/ok"}),
                         ("POST", "/u307"): Reply(307, "Temporary Redirect", {"Location": "/ok"}),
                         ("POST", "/ok"): Reply(201, "Created")}) as s:
        with pytest.raises(errors.RedirectRefused, match="not repeated"):
            run_blocking(request("POST", s.url("/u302"), body=upload_body(tmp_path, 4096), redirects="same-host",
                                 timeouts=FAST))
        r = run_blocking(request("POST", s.url("/u307"), body=upload_body(tmp_path, 4096), redirects="same-host",
                                 timeouts=FAST))
    assert r.status == 201 and s.requests[-1].body_length_seen == s.requests[-2].body_length_seen > 4096


# -- uploads -----------------------------------------------------------------------------------------------------

def test_multipart_upload_arrives_intact_with_progress(tmp_path):
    body = upload_body(tmp_path, 3 * 1024 * 1024)
    seen = []
    with FakeHttpServer({("POST", "/api/files/local"): Reply(201, "Created", body=b'{"done": true}')}) as s:
        r = run_blocking(request("POST", s.url("/api/files/local"), body=body, on_progress=lambda a, b: seen.append((a, b)),
                                 timeouts=FAST))
    assert r.status == 201 and r.json() == {"done": True}
    rec = s.requests[0]
    assert rec.body == b"".join(body.chunks()) and rec.headers["content-length"] == str(body.length)
    assert rec.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert seen[-1] == (body.length, body.length) or seen[-1][0] >= body.length - 1
    assert all(a <= b for a, b in seen) and [a for a, _ in seen] == sorted(a for a, _ in seen) and len(seen) > 3


def test_upload_yields_progress_fractions(tmp_path):
    body = upload_body(tmp_path, 2 * 1024 * 1024)
    with FakeHttpServer({("POST", "/u"): Reply(201, "Created")}) as s:
        gen = request("POST", s.url("/u"), body=body, timeouts=FAST)
        fractions = []
        try:
            while True:
                v = next(gen)
                if isinstance(v, float):
                    fractions.append(v)
                elif v is IDLE:
                    time.sleep(0.001)
        except StopIteration:
            pass
    assert fractions == sorted(fractions) and fractions[0] >= 0 and fractions[-1] <= 0.99 and len(fractions) > 2


def test_server_answering_before_the_body_is_heard(tmp_path):
    """A 401 sent right after the head: the upload stops and the status is reported (no reset error)."""
    body = upload_body(tmp_path, 8 * 1024 * 1024)
    reply = Reply(401, "Unauthorized", body=b"no", reply_before_body=True)
    with FakeHttpServer({("POST", "/u"): reply}) as s:
        r = run_blocking(request("POST", s.url("/u"), body=body, timeouts=FAST))
    assert r.status == 401 and r.body == b"no"
    assert s.requests[0].body_length_seen < body.length


def test_cancel_mid_upload_with_a_token(tmp_path):
    body = upload_body(tmp_path, 8 * 1024 * 1024)
    token = CancelToken()
    with FakeHttpServer({("POST", "/u"): Reply(201, "Created")}, read_delay=0.02) as s:
        gen = request("POST", s.url("/u"), body=body, cancel=token, timeouts=FAST)
        steps = 0
        with pytest.raises(errors.Cancelled):
            while True:
                v = next(gen)
                steps += 1
                if v is IDLE:
                    time.sleep(0.001)
                if steps == 40:
                    token.cancel()
        time.sleep(0.3)
    assert s.connections_closed_by_client == 1
    assert 0 < s.requests[0].body_length_seen < body.length


def test_cancel_mid_upload_through_the_tick_runner_closes_the_socket(tmp_path):
    body = upload_body(tmp_path, 8 * 1024 * 1024)
    done, cancelled = [], []
    with FakeHttpServer({("POST", "/u"): Reply(201, "Created")}, read_delay=0.02) as s:
        runner = TickRunner(budget_s=0.01, idle_interval_s=0)
        task = runner.submit("send", request("POST", s.url("/u"), body=body, timeouts=FAST),
                             on_done=done.append, on_cancel=lambda: cancelled.append(1))
        for _ in range(200):
            runner.tick()
            if task.progress > 0.01:
                break
        assert task.progress > 0
        runner.cancel(task)
        time.sleep(0.3)
    assert (task.state, cancelled, done) == (CANCELLED, [1], [])
    assert s.connections_closed_by_client == 1


# -- the tick runner ---------------------------------------------------------------------------------------------

def test_each_step_and_tick_stays_within_the_budget(tmp_path):
    """Upload 16 MB while a slow server reads: no single step takes more than a few ms and every tick is bounded."""
    body = upload_body(tmp_path, 16 * 1024 * 1024)
    step_times = []

    def timed(gen):
        while True:
            t0 = time.perf_counter()
            try:
                v = next(gen)
            except StopIteration as stop:
                step_times.append(time.perf_counter() - t0)
                return stop.value
            step_times.append(time.perf_counter() - t0)
            yield v

    with FakeHttpServer({("POST", "/u"): Reply(201, "Created")}) as s:
        runner = TickRunner(budget_s=0.020, idle_interval_s=0)
        result = []
        runner.submit("send", timed(request("POST", s.url("/u"), body=body, timeouts=Timeouts(connect=3, idle=10))),
                      on_done=result.append)
        tick_times = []
        while not runner.idle:
            t0 = time.perf_counter()
            runner.tick()
            tick_times.append(time.perf_counter() - t0)
    assert result and result[0].status == 201
    assert max(step_times) < 0.020, max(step_times)
    assert max(tick_times) < 0.020 + 0.020, max(tick_times)
    assert len(tick_times) > 5                              # it really was spread over many ticks


def test_a_blocked_socket_idles_instead_of_spinning():
    """Against a silent server the runner stops early and asks for a later call (IDLE protocol)."""
    with fake_net.Silent() as srv:
        runner = TickRunner(budget_s=0.5, idle_interval_s=0.05)
        errs = []
        runner.submit("t", request("GET", f"http://127.0.0.1:{srv.port}/", timeouts=Timeouts(connect=1, idle=0.2)),
                      on_error=errs.append)
        t0 = time.perf_counter()
        delay = runner.tick()
        assert time.perf_counter() - t0 < 0.1 and delay == 0.05
        assert runner.get("t").steps < 50
        runner.run_until_idle()
    assert isinstance(errs[0], errors.Timeout)


def test_the_runner_reports_a_failure_as_the_taxonomy_error():
    runner = TickRunner()
    errs = []
    task = runner.submit("t", request("GET", f"http://127.0.0.1:{fake_net.free_port()}/", timeouts=FAST),
                         on_error=errs.append)
    runner.run_until_idle()
    assert task.state == FAILED and isinstance(errs[0], errors.ConnectionRefused)


def test_two_requests_interleave_on_one_runner():
    with FakeHttpServer({("GET", "/1"): Reply(body=b"one", delay=0.05), ("GET", "/2"): Reply(body=b"two")}) as s:
        runner = TickRunner(idle_interval_s=0)
        got = {}
        runner.submit("a", request("GET", s.url("/1"), timeouts=FAST), on_done=lambda r: got.setdefault("a", r.body))
        runner.submit("b", request("GET", s.url("/2"), timeouts=FAST), on_done=lambda r: got.setdefault("b", r.body))
        runner.run_until_idle()
    assert got == {"a": b"one", "b": b"two"}


# -- TLS ---------------------------------------------------------------------------------------------------------

def tls_server(files, **kw):
    return FakeHttpServer({("GET", "/secure"): Reply(body=b"secret-data")}, tls_files=files, **kw)


def test_tls_default_policy_rejects_a_self_signed_certificate(certs):
    with tls_server(certs[0]) as s:
        with pytest.raises(errors.TlsVerificationError) as exc:
            get(s.url("/secure"))
    assert "could not be verified" in str(exc.value)
    assert s.requests == []                                     # the request was never sent


def test_tls_verify_succeeds_with_the_certificate_as_trusted_ca(certs):
    with tls_server(certs[0]) as s:
        r = get(s.url("/secure"), tls=TlsPolicy(cafile=certs[0][0]))
    assert r.body == b"secret-data"


def test_tls_verify_fails_when_the_ca_is_a_different_one(certs):
    with tls_server(certs[0]) as s:
        with pytest.raises(errors.TlsVerificationError):
            get(s.url("/secure"), tls=TlsPolicy(cafile=certs[1][0]))


def _fp(files):
    import ssl
    pem = open(files[0]).read()
    return fingerprint_of(ssl.PEM_cert_to_DER_cert(pem))


def test_tls_pin_trust_on_first_use_flow(certs):
    store = MemoryPinStore()
    policy = TlsPolicy(mode="pin", pin_key="printer-1", pin_store=store)
    with tls_server(certs[0]) as s:
        with pytest.raises(errors.TlsUnknownCertificate) as exc:
            get(s.url("/secure"), headers={"X-Api-Key": "k"}, tls=policy)
        assert exc.value.fingerprint == _fp(certs[0]) and s.requests == []      # nothing sent before the user agrees
        assert format_fingerprint(exc.value.fingerprint) in str(exc.value)
        store.set("printer-1", exc.value.fingerprint)                          # the user confirmed
        r = get(s.url("/secure"), headers={"X-Api-Key": "k"}, tls=policy)
    assert r.body == b"secret-data" and s.requests[0].headers["x-api-key"] == "k"


def test_tls_pin_mismatch_is_never_repaired_and_sends_nothing(certs):
    store = MemoryPinStore({"p": _fp(certs[0])})
    policy = TlsPolicy(mode="pin", pin_key="p", pin_store=store)
    with tls_server(certs[1]) as s:
        with pytest.raises(errors.TlsPinMismatch) as exc:
            get(s.url("/secure"), headers={"X-Api-Key": "k"}, tls=policy)
    assert exc.value.expected == _fp(certs[0]) and exc.value.got == _fp(certs[1])
    assert s.requests == [] and store.get("p") == _fp(certs[0])


def test_fetch_fingerprint_returns_the_leaf_hash_and_sends_nothing(certs):
    with tls_server(certs[1]) as s:
        fp = run_blocking(fetch_fingerprint("127.0.0.1", s.port, timeouts=FAST))
    assert fp == _fp(certs[1]) and s.requests == []


def test_tls_to_a_plain_server_is_a_tls_error_not_a_hang():
    with FakeHttpServer({("GET", "/"): Reply(body=b"plain")}) as s:
        with pytest.raises((errors.TlsError, errors.ConnectionLost, errors.Timeout)):
            get(s.url("/").replace("http://", "https://"), timeouts=Timeouts(connect=1, idle=1))


def test_plain_http_to_a_tls_server_does_not_hang(certs):
    with tls_server(certs[0]) as s:
        with pytest.raises((errors.ProtocolError, errors.ConnectionLost, errors.Timeout)):
            get(s.url("/secure").replace("https://", "http://"), timeouts=Timeouts(connect=1, idle=1))


def test_pin_policy_validation_and_fingerprint_helpers():
    with pytest.raises(ValueError):
        TlsPolicy(mode="pin")
    with pytest.raises(ValueError):
        TlsPolicy(mode="whatever")
    fp = "ab" * 32
    assert normalize_fingerprint(format_fingerprint(fp)) == fp
    with pytest.raises(ValueError):
        normalize_fingerprint("12:34")
    store = MemoryPinStore()
    store.set("k", fp.upper())
    assert store.get("k") == fp
    store.remove("k")
    assert store.get("k") is None


def test_error_messages_never_contain_request_secrets(certs):
    """Whatever fails, the message holds neither the API key nor the query string."""
    secret = "SEKRET-API-KEY-123"
    failures = []
    for url, kw in ((f"http://127.0.0.1:{fake_net.free_port()}/p?token={secret}", {}),):
        try:
            get(url, headers={"X-Api-Key": secret}, **kw)
        except errors.NetError as exc:
            failures.append(exc)
    with FakeHttpServer({}) as s:
        r = get(s.url(f"/missing?token={secret}"), headers={"X-Api-Key": secret})
        try:
            r.raise_for_status()
        except errors.NetError as exc:
            failures.append(exc)
    with tls_server(certs[0]) as s:
        try:
            get(s.url(f"/secure?token={secret}"), headers={"X-Api-Key": secret})
        except errors.NetError as exc:
            failures.append(exc)
    assert len(failures) == 3
    for exc in failures:
        assert secret not in str(exc) and secret not in repr(exc) and "token=" not in str(exc)
