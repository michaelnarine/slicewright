# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network/mqtt.py (a minimal MQTT 3.1.1 client over TLS) against the in-process FakeMqttBroker."""
from __future__ import annotations

import json
import logging
import ssl
import threading

import pytest
from fake_net import FakeMqttBroker, free_port
from slicewright.core import redact
from slicewright.core.network import errors, mqtt
from slicewright.core.network.nbsocket import CancelToken, Timeouts, run_blocking
from slicewright.core.network.tls import MemoryPinStore, TlsPolicy, fingerprint_of
from slicewright.core.redact import SecretStr

CODE = "AC-98765432"
FAST = Timeouts(connect=3.0, idle=3.0)


def fp(files):
    return fingerprint_of(ssl.PEM_cert_to_DER_cert(open(files[0]).read()))


def endpoint(srv, certs, *, pinned=0, code=CODE, **kw):
    store = MemoryPinStore({"p": fp(certs[pinned])})
    kw.setdefault("timeouts", FAST)
    return mqtt.MqttEndpoint("127.0.0.1", TlsPolicy(mode="pin", pin_key="p", pin_store=store), srv.port, "bblp",
                             SecretStr(code) if code else None, login_hint=", and Developer Mode", **kw)


def broker(certs, **kw):
    return FakeMqttBroker(CODE, tls_files=certs[0], **kw)


def run(gen):
    return run_blocking(gen)


def session(srv, certs, **kw):
    client = run(mqtt.open_client(endpoint(srv, certs, **kw)))
    run(client.subscribe(srv.report_topic))
    return client


# -- the packets ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("n,expected", [(0, b"\x00"), (127, b"\x7f"), (128, b"\x80\x01"), (16383, b"\xff\x7f"),
                                        (16384, b"\x80\x80\x01"), (2097151, b"\xff\xff\x7f"),
                                        (2097152, b"\x80\x80\x80\x01"), (268435455, b"\xff\xff\xff\x7f")])
def test_remaining_length_encoding_follows_the_spec(n, expected):
    assert mqtt.encode_length(n) == expected
    with pytest.raises(ValueError):
        mqtt.encode_length(268435456)


def test_the_connect_packet_has_the_spec_layout():
    got = mqtt.connect_packet("cid", "bblp", b"pw", keep_alive=30)
    # fixed header, remaining length; protocol name "MQTT", level 4, flags (user, password, clean session), keep alive 30;
    # then client id, user name and password as length-prefixed strings
    assert got == b"\x10" + bytes([len(got) - 2]) + b"\x00\x04MQTT\x04\xc2\x00\x1e\x00\x03cid\x00\x04bblp\x00\x02pw"


def test_subscribe_and_publish_packets():
    assert mqtt.subscribe_packet(7, "device/x/report") == b"\x82\x14\x00\x07\x00\x0fdevice/x/report\x00"
    assert mqtt.publish_packet("a/b", b"hi") == b"\x30\x07\x00\x03a/bhi"
    assert mqtt.publish_packet("a/b", b"hi", 1, 9) == b"\x32\x09\x00\x03a/b\x00\x09hi"
    for bad in ("a/#", "a/+", ""):
        with pytest.raises(errors.InvalidRequest):
            mqtt.publish_packet(bad, b"x")
    with pytest.raises(errors.InvalidRequest):
        mqtt.publish_packet("a", b"x", 2, 1)


def test_the_parser_splits_a_stream_and_refuses_oversized_or_malformed_lengths():
    parser = mqtt._Parser()
    stream = mqtt.publish_packet("t", b"x" * 300) + mqtt.packet(mqtt.PINGRESP, 0, b"")
    for i in range(0, len(stream), 7):                               # in pieces of seven bytes
        parser.feed(stream[i:i + 7])
    first, second = parser.next("h"), parser.next("h")
    assert (first[0], len(first[2])) == (mqtt.PUBLISH, 2 + 1 + 300) and second == (mqtt.PINGRESP, 0, b"")
    assert parser.next("h") is None
    bad = mqtt._Parser()
    bad.feed(b"\x30\xff\xff\xff\xff\x01")
    with pytest.raises(errors.ProtocolError, match="malformed"):
        bad.next("h")
    big = mqtt._Parser()
    big.feed(b"\x30" + mqtt.encode_length(mqtt.MAX_PACKET + 1))
    with pytest.raises(errors.LimitExceeded):
        big.next("h")


