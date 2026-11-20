# SPDX-License-Identifier: GPL-3.0-or-later
"""A minimal FTPS client (implicit TLS) as non-blocking generators (03 section 8.4). No ``bpy``, no threads, no ``ftplib``.

``ftplib`` blocks on every call and its ``FTP_TLS`` only speaks *explicit* TLS, so this module drives the protocol itself
on :mod:`~slicewright.core.network.nbsocket`: every operation is a generator whose ``yield`` is one bounded step (see
that module), run by the tick runner in Blender and by ``run_blocking`` in tests.

What it does, written from RFC 959 (FTP), RFC 4217 (FTP over TLS) and RFC 2428 for the reply shapes, and from the public
description of Bambu's LAN service (implicit TLS on port 990, user ``bblp``, password the access code), not from any other
client's code:

* **Implicit TLS** on the control connection: the handshake comes first and the certificate is checked by the endpoint's
  :class:`~slicewright.core.network.tls.TlsPolicy` (a pin, for a self-signed printer) **before the first byte is sent**,
  so a user name and password never reach a device that failed its check.
* **The data channel is TLS too** (``PBSZ 0`` then ``PROT P``). If the server refuses ``PROT P`` the transfer is
  refused: there is **no fallback to a clear data channel**. The data connection starts TLS only after the server has
  accepted the transfer command (as RFC 4217 servers expect), is checked with the same policy (the pin is compared
  before any file byte is sent) and **offers the control connection's TLS session for resumption**. Servers configured
  to require session reuse (the vsftpd ``require_ssl_reuse`` setting) refuse a data channel that does not resume it; a
  server that does not care ignores the offer. The session must come from the same ``SSLContext``, which
  :func:`~slicewright.core.network.nbsocket.start_tls` takes care of. Under TLS 1.3 the session is a ticket that the
  server sends after the handshake, which the client has received by the time it has read the greeting.
* **Passive mode** (``PASV``) only. The address in the ``227`` reply is **ignored**: the data connection goes to the address
  the control connection is connected to, so a printer (or something posing as one) cannot send the client to a third host.
* ``TYPE I``, ``STOR <name>`` streamed from a file in 64 KB slices, ``LIST`` for the connection test, ``QUIT``.
* After the last byte the data connection is closed with a TLS ``close_notify`` (best effort, never waiting for the
  peer's: some embedded servers never answer it) and the final ``226`` is awaited on the control connection.

Errors (``errors.py``): a refused login is :class:`LoginRejected` and **stops** (no retry, no other mode is tried);
``550``/``553`` to ``STOR`` is :class:`PermissionDenied`, ``452``/``552`` :class:`StorageFull`; a data connection that
cannot be opened or whose TLS is refused is :class:`DataChannelError`; TLS pin errors pass through untouched. Messages hold
the target and a reply code, never the server's own text, a command or the password.

The password is a :class:`~slicewright.core.redact.SecretStr`; ``reveal()`` is called in exactly one place (building the
``PASS`` line), into a local that is dropped at once. Nothing here logs.
"""
from __future__ import annotations

import errno
import os
import re
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Iterator

from ..redact import SecretStr
from ..ticking import IDLE
from .errors import (Cancelled, ConnectionLost, ConnectionRefused, DataChannelError, FtpError, InvalidRequest,
                     LimitExceeded, LoginRejected, NetError, PermissionDenied, ProtocolError, StorageFull, Timeout,
                     TlsError, TlsPinMismatch, TlsUnknownCertificate, TlsVerificationError, UploadError, Unreachable,
                     target)
from .nbsocket import CancelToken, Clock, Connection, Timeouts, connect, start_tls
from .resolve import Resolver, default_resolver
from .tls import TlsPolicy
from .webapi import Transfer, check_filename

SEND_SLICE = 64 * 1024
MAX_LINE = 4096
MAX_REPLY_LINES = 200
MAX_LISTING = 1024 * 1024
DEFAULT_PORT = 990
_CTL = re.compile(r"[\x00-\x1f\x7f]")
_PASV = re.compile(r"\((\d{1,3}),(\d{1,3}),(\d{1,3}),(\d{1,3}),(\d{1,3}),(\d{1,3})\)")
_CODE = re.compile(r"^(\d{3})([ -])")


