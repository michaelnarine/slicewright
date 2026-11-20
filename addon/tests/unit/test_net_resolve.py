# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network/resolve: host-name lookups in a worker thread, with a timeout and cancel support."""
from __future__ import annotations

import socket
import threading
import time

import pytest
from fake_net import FakeHttpServer, Reply
from slicewright.core.network import errors, resolve
from slicewright.core.network.httpclient import request
from slicewright.core.network.nbsocket import CancelToken, Timeouts, connect, run_blocking
from slicewright.core.ticking import IDLE, TickRunner


def _lookup(*args, **kw):
    return run_blocking(resolve.resolve(*args, **kw))


def test_an_ip_literal_never_starts_a_thread_or_calls_the_resolver():
    def resolver(host, port):
        raise AssertionError("used")
    before = threading.active_count()
    assert _lookup("127.0.0.1", 80, resolver=resolver)[0][4] == ("127.0.0.1", 80)
    assert _lookup("::1", 80, resolver=resolver)[0][4] == ("::1", 80, 0, 0)
    assert threading.active_count() == before


def test_a_name_is_resolved_off_the_calling_thread_and_the_generator_idles_meanwhile():
    release = threading.Event()
    seen = {}

    def resolver(host, port):
        seen["thread"] = threading.current_thread()
        release.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", port))]
    gen = resolve.resolve("printer.local", 5000, resolver=resolver)
    assert next(gen) is IDLE and next(gen) is IDLE          # still waiting: every step returns at once
    release.set()
    with pytest.raises(StopIteration) as done:
        while True:
            next(gen)
    assert done.value.value[0][4] == ("127.0.0.1", 5000)
    assert seen["thread"] is not threading.main_thread() and seen["thread"].daemon
    assert resolve.stuck_lookups() == 0


def test_a_slow_lookup_times_out_without_blocking_and_is_abandoned():
    release = threading.Event()
    started = time.monotonic()
    with pytest.raises(errors.ResolveError, match=r"did not resolve within 0\.1 s.*IP address"):
        _lookup("slow.local", 80, resolver=lambda h, p: release.wait(5) or [], timeout=0.1)
    assert time.monotonic() - started < 1.0
    assert resolve.stuck_lookups() == 1                      # the OS has not answered; the thread is left alone
    release.set()
    for _ in range(100):
        if not resolve.stuck_lookups():
            break
        time.sleep(0.01)
    assert resolve.stuck_lookups() == 0


def test_a_failed_or_empty_lookup_is_a_resolve_error():
    def gaierror(host, port):
        raise socket.gaierror(8, "nodename nor servname provided")
    with pytest.raises(errors.ResolveError, match="nope.invalid"):
        _lookup("nope.invalid", 80, resolver=gaierror)
    with pytest.raises(errors.ResolveError):
        _lookup("empty.invalid", 80, resolver=lambda h, p: [])
    with pytest.raises(errors.ResolveError):
        _lookup("odd.invalid", 80, resolver=lambda h, p: 1 / 0)      # whatever the resolver raised: no leak


def test_cancel_stops_the_wait_at_once():
    release = threading.Event()
    token = CancelToken()
    gen = resolve.resolve("slow.local", 80, resolver=lambda h, p: release.wait(5) or [], cancel=token, timeout=30)
    assert next(gen) is IDLE
    token.cancel()
    with pytest.raises(errors.Cancelled):
        next(gen)
    release.set()


def test_closing_the_generator_leaves_no_stuck_state_once_the_thread_returns():
    release = threading.Event()
    gen = resolve.resolve("slow.local", 80, resolver=lambda h, p: release.wait(5) or [], timeout=30)
    next(gen)
    gen.close()                                              # a cancelled TickRunner task does this
    assert resolve.stuck_lookups() == 1
    release.set()
    for _ in range(100):
        if not resolve.stuck_lookups():
            break
        time.sleep(0.01)
    assert resolve.stuck_lookups() == 0


def test_too_many_stuck_lookups_are_refused_instead_of_piling_up_threads(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(resolve, "MAX_STUCK", 2)
    for i in range(2):
        gen = resolve.resolve(f"slow{i}.local", 80, resolver=lambda h, p: release.wait(5) or [], timeout=30)
        next(gen)
        gen.close()
    try:
        with pytest.raises(errors.ResolveError, match="too many lookups"):
            _lookup("third.local", 80, resolver=lambda h, p: [])
    finally:
        release.set()


def test_connect_uses_the_resolver_through_the_worker_and_the_runner_stays_responsive():
    release = threading.Event()

    def resolver(host, port):
        release.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", port))]
    with FakeHttpServer({("GET", "/x"): Reply(body=b"ok")}) as srv:
        runner = TickRunner()
        got = []
        runner.submit("get", request("GET", f"http://printer.local:{srv.port}/x", resolver=resolver,
                                     timeouts=Timeouts(connect=3, idle=3)), on_done=got.append)
        for _ in range(5):                                   # the lookup is pending: ticks return, nothing is done
            assert runner.tick() is not None
        assert not got
        release.set()
        runner.run_until_idle()
    assert got and got[0].body == b"ok"


def test_the_connect_deadline_starts_after_the_lookup():
    """A lookup that takes longer than ``connect`` must not eat the connect budget."""
    def resolver(host, port):
        time.sleep(0.3)
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", port))]
    with FakeHttpServer({("GET", "/"): Reply(body=b"ok")}) as srv:
        gen = request("GET", f"http://printer.local:{srv.port}/", resolver=resolver,
                      timeouts=Timeouts(connect=0.2, idle=3, resolve=2))
        assert run_blocking(gen).body == b"ok"


def test_connect_reports_a_lookup_timeout_as_a_resolve_error():
    release = threading.Event()
    try:
        with pytest.raises(errors.ResolveError, match="did not resolve within"):
            run_blocking(connect("slow.local", 80, resolver=lambda h, p: release.wait(5) or [],
                                 timeouts=Timeouts(resolve=0.1)))
    finally:
        release.set()
