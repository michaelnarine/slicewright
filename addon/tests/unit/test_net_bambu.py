# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network/bambu.py: Bambu LAN mode (FTPS now, MQTT in the next layer) against the in-process fakes."""
from __future__ import annotations

import ssl

import pytest
from fake_net import FakeFtpsServer
from slicewright.core.network import bambu, errors
from slicewright.core.network.nbsocket import Timeouts, run_blocking
from slicewright.core.network.tls import MemoryPinStore, TlsPolicy, fingerprint_of
from slicewright.core.redact import SecretStr

CODE = "AC-98765432"
FAST = Timeouts(3.0, 3.0)


def fp(files):
    return fingerprint_of(ssl.PEM_cert_to_DER_cert(open(files[0]).read()))


def endpoint(srv, certs, pinned=0, code=CODE):
    store = MemoryPinStore({"p": fp(certs[pinned])})
    return bambu.BambuEndpoint("127.0.0.1", TlsPolicy(mode="pin", pin_key="p", pin_store=store),
                               SecretStr(code), "01P00A123456789", srv.port, timeouts=FAST, upload_timeouts=FAST)


def test_the_connection_test_says_what_worked_and_labels_the_mode(certs):
    with FakeFtpsServer(CODE, tls_files=certs[0]) as srv:
        info = run_blocking(bambu.test_connection(endpoint(srv, certs)))
    assert info.summary() == ("Bambu LAN mode (unsupported by Bambu): access code accepted over FTPS, certificate "
                              "trusted, storage reachable (2 entries in its root)")


def test_a_broken_data_channel_is_reported_in_the_summary_not_raised(certs):
    with FakeFtpsServer(CODE, tls_files=certs[0]) as srv:
        srv.bad_pasv = True
        info = run_blocking(bambu.test_connection(endpoint(srv, certs)))
    assert not info.data_ok and "the data channel failed" in info.summary() and "passive mode" in info.summary()


def test_a_refused_login_stops_and_points_to_developer_mode_without_a_retry(certs):
    with FakeFtpsServer(CODE, tls_files=certs[0]) as srv:
        with pytest.raises(errors.LoginRejected) as exc:
            run_blocking(bambu.test_connection(endpoint(srv, certs, code="not-the-code")))
    text = str(exc.value)
    assert "Developer Mode" in text and "LAN Only" in text and "not-the-code" not in text
    assert srv.logins == [("bblp", False)]


def test_the_certificate_must_be_the_pinned_one_for_the_test_and_the_upload(tmp_path, certs):
    f = tmp_path / "a.gcode.3mf"
    f.write_bytes(b"x" * 1000)
    with FakeFtpsServer(CODE, tls_files=certs[1]) as imposter:
        for gen in (bambu.test_connection(endpoint(imposter, certs)),
                    bambu.upload(endpoint(imposter, certs), str(f), "a.gcode.3mf")):
            with pytest.raises(errors.TlsPinMismatch):
                run_blocking(gen)
    assert imposter.raw_received == b"" and imposter.logins == []


def test_the_endpoint_cannot_be_built_without_pinning_and_hides_the_code(certs):
    with pytest.raises(ValueError, match="pinning"):
        bambu.BambuEndpoint("h", TlsPolicy(), SecretStr(CODE))
    ep = bambu.BambuEndpoint("h", TlsPolicy(mode="pin", pin_key="k", pin_store=MemoryPinStore()), SecretStr(CODE))
    assert CODE not in repr(ep) and "***" in repr(ep) and CODE not in repr(ep.ftps())
    assert (ep.port, ep.mqtt_port, bambu.USER) == (990, 8883, "bblp")