@dataclass(frozen=True)
class FtpsEndpoint:
    """Where and how to talk to one FTPS server. ``tls`` is required: there is no plain FTP here. ``login_hint`` is
    appended to the message of a refused login (the product knows what to tell the user)."""
    host: str
    tls: TlsPolicy
    password: SecretStr | None = None
    port: int = DEFAULT_PORT
    user: str = "bblp"
    product: str = "The printer"
    login_hint: str = ""
    timeouts: Timeouts = Timeouts(connect=5.0, idle=15.0)
    upload_timeouts: Timeouts = Timeouts(connect=5.0, idle=60.0)      # the printer may think after the last byte
    resolver: Resolver = default_resolver
    clock: Clock = time.monotonic
    cancel: CancelToken | None = None

    def __repr__(self) -> str:
        return f"FtpsEndpoint({target(self.host, self.port)}, user={self.user!r}, password=***)"


@dataclass(frozen=True)
class FtpUpload:
    name: str
    path: str                     # where it is stored (the root: just the name)
    size: int


@dataclass(frozen=True)
class FtpCheck:
    """What :func:`check` found. ``data_ok`` is False (with ``data_problem``, a sentence) when login worked but a data
    channel could not be set up or listed; ``entries`` is the number of lines of the root listing."""
    data_ok: bool
    entries: int = 0
    data_problem: str = ""


class Reply:
    __slots__ = ("code", "lines")

    def __init__(self, code: int, lines: list[str]) -> None:
        self.code, self.lines = code, lines

    @property
    def text(self) -> str:
        return self.lines[0] if self.lines else ""


class _Control:
    """The control connection: commands out, replies in."""

    def __init__(self, conn: Connection, ep: FtpsEndpoint) -> None:
        self.conn, self.ep = conn, ep
        self.buf = bytearray()
        self.clock = ep.clock
        self.cancel = ep.cancel or CancelToken()
        self.where = target(conn.host, conn.port)

    def _idle_check(self, last: float, idle: float) -> None:
        if self.clock() - last > idle:
            raise Timeout(f"{self.where} did not respond for {idle:g} s", phase="idle", host=self.conn.host,
                          port=self.conn.port)

    def send(self, data: bytes, idle: float) -> Iterator:
        out, last = memoryview(data), self.clock()
        while len(out):
            self.cancel.check(self.conn.host, self.conn.port)
            n = self.conn.send(out)
            if n:
                out, last = out[n:], self.clock()
                yield None
            else:
                self._idle_check(last, idle)
                yield IDLE

    def command(self, line: str, idle: float) -> Iterator:
        """Send one command and return its (final) :class:`Reply`. ``line`` has no CR/LF."""
        if _CTL.search(line):
            raise InvalidRequest("A file name or command contains a control character")
        reply = yield from self._command_bytes(line.encode("utf-8") + b"\r\n", idle)
        return reply

    def _command_bytes(self, data: bytes, idle: float) -> Iterator:
        yield from self.send(data, idle)
        reply = yield from self.reply(idle)
        return reply

    def _line(self) -> str | None:
        end = self.buf.find(b"\n")
        if end < 0:
            if len(self.buf) > MAX_LINE:
                raise LimitExceeded(f"{self.where} sent an FTP reply line that is too long", host=self.conn.host,
                                    port=self.conn.port)
            return None
        raw = bytes(self.buf[:end]).rstrip(b"\r")
        del self.buf[:end + 1]
        if len(raw) > MAX_LINE:
            raise LimitExceeded(f"{self.where} sent an FTP reply line that is too long", host=self.conn.host,
                                port=self.conn.port)
        return raw.decode("latin-1")

    def reply(self, idle: float) -> Iterator:
        """Read one complete reply (single or multi-line)."""
        lines: list[str] = []
        code: int | None = None
        last = self.clock()
        while True:
            self.cancel.check(self.conn.host, self.conn.port)
            line = self._line()
            if line is not None:
                m = _CODE.match(line)
                if code is None:
                    if m is None:
                        raise ProtocolError(f"{self.where} did not answer like an FTP server",
                                            host=self.conn.host, port=self.conn.port)
                    code = int(m.group(1))
                    lines.append(line[4:])
                    if m.group(2) == " ":
                        return Reply(code, lines)
                else:
                    if len(lines) >= MAX_REPLY_LINES:
                        raise LimitExceeded(f"{self.where} sent an FTP reply with too many lines",
                                            host=self.conn.host, port=self.conn.port)
                    if m is not None and int(m.group(1)) == code and m.group(2) == " ":
                        lines.append(line[4:])
                        return Reply(code, lines)
                    lines.append(line)
                continue
            data = self.conn.recv()
            if data is None:
                self._idle_check(last, idle)
                yield IDLE
            elif data == b"":
                raise ConnectionLost(f"{self.where} closed the connection", host=self.conn.host, port=self.conn.port)
            else:
                self.buf += data
                last = self.clock()
                yield None

    def error(self, reply: Reply, doing: str) -> NetError:
        """The exception for a negative ``reply`` to ``doing`` (a phrase). Holds the code, never the server's text."""
        code, host, port = reply.code, self.conn.host, self.conn.port
        if code in (530, 532):
            return LoginRejected(f"{self.where} refused the login (FTP {code}). Check the access code{self.ep.login_hint}",
                                 host=host, port=port)
        if code in (452, 552):
            return StorageFull(f"{self.where} says its storage is full (FTP {code}) while {doing}. Free some space "
                               f"on the printer", code=code, host=host, port=port)
        if code in (550, 553) and doing.startswith("storing"):
            return PermissionDenied(f"{self.where} would not store the file (FTP {code}): the name or the place is "
                                    f"not allowed, or the storage is read-only", code=code, host=host, port=port)
        if code in (425, 426):
            return DataChannelError(f"{self.where} could not open or keep the data connection (FTP {code}) while "
                                    f"{doing}. A firewall on the passive port, or a data channel whose TLS session "
                                    f"the printer refused, are the usual causes", host=host, port=port)
        return FtpError(f"{self.where} answered FTP {code} while {doing}", code=code, host=host, port=port)


