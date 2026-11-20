# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network/access.py: the one network gate, and that connect() honours it."""
from __future__ import annotations

import fake_net
import pytest
from fake_net import FakeHttpServer, Reply
from slicewright.core.network import access, errors
from slicewright.core.network.httpclient import request
from slicewright.core.network.nbsocket import fetch_fingerprint, run_blocking


def test_no_provider_means_no_network():
    access.set_provider(None)
    result = access.network_allowed()
    assert not result.allowed and not result and result.reason == access.NO_PROVIDER
    with pytest.raises(access.OnlineAccessDenied, match="not running inside Blender"):
        access.require_network()


def test_provider_answers_and_is_read_afresh_each_time():
    state = {"on": False}
    access.set_provider(lambda: access.Access(state["on"], "" if state["on"] else "switch it on"))
    assert access.network_allowed() == access.Access(False, "switch it on")
    state["on"] = True
    assert access.network_allowed().allowed
    access.require_network()


def test_a_failing_or_odd_provider_means_no():
    def boom():
        raise RuntimeError("x")
    access.set_provider(boom)
    assert not access.network_allowed().allowed
    access.set_provider(lambda: True)                   # not an Access
    assert not access.network_allowed().allowed


def test_no_socket_is_opened_when_access_is_denied(monkeypatch):
    """The gate is inside connect(): HTTP, the fingerprint probe and anything built on them are refused before any
    socket exists, and the server never sees a connection."""
    import socket
    created = []
    real = socket.socket
    with FakeHttpServer({("GET", "/"): Reply(body=b"hi")}) as srv:
        monkeypatch.setattr("slicewright.core.network.nbsocket.socket.socket",
                            lambda *a, **k: created.append(a) or real(*a, **k))
        access.set_provider(lambda: access.Access(False, "Blender's \"Allow Online Access\" is off."))
        with pytest.raises(access.OnlineAccessDenied) as exc:
            run_blocking(request("GET", srv.url("/")))
        assert "Allow Online Access" in str(exc.value) and isinstance(exc.value, errors.NetError)
        with pytest.raises(access.OnlineAccessDenied):
            run_blocking(fetch_fingerprint("127.0.0.1", srv.port))
        assert created == [] and srv.requests == []
        access.set_provider(lambda: access.ALLOW)
        assert run_blocking(request("GET", srv.url("/"))).body == b"hi"
    assert created                                       # allowed again: a socket was made


def test_denial_mid_session_stops_the_next_request():
    state = {"on": True}
    access.set_provider(lambda: access.Access(state["on"], "off now"))
    with FakeHttpServer({("GET", "/"): Reply(body=b"hi")}) as srv:
        assert run_blocking(request("GET", srv.url("/"))).body == b"hi"
        state["on"] = False
        with pytest.raises(access.OnlineAccessDenied, match="off now"):
            run_blocking(request("GET", srv.url("/")))
    assert len(srv.requests) == 1
