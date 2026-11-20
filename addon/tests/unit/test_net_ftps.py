# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network/ftps.py (implicit FTPS, as Bambu's LAN storage speaks it) against the in-process FakeFtpsServer."""
from __future__ import annotations

import logging
import ssl
import threading

import fake_net
import pytest
from fake_net import FakeFtpsServer
from slicewright.core import redact
from slicewright.core.network import errors, ftps
from slicewright.core.network.nbsocket import CancelToken, Timeouts, run_blocking
from slicewright.core.network.tls import MemoryPinStore, TlsPolicy, fingerprint_of
from slicewright.core.network.webapi import Transfer
from slicewright.core.redact import SecretStr
from slicewright.core.ticking import DONE, TickRunner

CODE = "AC-98765432"
FAST = Timeouts(connect=3.0, idle=3.0)


def fp(files):
    return fingerprint_of(ssl.PEM_cert_to_DER_cert(open(files[0]).read()))


def endpoint(srv, certs, *, code=CODE, pin=True, **kw):
    store = MemoryPinStore({"p": fp(certs[0])}) if pin else MemoryPinStore()
    kw.setdefault("timeouts", FAST)
    kw.setdefault("upload_timeouts", FAST)
    kw.setdefault("login_hint", ", and that the printer is in LAN Only mode with Developer Mode on")
    return ftps.FtpsEndpoint("127.0.0.1", TlsPolicy(mode="pin", pin_key="p", pin_store=store),
                             SecretStr(code) if code else None, port=srv.port, **kw)


def server(certs, **kw):
    return FakeFtpsServer(CODE, tls_files=certs[0], **kw)