def _open(ep: FtpsEndpoint) -> Iterator:
    """Connect (the pin is checked here), read the greeting, log in and switch the data channel to TLS. Returns the
    :class:`_Control`. The caller closes ``control.conn``."""
    if ep.password is None or not ep.password.reveal():
        raise InvalidRequest("Set the access code of this printer before connecting")
    conn = yield from connect(ep.host, ep.port, tls=ep.tls, timeouts=ep.timeouts, cancel=ep.cancel, clock=ep.clock,
                              resolver=ep.resolver)
    ctl = _Control(conn, ep)
    try:
        idle = ep.timeouts.idle
        greeting = yield from ctl.reply(idle)
        if greeting.code != 220:
            raise ctl.error(greeting, "connecting")
        reply = yield from ctl.command(f"USER {ep.user}", idle)
        if reply.code == 331:
            secret = ep.password.reveal()                                  # the one place the secret is text
            if _CTL.search(secret):
                raise InvalidRequest("The access code contains a control character")
            line = b"PASS " + secret.encode("utf-8") + b"\r\n"
            secret = ""
            try:
                reply = yield from ctl._command_bytes(line, idle)
            finally:
                line = b""
        if reply.code not in (230, 202):
            raise ctl.error(reply, "logging in") if reply.code != 421 else FtpError(
                f"{ctl.where} is not accepting connections (FTP 421)", code=421, host=ep.host, port=ep.port)
        for command in ("PBSZ 0", "PROT P"):
            reply = yield from ctl.command(command, idle)
            if reply.code != 200:
                raise DataChannelError(f"{ctl.where} does not offer an encrypted data channel (FTP {reply.code} to "
                                       f"{command.split()[0]}); nothing is sent without it", host=ep.host, port=ep.port)
        reply = yield from ctl.command("TYPE I", idle)
        if reply.code != 200:
            raise ctl.error(reply, "selecting binary mode")
    except BaseException:
        conn.close()
        raise
    return ctl


def _parse_pasv(reply: Reply) -> int:
    m = _PASV.search(reply.text)
    if reply.code != 227 or m is None:
        raise ValueError
    parts = [int(g) for g in m.groups()]
    if any(p > 255 for p in parts):
        raise ValueError
    port = parts[4] * 256 + parts[5]
    if not 1 <= port <= 65535:
        raise ValueError
    return port


