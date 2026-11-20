# SPDX-License-Identifier: GPL-3.0-or-later
"""Host-name resolution that never blocks the caller (03 section 8.6). No ``bpy``.

``getaddrinfo`` has no timeout and no non-blocking form, and a ``.local`` name that mDNS cannot answer can keep it
waiting for many seconds. So a host *name* is resolved in a short-lived daemon **worker thread**, and :func:`resolve` is a
generator that yields :data:`~slicewright.core.ticking.IDLE` until the thread has an answer, the timeout passes or the
operation is cancelled. This is the add-on's one use of a thread and the one place the code-structure test allows it: the
thread runs only the resolver (``socket.getaddrinfo`` by default), never touches ``bpy``, a task or any shared state but
its own result box, and cannot outlive the process. A lookup the OS never finishes is abandoned, not killed: the thread
ends when the OS gives up, and at most :data:`MAX_STUCK` abandoned lookups are tolerated before new ones are refused.

IP literals skip all of this and are answered at once.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from typing import Callable, Iterator

from ..ticking import IDLE
from .errors import Cancelled, ResolveError

MAX_STUCK = 8                    # lookups the OS has not answered yet; beyond this, refuse to start another
DEFAULT_TIMEOUT = 4.0

Resolver = Callable[[str, int], "list[tuple]"]


def default_resolver(host: str, port: int) -> list[tuple]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


class _Box:
    """What the worker hands back. Written once by the worker, read by the generator; ``done`` is set last."""

    __slots__ = ("done", "infos", "failed")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.infos: list[tuple] | None = None
        self.failed = False


_lock = threading.Lock()
_live: set[_Box] = set()         # lookups whose thread has not returned (a stuck one stays here)


def stuck_lookups() -> int:
    """How many lookup threads have not returned yet (tests, diagnostics)."""
    with _lock:
        return len(_live)


def is_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def literal_addresses(host: str, port: int) -> list[tuple]:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 0, "", (host, port, 0, 0) if family == socket.AF_INET6 else (host, port))]


def _work(resolver: Resolver, host: str, port: int, box: _Box) -> None:
    try:
        box.infos = list(resolver(host, port))
    except BaseException:  # noqa: BLE001 - the thread must end quietly; the generator reports "did not resolve"
        box.failed = True
    finally:
        with _lock:
            _live.discard(box)
        box.done.set()


def _not_resolved(host: str, port: int | None, why: str = "did not resolve") -> ResolveError:
    return ResolveError(f"The host name {host!r} {why}", host=host, port=port)


def resolve(host: str, port: int, *, resolver: Resolver = default_resolver, timeout: float = DEFAULT_TIMEOUT,
            cancel=None, clock: Callable[[], float] = time.monotonic) -> Iterator:
    """Generator: the addresses of ``host`` (``getaddrinfo`` tuples). An IP literal returns at once. A name is looked up
    in a worker thread; this yields ``IDLE`` while it waits, raises :class:`ResolveError` when the lookup fails or takes
    longer than ``timeout`` seconds, and :class:`~slicewright.core.network.errors.Cancelled` when ``cancel`` is set (or
    the generator is closed: the thread is then left to finish on its own and its answer is dropped)."""
    if is_literal(host):
        return literal_addresses(host, port)
    box = _Box()
    with _lock:
        if len(_live) >= MAX_STUCK:
            raise _not_resolved(host, port, "could not be looked up: too many lookups are still waiting on the system "
                                            "resolver. Use the printer's IP address")
        _live.add(box)
    try:
        threading.Thread(target=_work, args=(resolver, host, port, box), name="slicewright-resolve",
                         daemon=True).start()
    except RuntimeError:                          # cannot start a thread: no lookup
        with _lock:
            _live.discard(box)
        raise _not_resolved(host, port, "could not be looked up") from None
    deadline = clock() + timeout
    while not box.done.is_set():
        if cancel is not None:
            cancel.check(host, port)
        if clock() >= deadline:
            raise _not_resolved(host, port, f"did not resolve within {timeout:g} s (for a .local name, use the "
                                            f"printer's IP address)")
        yield IDLE
    if cancel is not None:
        cancel.check(host, port)
    if box.failed or not box.infos:
        raise _not_resolved(host, port)
    return box.infos