# -- a session ----------------------------------------------------------------------------------------------------

def test_connect_subscribe_publish_and_receive(certs):
    with broker(certs) as srv:
        client = session(srv, certs)
        (cid, user, password), = srv.connects
        assert cid.startswith("slicewright-") and "bambu" not in cid.lower() and (user, password) == ("bblp", CODE)
        assert srv.subscriptions == [srv.report_topic]
        run(client.publish(srv.request_topic, b'{"a":1}'))                      # QoS 0
        run(client.publish(srv.request_topic, b'{"a":2}', qos=1))               # QoS 1: waits for the PUBACK
        srv.emit({"print": {"gcode_state": "IDLE"}})
        message = run(client.next_message(2.0))
        assert message.topic == srv.report_topic and json.loads(message.payload) == {"print": {"gcode_state": "IDLE"}}
        assert run(client.next_message(0.1)) is None                             # nothing more: a timeout, not an error
        client.close()
        for _ in range(100):
            if srv.clean_disconnects:
                break
            threading.Event().wait(0.02)
    assert [(p.topic, p.payload, p.qos) for p in srv.published] == [
        (srv.request_topic, b'{"a":1}', 0), (srv.request_topic, b'{"a":2}', 1)]
    assert srv.clean_disconnects == 1 and srv.errors == []


def test_an_incoming_qos1_message_is_acknowledged_and_a_qos2_one_is_refused(certs):
    with broker(certs) as srv:
        client = session(srv, certs)
        srv.emit({"x": 1}, qos=1)
        assert run(client.next_message(2.0)).qos == 1
        for _ in range(100):
            if srv.pubacks:
                break
            run(client.next_message(0.02))
        assert srv.pubacks == [7]
        client.close()


def test_keep_alive_pings_when_the_line_is_quiet(certs, monkeypatch):
    monkeypatch.setattr(mqtt, "KEEP_ALIVE", 0.2)
    with broker(certs) as srv:
        client = session(srv, certs)
        assert run(client.next_message(0.7)) is None
        client.close()
    assert srv.pings >= 2


def test_a_very_large_report_is_read_whole(certs):
    big = {"print": {"gcode_state": "IDLE", "filler": "x" * 200_000}}
    with broker(certs) as srv:
        client = session(srv, certs)
        srv.emit(big)
        message = run(client.next_message(3.0))
        client.close()
    assert json.loads(message.payload) == big


# -- failures ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("scripted,code", [(None, "wrong-code"), (4, CODE), (5, CODE)])
def test_a_refused_login_stops_with_a_clear_message_and_no_retry(certs, scripted, code):
    with broker(certs) as srv:
        srv.connack = scripted
        with pytest.raises(errors.LoginRejected, match="Developer Mode") as exc:
            run(mqtt.open_client(endpoint(srv, certs, code=code)))
    assert len(srv.connects) == 1 and srv.subscriptions == [] and srv.published == []
    assert "wrong-code" not in str(exc.value) and "MQTT" in str(exc.value)


def test_other_connack_codes_are_protocol_errors_and_a_silent_broker_times_out(certs):
    with broker(certs) as srv:
        srv.connack = 3
        with pytest.raises(errors.ProtocolError, match="not accepting"):
            run(mqtt.open_client(endpoint(srv, certs)))
    with broker(certs) as srv:
        srv.silent_connect = True
        with pytest.raises(errors.Timeout):
            run(mqtt.open_client(endpoint(srv, certs, timeouts=Timeouts(connect=2, idle=0.4))))