def _start_transfer(ctl: _Control, command: str, *, doing: str) -> Iterator:
    """``PASV``, open the data connection, send ``command``, wait for the server to accept it (``125``/``150``), then
    start TLS on the data connection (resuming the control connection's session) and check the peer. Returns the TLS
    data :class:`Connection`; the caller closes it."""
    ep = ctl.ep
    reply = yield from ctl.command("PASV", ctl.ep.timeouts.idle)
    try:
        port = _parse_pasv(reply)
    except ValueError:
        raise DataChannelError(f"{ctl.where} does not offer passive mode (FTP {reply.code} to PASV); this client "
                               f"cannot use the data channel without it", host=ep.host, port=ep.port) from None
    try:
        peer = ctl.conn.sock.getpeername()[0]        # the control connection's own address, never the one in the reply
    except OSError:
        peer = ep.host
    try:
        data = yield from connect(peer, port, tls=None, timeouts=ep.timeouts, cancel=ep.cancel, clock=ep.clock,
                                  resolver=ep.resolver)
    except (ConnectionRefused, Unreachable, ConnectionLost, Timeout) as exc:
        raise DataChannelError(f"The data connection of {ctl.where} could not be opened ({exc.kind}); a firewall "
                               f"may block the passive port", host=ep.host, port=ep.port) from None
    try:
        reply = yield from ctl.command(command, ep.timeouts.idle)
        if reply.code not in (125, 150):
            raise ctl.error(reply, doing)
        try:
            yield from start_tls(data, ep.tls, timeouts=ep.timeouts, cancel=ep.cancel, clock=ep.clock, reuse=ctl.conn)
        except (TlsUnknownCertificate, TlsPinMismatch, TlsVerificationError):
            raise
        except TlsError:
            raise DataChannelError(
                f"The TLS handshake of the data connection of {ctl.where} failed. The printer may require the TLS "
                f"session of the control connection to be resumed, or it uses another certificate",
                host=ep.host, port=ep.port) from None
        except (ConnectionLost, Timeout) as exc:
            raise DataChannelError(f"The data connection of {ctl.where} broke during its TLS handshake ({exc.kind})",
                                   host=ep.host, port=ep.port) from None
    except BaseException:
        data.close()
        raise
    return data


def _close_data(data: Connection, *, clean: bool) -> None:
    """End a TLS data connection. ``clean`` (every byte was sent): send ``close_notify`` if it can go out at once (best
    effort, no waiting for the peer's), then close. Otherwise (cancelled, failed) the connection is **aborted**: no
    ``close_notify``; the TCP connection is shut down at once, so the printer sees a truncated TLS stream. FTP has no length, so a clean TLS close is the
    end-of-file mark, and one after a partial file would make the printer store it as if it were whole. Never raises."""
    try:
        if data.closed:
            return
        if clean and data.tls:
            for _ in range(4):
                try:
                    data.sock.unwrap()
                    break
                except ssl.SSLWantWriteError:
                    continue
                except (ssl.SSLError, OSError, ValueError):
                    break
        elif not clean:
            try:
                data.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    except Exception:  # noqa: BLE001 - closing must never raise
        pass
    data.close()


def _quit(ctl: _Control) -> None:
    """Best effort ``QUIT`` (one send attempt, no wait), then close."""
    try:
        ctl.conn.send(b"QUIT\r\n")
    except NetError:
        pass
    ctl.conn.close()


def upload(ep: FtpsEndpoint, file_path: str, name: str, *, transfer: Transfer | None = None) -> Iterator:
    """Generator: store ``file_path`` in the server's root as ``name`` over FTPS; returns :class:`FtpUpload`. Raises
    ``InvalidRequest`` for a bad name before anything is sent, ``UploadError`` if the file cannot be read, and the errors
    named in the module docstring."""
    name = check_filename(name)
    try:
        size = os.path.getsize(file_path)
    except OSError as exc:
        raise UploadError(f"Cannot read {os.path.basename(file_path)!r} to send it "
                          f"({errno.errorcode.get(exc.errno, 'error')})") from None
    transfer = transfer if transfer is not None else Transfer()
    transfer.total, transfer.sent, transfer.phase = size, 0, "connecting"
    ctl = yield from _open(ep)
    try:
        data = yield from _start_transfer(ctl, f"STOR {name}", doing=f"storing {name}")
        complete = False
        try:
            yield from _send_file(ctl, data, file_path, size, transfer)
            complete = True
        finally:
            _close_data(data, clean=complete)
        transfer.phase = "waiting"
        final = yield from ctl.reply(ep.upload_timeouts.idle)
        if final.code not in (226, 250):
            raise ctl.error(final, f"storing {name}")
    finally:
        _quit(ctl)
    transfer.phase = "done"
    return FtpUpload(name, name, size)