def payload(tmp_path, size=100_000, name="part.gcode.3mf"):
    path = tmp_path / name
    path.write_bytes(bytes(range(256)) * (size // 256 + 1))
    path.write_bytes(path.read_bytes()[:size])
    return str(path)


def run(gen):
    return run_blocking(gen)


# -- the happy path ----------------------------------------------------------------------------------------------

def test_upload_logs_in_switches_the_data_channel_to_tls_and_stores_the_file_byte_for_byte(tmp_path, certs):
    path = payload(tmp_path, 700_000)
    transfer = Transfer()
    with server(certs) as srv:
        result = run(ftps.upload(endpoint(srv, certs), path, "my part.gcode.3mf", transfer=transfer))
    assert (result.name, result.path, result.size) == ("my part.gcode.3mf", "my part.gcode.3mf", 700_000)
    assert srv.commands == ["USER bblp", "PASS ***", "PBSZ 0", "PROT P", "TYPE I", "PASV", "STOR my part.gcode.3mf", "QUIT"]
    assert srv.logins == [("bblp", True)] and srv.prot_levels == ["P"]
    (stored,) = srv.stored
    assert stored.content == open(path, "rb").read() and stored.clean_end            # whole file, then a TLS close_notify
    assert (transfer.phase, transfer.sent) == ("done", transfer.total) and transfer.total == 700_000
    assert srv.errors == []


def test_the_data_channel_resumes_the_control_sessions_tls_session(tmp_path, certs):
    with server(certs, require_session_reuse=True) as srv:           # refuses a data channel that starts a new session
        run(ftps.upload(endpoint(srv, certs), payload(tmp_path), "a.gcode.3mf"))
    assert [s.session_reused for s in srv.stored] == [True] and srv.errors == []


def test_an_empty_file_and_a_multi_line_greeting(tmp_path, certs):
    empty = tmp_path / "e.3mf"
    empty.write_bytes(b"")
    with server(certs) as srv:
        srv.greeting = ["Welcome", "to the fake", "printer"]
        result = run(ftps.upload(endpoint(srv, certs), str(empty), "e.3mf"))
    assert result.size == 0 and srv.stored[0].content == b"" and srv.stored[0].clean_end


def test_the_address_in_the_pasv_reply_is_ignored(tmp_path, certs):
    with server(certs) as srv:
        srv.pasv_ip = "10.255.255.1"                              # unreachable: a client that believed it would hang
        run(ftps.upload(endpoint(srv, certs), payload(tmp_path), "a.gcode.3mf"))
    assert len(srv.stored) == 1


def test_the_upload_runs_under_the_tick_runner(tmp_path, certs):
    got = []
    with server(certs) as srv:
        runner = TickRunner()
        task = runner.submit("send", ftps.upload(endpoint(srv, certs), payload(tmp_path, 2_000_000), "a.gcode.3mf"),
                             on_done=got.append)
        runner.run_until_idle()
    assert task.state == DONE and got[0].size == 2_000_000


def test_progress_is_reported_while_the_file_moves(tmp_path, certs):
    seen = []
    transfer = Transfer()
    with server(certs, read_delay=0.001) as srv:
        gen = ftps.upload(endpoint(srv, certs), payload(tmp_path, 1_000_000), "a.gcode.3mf", transfer=transfer)
        for value in gen:
            if isinstance(value, float):
                seen.append(value)
    assert seen and seen == sorted(seen) and 0 < seen[0] < 1 and max(seen) <= 0.99
    assert transfer.phase == "done"


# -- failures ----------------------------------------------------------------------------------------------------

def test_a_wrong_access_code_stops_the_login_with_no_retry_and_points_to_developer_mode(tmp_path, certs):
    with server(certs) as srv:
        with pytest.raises(errors.LoginRejected, match="Developer Mode") as exc:
            run(ftps.upload(endpoint(srv, certs, code="not-the-code"), payload(tmp_path), "a.gcode.3mf"))
    assert srv.logins == [("bblp", False)]                        # exactly one attempt
    assert not any(c.startswith(("PBSZ", "PROT", "PASV", "STOR")) for c in srv.commands)
    assert "not-the-code" not in str(exc.value) and "530" in str(exc.value)


def test_a_missing_access_code_is_refused_before_connecting(tmp_path, certs):
    with server(certs) as srv:
        with pytest.raises(errors.InvalidRequest, match="access code"):
            run(ftps.upload(endpoint(srv, certs, code=None), payload(tmp_path), "a.gcode.3mf"))
    assert srv.commands == [] and srv.raw_received == b""


@pytest.mark.parametrize("name", ["", "a/b.3mf", "a\\b.3mf", ".hidden.3mf", "x\r\nDELE y.3mf"])
def test_bad_file_names_are_refused_before_anything_is_sent(tmp_path, certs, name):
    with server(certs) as srv:
        with pytest.raises(errors.InvalidRequest):
            run(ftps.upload(endpoint(srv, certs), payload(tmp_path), name))
    assert srv.commands == []


@pytest.mark.parametrize("status,kind", [(550, errors.PermissionDenied), (553, errors.PermissionDenied),
                                         (552, errors.StorageFull), (452, errors.StorageFull),
                                         (425, errors.DataChannelError), (500, errors.FtpError)])
def test_stor_refusals_map_to_the_error_taxonomy(tmp_path, certs, status, kind):
    with server(certs) as srv:
        srv.store_status = status
        with pytest.raises(kind) as exc:
            run(ftps.upload(endpoint(srv, certs), payload(tmp_path), "a.gcode.3mf"))
    assert str(status) in str(exc.value) and "Scripted" not in str(exc.value) and srv.stored == []


def test_storage_full_after_the_data_was_sent(tmp_path, certs):
    with server(certs) as srv:
        srv.final_status = 552
        with pytest.raises(errors.StorageFull, match="storage is full"):
            run(ftps.upload(endpoint(srv, certs), payload(tmp_path), "a.gcode.3mf"))


def test_the_connection_dropped_mid_transfer_says_why_when_the_control_channel_does(tmp_path, certs):
    with server(certs) as srv:
        srv.drop_after = 200_000
        with pytest.raises(errors.DataChannelError):
            run(ftps.upload(endpoint(srv, certs), payload(tmp_path, 3_000_000), "a.gcode.3mf"))
    with server(certs) as srv:
        srv.drop_after = 200_000
        srv.drop_reply = "552 Disk full"
        with pytest.raises(errors.StorageFull):
            run(ftps.upload(endpoint(srv, certs), payload(tmp_path, 3_000_000), "a.gcode.3mf"))


def test_a_server_that_refuses_an_encrypted_data_channel_gets_nothing_in_the_clear(tmp_path, certs):
    with server(certs) as srv:
        srv.refuse_prot_p = True
        with pytest.raises(errors.DataChannelError, match="encrypted data channel"):
            run(ftps.upload(endpoint(srv, certs), payload(tmp_path), "a.gcode.3mf"))
    assert not any(c.startswith(("PASV", "STOR")) for c in srv.commands) and srv.stored == []


def test_no_passive_mode_is_a_data_channel_error(tmp_path, certs):
    with server(certs) as srv:
        srv.bad_pasv = True
        with pytest.raises(errors.DataChannelError, match="passive mode"):
            run(ftps.upload(endpoint(srv, certs), payload(tmp_path), "a.gcode.3mf"))


def test_a_data_connection_that_the_server_refuses_to_resume_is_explained(tmp_path, certs):
    with server(certs) as srv:
        srv.require_session_reuse = True
        ep = endpoint(srv, certs)
        orig = ftps.start_tls

        def no_resume(conn, policy, **kw):
            kw["reuse"] = None                                    # a client that forgot to offer the session
            return orig(conn, policy, **kw)
        ftps.start_tls = no_resume
        try:
            with pytest.raises(errors.DataChannelError, match="TLS session"):
                run(ftps.upload(ep, payload(tmp_path), "a.gcode.3mf"))
        finally:
            ftps.start_tls = orig
    assert srv.stored == []


def test_connection_refused_and_a_server_that_stops_reading(tmp_path, certs):
    ep = ftps.FtpsEndpoint("127.0.0.1", TlsPolicy(mode="pin", pin_key="p", pin_store=MemoryPinStore()),
                           SecretStr(CODE), port=fake_net.free_port(), timeouts=FAST)
    with pytest.raises(errors.ConnectionRefused):
        run(ftps.upload(ep, payload(tmp_path), "a.gcode.3mf"))
    with server(certs, read_delay=3.0, read_chunk=4096) as srv:
        with pytest.raises(errors.Timeout):
            run(ftps.upload(endpoint(srv, certs, upload_timeouts=Timeouts(connect=2, idle=0.4)),
                            payload(tmp_path, 4_000_000), "a.gcode.3mf"))


def test_something_that_is_not_ftp_is_a_protocol_error(certs):
    # a TLS server that answers with a non-FTP line
    import socket
    import threading as th
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*certs[0])
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        try:
            tls = ctx.wrap_socket(conn, server_side=True)
            tls.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            tls.recv(10)
        except OSError:
            pass
    t = th.Thread(target=serve, daemon=True)
    t.start()
    ep = ftps.FtpsEndpoint("127.0.0.1", TlsPolicy(mode="pin", pin_key="p", pin_store=MemoryPinStore({"p": fp(certs[0])})),
                           SecretStr(CODE), port=listener.getsockname()[1], timeouts=FAST)
    with pytest.raises(errors.ProtocolError, match="did not answer like an FTP server"):
        run(ftps.upload(ep, __file__, "a.gcode.3mf"))
    listener.close()


# -- certificate pinning ------------------------------------------------------------------------------------------

def test_an_unknown_certificate_stops_before_the_access_code_is_sent_then_works_once_trusted(tmp_path, certs):
    store = MemoryPinStore()
    ep = ftps.FtpsEndpoint("127.0.0.1", TlsPolicy(mode="pin", pin_key="p", pin_store=store), SecretStr(CODE), timeouts=FAST)
    with server(certs) as srv:
        ep = ftps.FtpsEndpoint("127.0.0.1", ep.tls, SecretStr(CODE), port=srv.port, timeouts=FAST, upload_timeouts=FAST)
        with pytest.raises(errors.TlsUnknownCertificate) as exc:
            run(ftps.upload(ep, payload(tmp_path), "a.gcode.3mf"))
        assert exc.value.fingerprint == fp(certs[0])
        assert srv.raw_received == b"" and srv.commands == []     # not even a USER: nothing before the user agrees
        store.set("p", exc.value.fingerprint)                      # the user confirmed
        run(ftps.upload(ep, payload(tmp_path), "a.gcode.3mf"))
    assert srv.logins == [("bblp", True)]


def test_an_imposter_with_another_certificate_never_receives_the_access_code(tmp_path, certs):
    with FakeFtpsServer(CODE, tls_files=certs[1]) as imposter:      # certs[1] is not the pinned certificate
        with pytest.raises(errors.TlsPinMismatch) as exc:
            run(ftps.upload(endpoint(imposter, certs), payload(tmp_path), "a.gcode.3mf"))
        with pytest.raises(errors.TlsPinMismatch):
            run(ftps.check(endpoint(imposter, certs)))
    assert exc.value.expected == fp(certs[0]) and exc.value.got == fp(certs[1])
    assert imposter.raw_received == b"" and imposter.commands == [] and imposter.logins == []
    assert CODE not in str(exc.value)


def test_the_data_channel_is_pinned_too(tmp_path, certs):
    """A server whose data channel shows another certificate than its control channel does not get the file."""
    with server(certs) as srv:
        other = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        other.load_cert_chain(*certs[1])
        srv_ctx_data = other
        original = srv._ctx

        class Swap:
            """First wrap uses the pinned certificate, the next (the data connection) another one."""
            calls = 0

            def wrap_socket(self, *a, **kw):
                Swap.calls += 1
                return (original if Swap.calls == 1 else srv_ctx_data).wrap_socket(*a, **kw)
        srv._ctx = Swap()
        with pytest.raises(errors.TlsPinMismatch):
            run(ftps.upload(endpoint(srv, certs), payload(tmp_path), "a.gcode.3mf"))
    assert all(s.content == b"" for s in srv.stored)             # no file byte reached the other certificate


# -- cancel -------------------------------------------------------------------------------------------------------

def test_cancel_mid_upload_drops_the_data_connection_without_a_clean_close(tmp_path, certs):
    token = CancelToken()
    transfer = Transfer()
    with server(certs, read_delay=0.02, slow_bytes=100_000) as srv:
        gen = ftps.upload(endpoint(srv, certs, cancel=token), payload(tmp_path, 4_000_000), "a.gcode.3mf", transfer=transfer)
        with pytest.raises(errors.Cancelled):
            while True:
                next(gen)
                if transfer.sent > 300_000:
                    token.cancel()
        for _ in range(150):
            if srv.stored:
                break
            threading.Event().wait(0.02)
    assert 0 < transfer.sent < transfer.total
    assert [s.clean_end for s in srv.stored] == [False] and srv.aborted_transfers == 1      # a truncation, not an EOF
    assert len(srv.stored[0].content) < 4_000_000


def test_closing_the_generator_cancels_the_same_way(tmp_path, certs):
    transfer = Transfer()
    with server(certs, read_delay=0.02, slow_bytes=100_000) as srv:
        gen = ftps.upload(endpoint(srv, certs), payload(tmp_path, 4_000_000), "a.gcode.3mf", transfer=transfer)
        while transfer.sent < 300_000:
            next(gen)
        gen.close()
        for _ in range(150):
            if srv.aborted_transfers:
                break
            threading.Event().wait(0.02)
    assert srv.aborted_transfers == 1 and srv.connections_closed_by_client >= 1
    assert srv.stored and not srv.stored[0].clean_end


# -- the connection test ------------------------------------------------------------------------------------------

def test_check_logs_in_and_lists_the_root_over_a_tls_data_channel(certs):
    with server(certs, require_session_reuse=True) as srv:
        info = run(ftps.check(endpoint(srv, certs)))
    assert (info.data_ok, info.entries) == (True, 2)
    assert srv.commands[-3:] == ["PASV", "LIST", "QUIT"] and srv.stored == []


def test_check_reports_a_broken_data_channel_but_a_refused_login_raises(certs):
    with server(certs) as srv:
        srv.bad_pasv = True
        info = run(ftps.check(endpoint(srv, certs)))
        assert not info.data_ok and "passive mode" in info.data_problem
    with server(certs) as srv:
        with pytest.raises(errors.LoginRejected):
            run(ftps.check(endpoint(srv, certs, code="wrong-code")))


# -- the access code is nowhere ------------------------------------------------------------------------------------

def test_the_access_code_never_appears_in_messages_reprs_results_or_logs(tmp_path, certs, caplog):
    redact.register(CODE)
    seen: list[str] = []
    with server(certs) as srv:
        ep = endpoint(srv, certs)
        assert CODE not in repr(ep) and CODE not in str(ep.password)
        with caplog.at_level(logging.DEBUG):
            result = run(ftps.upload(ep, payload(tmp_path), "a.gcode.3mf"))
            seen += [repr(result), repr(run(ftps.check(ep)))]
            for status in (550, 552, 425, 500):
                srv.store_status = status
                with pytest.raises(errors.NetError) as exc:
                    run(ftps.upload(ep, payload(tmp_path), "a.gcode.3mf"))
                seen += [str(exc.value), repr(exc.value)]
            srv.store_status = None
            srv.password = "different-code"
            with pytest.raises(errors.LoginRejected) as exc:
                run(ftps.upload(ep, payload(tmp_path), "a.gcode.3mf"))
            seen += [str(exc.value), repr(exc.value)]
        assert all(CODE not in c for c in srv.commands)            # (the fake masks PASS; this guards the others)
    assert all(CODE not in text for text in seen) and CODE not in caplog.text
    redact.clear()