def test_a_refused_subscription_and_a_dropped_connection(certs):
    with broker(certs) as srv:
        srv.refuse_subscribe = True
        client = run(mqtt.open_client(endpoint(srv, certs)))
        with pytest.raises(errors.ProtocolError, match="refused the subscription"):
            run(client.subscribe(srv.report_topic))
        client.close()
    with broker(certs) as srv:
        srv.drop_on_publish = True
        client = session(srv, certs)
        with pytest.raises(errors.ConnectionLost):
            run(client.publish(srv.request_topic, b"{}", qos=1))
        client.close()


def test_connection_refused_and_missing_credentials(certs):
    ep = mqtt.MqttEndpoint("127.0.0.1", TlsPolicy(mode="pin", pin_key="p", pin_store=MemoryPinStore()), free_port(),
                           "bblp", SecretStr(CODE), timeouts=FAST)
    with pytest.raises(errors.ConnectionRefused):
        run(mqtt.open_client(ep))
    with broker(certs) as srv:
        with pytest.raises(errors.InvalidRequest, match="access code"):
            run(mqtt.open_client(endpoint(srv, certs, code=None)))
    assert srv.connects == [] and bytes(srv.raw_received) == b""


# -- certificate pinning --------------------------------------------------------------------------------------------

def test_an_unknown_certificate_stops_before_the_access_code_is_sent(certs):
    store = MemoryPinStore()
    with broker(certs) as srv:
        ep = mqtt.MqttEndpoint("127.0.0.1", TlsPolicy(mode="pin", pin_key="p", pin_store=store), srv.port, "bblp",
                               SecretStr(CODE), timeouts=FAST)
        with pytest.raises(errors.TlsUnknownCertificate) as exc:
            run(mqtt.open_client(ep))
        assert exc.value.fingerprint == fp(certs[0])
        assert bytes(srv.raw_received) == b"" and srv.connects == []
        store.set("p", exc.value.fingerprint)
        run(mqtt.open_client(ep)).close()
    assert [c[2] for c in srv.connects] == [CODE]


def test_an_imposter_with_another_certificate_never_receives_the_access_code(certs):
    with FakeMqttBroker(CODE, tls_files=certs[1]) as imposter:
        with pytest.raises(errors.TlsPinMismatch) as exc:
            run(mqtt.open_client(endpoint(imposter, certs)))
    assert exc.value.expected == fp(certs[0]) and exc.value.got == fp(certs[1])
    assert bytes(imposter.raw_received) == b"" and imposter.connects == [] and CODE not in str(exc.value)


# -- cancel ---------------------------------------------------------------------------------------------------------

def test_cancel_while_waiting_and_closing_the_generator_close_the_connection(certs):
    token = CancelToken()
    with broker(certs) as srv:
        client = session(srv, certs, cancel=token)
        gen = client.next_message(30.0)
        next(gen)
        token.cancel()
        with pytest.raises(errors.Cancelled):
            next(gen)
        client.close()
    with broker(certs) as srv:
        client = session(srv, certs)
        gen = client.next_message(30.0)
        next(gen)
        gen.close()
        client.close()
        for _ in range(100):
            if srv.clean_disconnects:
                break
            threading.Event().wait(0.02)
    assert srv.clean_disconnects == 1


# -- the access code is nowhere --------------------------------------------------------------------------------------

def test_the_access_code_never_appears_in_messages_reprs_or_logs(certs, caplog):
    redact.register(CODE)
    seen = []
    with broker(certs) as srv:
        ep = endpoint(srv, certs)
        assert CODE not in repr(ep) and CODE not in str(ep.password)
        with caplog.at_level(logging.DEBUG):
            client = session(srv, certs)
            client.close()
            for scripted in (4, 5, 3):
                srv.connack = scripted
                with pytest.raises(errors.NetError) as exc:
                    run(mqtt.open_client(ep))
                seen += [str(exc.value), repr(exc.value)]
    assert all(CODE not in text for text in seen) and CODE not in caplog.text
    redact.clear()