def _send_file(ctl: _Control, data: Connection, file_path: str, size: int, transfer: Transfer) -> Iterator:
    ep = ctl.ep
    idle = ep.upload_timeouts.idle
    sent, last = 0, ep.clock()
    try:
        f = open(file_path, "rb")
    except OSError as exc:
        raise UploadError(f"Cannot read {os.path.basename(file_path)!r} to send it "
                          f"({errno.errorcode.get(exc.errno, 'error')})") from None
    with f:
        out = memoryview(b"")
        while sent < size:
            ctl.cancel.check(ep.host, ep.port)
            if ep.clock() - last > idle:
                raise Timeout(f"{ctl.where} did not take any data for {idle:g} s", phase="idle", host=ep.host,
                              port=ep.port)
            if not len(out):
                try:
                    chunk = f.read(min(SEND_SLICE, size - sent))
                except OSError as exc:
                    raise UploadError(f"Reading {os.path.basename(file_path)!r} failed "
                                      f"({errno.errorcode.get(exc.errno, 'error')})") from None
                if not chunk:
                    raise UploadError(f"{os.path.basename(file_path)!r} changed while it was being sent "
                                      f"(it became shorter)")
                out = memoryview(chunk)
            try:
                n = data.send(out)
            except (ConnectionLost, TlsError) as lost:
                raise (yield from _why_dropped(ctl, lost)) from None
            if n:
                out, sent, last = out[n:], sent + n, ep.clock()
                transfer.update(sent, size)
                yield min(0.99, sent / size) if size else None
            else:
                yield IDLE
    if not size:
        yield None


def _why_dropped(ctl: _Control, lost: NetError) -> Iterator:
    """The data connection dropped mid-transfer: the control connection may say why (``552``, ``426``...). Waits a
    moment for that reply; returns the exception to raise."""
    try:
        reply = yield from ctl.reply(2.0)
    except NetError as exc:
        if isinstance(exc, Cancelled):
            return exc
        return lost
    if reply.code >= 400:
        return ctl.error(reply, "storing the file")
    return lost


def check(ep: FtpsEndpoint) -> Iterator:
    """Generator for the connection test: connect (pin check), log in, switch the data channel to TLS, then list the root
    over a data connection to prove that works too. Returns :class:`FtpCheck`. A refused login, a TLS refusal or a cancel
    raises; a data channel that fails is *reported* in the result (login worked, so sending may still be tried)."""
    ctl = yield from _open(ep)
    try:
        try:
            data = yield from _start_transfer(ctl, "LIST", doing="listing the storage")
        except (DataChannelError, FtpError, PermissionDenied) as exc:
            return FtpCheck(False, 0, str(exc))
        try:
            entries = yield from _read_listing(ctl, data)
        except NetError as exc:
            if isinstance(exc, (Cancelled,)) or exc.kind in ("online_access", "tls_unknown", "tls_pin_mismatch"):
                raise
            return FtpCheck(False, 0, str(exc))
        finally:
            _close_data(data, clean=True)
        try:
            yield from ctl.reply(ep.timeouts.idle)             # the 226 (not important)
        except NetError as exc:
            if isinstance(exc, Cancelled):
                raise
        return FtpCheck(True, entries)
    finally:
        _quit(ctl)


def _read_listing(ctl: _Control, data: Connection) -> Iterator:
    """Read the listing to its end and return the number of lines; the text itself is dropped."""
    ep = ctl.ep
    total, lines, last = 0, 0, ep.clock()
    while True:
        ctl.cancel.check(ep.host, ep.port)
        chunk = data.recv()
        if chunk is None:
            if ep.clock() - last > ep.timeouts.idle:
                raise Timeout(f"{ctl.where} did not send the listing for {ep.timeouts.idle:g} s", phase="idle",
                              host=ep.host, port=ep.port)
            yield IDLE
        elif chunk == b"":
            return lines
        else:
            total += len(chunk)
            if total > MAX_LISTING:
                raise LimitExceeded(f"{ctl.where} sent a listing that is too long", host=ep.host, port=ep.port)
            lines += chunk.count(b"\n")
            last = ep.clock()
            yield None
