# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-blocking TCP and TLS connections driven in small steps (03 section 8.6). No ``bpy``, no threads.

Every operation in this package is a **generator**: each ``yield`` ends one step that did a bounded amount of
non-blocking work (one ``connect_ex`` poll, one handshake attempt, one ``send`` or ``recv``). A step yields
:data:`~slicewright.core.ticking.IDLE` when the socket would block and ``None`` (or a progress value) when it moved
something, and the generator's ``return`` value is the result. The same generator runs

* under :class:`~slicewright.core.ticking.TickRunner`, from a Blender timer (closing the generator, which closes the
  socket, is how a task is cancelled), or
* blocking, through :func:`run_blocking`, in unit tests and scripts.

The one call that can block is host-name resolution (``getaddrinfo`` has no timeout and no non-blocking form, and the
add-on may not use threads): a numeric address skips it, and the resolver is injectable. Everything else never waits.
"""
from __future__ import annotations

import errno
import ipaddress
import select
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Callable, Generator, Iterator

from ..ticking import IDLE
from .access import require_network
from .errors import (Cancelled, ConnectionLost, ConnectionRefused, NetError, ResolveError, Timeout, TlsError,
                     TlsUnknownCertificate, Unreachable, target)
from .tls import PIN, MemoryPinStore, TlsPolicy, explain_ssl_error

RECV_SIZE = 64 * 1024

_IN_PROGRESS = {errno.EINPROGRESS, errno.EALREADY, errno.EWOULDBLOCK, errno.EAGAIN, errno.EINTR,
                getattr(errno, "WSAEWOULDBLOCK", errno.EINPROGRESS)}
_UNREACHABLE = {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN, errno.ENETDOWN, errno.EADDRNOTAVAIL}

Clock = Callable[[], float]
Resolver = Callable[[str, int], "list[tuple]"]


@dataclass(frozen=True)
class Timeouts:
    """``connect`` covers TCP and the TLS handshake together; ``idle`` is how long nothing may move in either
    direction; ``total`` (optional) caps the whole exchange."""
    connect: float = 5.0
    idle: float = 15.0
    total: float | None = None


class CancelToken:
    """Set from the UI (or a test); checked at every step. Safe to share with a TickRunner task."""

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def check(self, host: str = "", port: int | None = None) -> None:
        if self.cancelled:
            raise Cancelled(host=host, port=port)


def default_resolver(host: str, port: int) -> list[tuple]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def run_blocking(gen: Generator, *, sleep: float = 0.001):
    """Drive ``gen`` to completion on this thread (tests, scripts) and return its result. A step that found nothing
    to do sleeps ``sleep`` seconds. The generator is closed on the way out, whatever happens."""
    try:
        while True:
            value = next(gen)
            if value is IDLE:
                time.sleep(sleep)
    except StopIteration as stop:
        return stop.value
    finally:
        gen.close()


class Connection:
    """An established connection. ``recv`` and ``send`` never block."""

    def __init__(self, sock: socket.socket, host: str, port: int, *, tls: bool = False,
                 peer_fingerprint: str | None = None) -> None:
        self.sock = sock
        self.host = host
        self.port = port
        self.tls = tls
        self.peer_fingerprint = peer_fingerprint
        self.closed = False

    def recv(self, size: int = RECV_SIZE) -> bytes | None:
        """Up to ``size`` bytes; ``None`` if nothing is available yet; ``b""`` at end of stream."""
        try:
            data = self.sock.recv(size)
        except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError, InterruptedError):
            return None
        except ssl.SSLZeroReturnError:
            return b""
        except ssl.SSLEOFError:
            return b""                       # the peer closed without a TLS close_notify
        except ssl.SSLError as exc:
            raise explain_ssl_error(exc, host=self.host, port=self.port) from None
        except OSError as exc:
            raise ConnectionLost(f"The connection to {target(self.host, self.port)} was lost "
                                 f"({errno.errorcode.get(exc.errno, 'error')})",
                                 host=self.host, port=self.port) from None
        return data

    def send(self, data) -> int:
        """Bytes accepted by the socket (maybe fewer than ``len(data)``); ``0`` if it would block. After a ``0``
        from a TLS socket the next call must offer the same bytes again (the caller keeps its buffer)."""
        try:
            return self.sock.send(data)
        except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError, InterruptedError):
            return 0
        except ssl.SSLError as exc:
            raise explain_ssl_error(exc, host=self.host, port=self.port) from None
        except OSError as exc:
            raise ConnectionLost(f"The connection to {target(self.host, self.port)} was lost "
                                 f"({errno.errorcode.get(exc.errno, 'error')})",
                                 host=self.host, port=self.port) from None

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.sock.close()
            except OSError:
                pass

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _addresses(host: str, port: int, resolver: Resolver) -> list[tuple]:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:                                    # a numeric address needs no resolver
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 0, "", (host, port, 0, 0) if family == socket.AF_INET6 else (host, port))]
    try:
        infos = resolver(host, port)
    except (socket.gaierror, UnicodeError, OSError):
        raise ResolveError(f"The host name {host!r} did not resolve", host=host, port=port) from None
    if not infos:
        raise ResolveError(f"The host name {host!r} did not resolve", host=host, port=port)
    return infos


def connect(host: str, port: int, *, tls: TlsPolicy | None = None, timeouts: Timeouts = Timeouts(),
            cancel: CancelToken | None = None, clock: Clock = time.monotonic,
            resolver: Resolver = default_resolver) -> Iterator:
    """Generator: open a TCP connection and, with ``tls``, complete (and check) the TLS handshake. Returns a
    :class:`Connection`. ``timeouts.connect`` bounds the lot."""
    require_network()                        # the online-access gate: no socket without it (access.py)
    cancel = cancel or CancelToken()
    deadline = clock() + timeouts.connect
    cancel.check(host, port)
    infos = _addresses(host, port, resolver)
    yield None
    last: NetError | None = None
    for family, socktype, proto, _canon, addr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.setblocking(False)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            yield from _tcp_connect(sock, addr, host, port, deadline, cancel, clock, timeouts)
            if tls is None:
                return Connection(sock, host, port)
            ssock, fingerprint = yield from _handshake(sock, tls, host, port, deadline, cancel, clock)
            return Connection(ssock, host, port, tls=True, peer_fingerprint=fingerprint)
        except (ConnectionRefused, Unreachable) as exc:
            sock.close()
            last = exc                       # try the next address, if any
        except BaseException:
            sock.close()
            raise
    assert last is not None
    raise last


def _expired(deadline: float, clock: Clock, host: str, port: int, what: str) -> None:
    if clock() >= deadline:
        raise Timeout(f"Connecting to {target(host, port)} timed out ({what})", phase="connect", host=host, port=port)


def _tcp_connect(sock, addr, host, port, deadline, cancel, clock, timeouts) -> Iterator:
    rc = sock.connect_ex(addr)
    while True:
        cancel.check(host, port)
        if rc not in _IN_PROGRESS and rc != 0:
            _raise_connect_error(rc, host, port)
        if rc == 0:
            return
        _expired(deadline, clock, host, port, "no answer")
        try:
            _, writable, failed = select.select([], [sock], [sock], 0)
        except (OSError, ValueError):
            writable, failed = [], [sock]
        if writable or failed:
            rc = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if rc != 0:
                _raise_connect_error(rc, host, port)
            return
        yield IDLE


def _raise_connect_error(code: int, host: str, port: int) -> None:
    name = target(host, port)
    if code == errno.ECONNREFUSED:
        raise ConnectionRefused(f"{name} refused the connection (is the service running and the port right?)",
                                host=host, port=port)
    if code in _UNREACHABLE:
        raise Unreachable(f"{name} is not reachable from this computer", host=host, port=port)
    if code == errno.ETIMEDOUT:
        raise Timeout(f"Connecting to {name} timed out", phase="connect", host=host, port=port)
    raise ConnectionLost(f"Could not connect to {name} ({errno.errorcode.get(code, 'error')})", host=host, port=port)


def _handshake(sock, policy: TlsPolicy, host, port, deadline, cancel, clock) -> Iterator:
    ctx = policy.make_context()
    try:
        ssock = ctx.wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
    except ssl.SSLError as exc:
        raise explain_ssl_error(exc, host=host, port=port) from None
    try:
        while True:
            cancel.check(host, port)
            try:
                ssock.do_handshake()
                break
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                _expired(deadline, clock, host, port, "TLS handshake")
                yield IDLE
            except ssl.SSLError as exc:
                raise explain_ssl_error(exc, host=host, port=port) from None
            except OSError as exc:
                raise ConnectionLost(f"The TLS handshake with {target(host, port)} was cut off "
                                     f"({errno.errorcode.get(exc.errno, 'error')})", host=host, port=port) from None
        fingerprint = policy.check_peer(ssock.getpeercert(binary_form=True), host=host, port=port)
        return ssock, fingerprint
    except BaseException:
        ssock.close()
        raise


def fetch_fingerprint(host: str, port: int, *, timeouts: Timeouts = Timeouts(), cancel: CancelToken | None = None,
                      clock: Clock = time.monotonic, resolver: Resolver = default_resolver) -> Iterator:
    """Generator: connect with TLS, trusting nothing, and return the leaf SHA-256 the device presents, so the UI can
    show it for the user to confirm before it is pinned. Nothing is sent; the connection is closed."""
    probe = TlsPolicy(mode=PIN, pin_key="probe", pin_store=MemoryPinStore())    # an empty store: always "unknown"
    try:
        conn = yield from connect(host, port, tls=probe, timeouts=timeouts, cancel=cancel, clock=clock,
                                  resolver=resolver)
    except TlsUnknownCertificate as unknown:
        return unknown.fingerprint
    conn.close()                              # unreachable with an empty store; never leave a connection open
    raise TlsError(f"{target(host, port)} could not be probed", host=host, port=port)
