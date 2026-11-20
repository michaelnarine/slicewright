# SPDX-License-Identifier: GPL-3.0-or-later
"""A small HTTP/1.1 client on non-blocking sockets, advanced in steps (03 sections 8.2, 8.3 and 8.6). No ``bpy``.

One request per connection (``Connection: close``). :func:`request` is a generator (see
:mod:`slicewright.core.network.nbsocket` for how steps, ``IDLE`` and cancellation work) that returns a
:class:`Response`. It

* streams a request body (a :class:`~slicewright.core.network.multipart.MultipartBody` reads its file one chunk at a
  time) and reads the response **while** sending, so a server that answers ``401`` or ``413`` early is heard and the
  upload stops;
* never trusts the peer's sizes: the response head, the number of headers and the body have limits, enforced as bytes
  arrive (:class:`Limits`);
* follows no redirect unless told to, and then only to the same host, never from HTTPS to HTTP, and never replays a
  ``POST`` after a ``301``/``302``/``303`` (that would silently turn an upload into a ``GET``);
* sends the request only after the TLS policy accepted the peer (pinned fingerprint or verified chain), so headers such
  as an API key never go to a device that failed its check.

Error messages hold the target and the status line only, never a header, a query string or a body.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urljoin, urlsplit

from ..ticking import IDLE
from .errors import (Cancelled, ConnectionLost, HttpStatusError, InvalidRequest, LimitExceeded, NetError,
                     ProtocolError, RedirectRefused, Timeout, target)
from .multipart import BytesBody, MultipartBody
from .nbsocket import CancelToken, Clock, Connection, Resolver, Timeouts, connect, default_resolver
from .tls import TlsPolicy

USER_AGENT = "Slicewright"
SEND_SLICE = 256 * 1024
NO_REDIRECTS, SAME_HOST = "none", "same-host"
_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_CTL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")                # a tab is allowed in a header value
_FORBIDDEN_HEADERS = {"host", "content-length", "content-type", "connection", "transfer-encoding", "user-agent"}
_NO_BODY_STATUS = {204, 304}
_REDIRECTS = {301, 302, 303, 307, 308}


@dataclass(frozen=True)
class Limits:
    """What the client accepts from a peer. The defaults suit JSON APIs (OctoPrint, Moonraker)."""
    max_head_bytes: int = 32 * 1024         # status line + headers, all of them
    max_headers: int = 100
    max_body_bytes: int = 1024 * 1024
    max_line_bytes: int = 8 * 1024          # one header line, a chunk-size line


# -- URLs --------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    path: str                                # path and query, starting with "/"

    @property
    def authority(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        default = 443 if self.scheme == "https" else 80
        return host if self.port == default else f"{host}:{self.port}"

    @property
    def origin(self) -> str:
        """``scheme://host[:port]``: what messages may show of a URL (no path, no query)."""
        return f"{self.scheme}://{self.authority}"


def parse_url(url: str) -> Target:
    """Split an ``http``/``https`` URL; ``InvalidRequest`` for anything else, for credentials in the URL (they would
    end up in messages and logs: use a header) and for spaces or control characters."""
    if not isinstance(url, str) or _CTL.search(url) or " " in url:
        raise InvalidRequest("The URL contains a space or a control character")
    try:
        parts = urlsplit(url)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise InvalidRequest("The URL is not valid") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidRequest("Only http:// and https:// addresses can be used")
    if not host:
        raise InvalidRequest("The URL has no host")
    if parts.username is not None or parts.password is not None:
        raise InvalidRequest("Credentials do not belong in the URL")
    scheme = parts.scheme.lower()
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return Target(scheme, host.lower(), port or (443 if scheme == "https" else 80), path)


# -- responses ---------------------------------------------------------------------------------------------------

@dataclass
class Response:
    status: int
    reason: str
    headers: list[tuple[str, str]] = field(default_factory=list)      # names lower-cased, in order
    body: bytes = b""
    url: str = ""                           # scheme://host[:port] of the server that answered (no path, no query)
    upload_complete: bool = True            # False: the server answered before the whole body was sent

    def header(self, name: str, default: str | None = None) -> str | None:
        name = name.lower()
        values = [v for k, v in self.headers if k == name]
        return ", ".join(values) if values else default

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ProtocolError(f"{self.url} answered with something that is not JSON") from None

    def raise_for_status(self) -> "Response":
        """Return ``self`` for 2xx; raise :class:`HttpStatusError` (message: the status line only) otherwise."""
        if self.ok:
            return self
        reason = _clean_reason(self.reason)
        note = " (redirects are not followed)" if self.status in _REDIRECTS else ""
        raise HttpStatusError(f"{self.url} answered HTTP {self.status} {reason}".rstrip() + note,
                              status=self.status, reason=reason, response=self)


def _clean_reason(reason: str) -> str:
    return re.sub(r"[^\x20-\x7e]", "", reason)[:64].strip()


class ResponseParser:
    """Incremental parser for one HTTP/1.x response. ``feed`` takes whatever bytes arrived, in any split; ``feed_eof``
    says the stream ended. Limits are enforced as bytes arrive, so a hostile peer cannot make the client buffer more
    than ``limits`` allows."""

    HEAD, LENGTH, CHUNK_SIZE, CHUNK_DATA, CHUNK_END, TRAILERS, TO_EOF, DONE = range(8)

    def __init__(self, method: str = "GET", limits: Limits = Limits(), *, host: str = "", port: int | None = None):
        self.method = method.upper()
        self.limits = limits
        self._host, self._port = host, port
        self._where = target(host, port) if host else "the server"
        self._buf = bytearray()
        self.state = self.HEAD
        self.status = 0
        self.reason = ""
        self.headers: list[tuple[str, str]] = []
        self.body = bytearray()
        self._remaining = 0
        self._head_bytes = 0
        self.received = 0

    @property
    def head_done(self) -> bool:
        return self.state != self.HEAD

    @property
    def done(self) -> bool:
        return self.state == self.DONE

    # -- input -------------------------------------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        self.received += len(data)
        if self.state == self.DONE:
            return                                # trailing bytes after a complete response are ignored
        self._buf += data
        while self._buf and self.state != self.DONE:
            if not self._advance():
                break

    def feed_eof(self) -> None:
        """The peer closed. Completes a read-until-close body; anything else unfinished is a lost connection."""
        if self.state == self.TO_EOF:
            self.state = self.DONE
        elif self.state != self.DONE:
            what = "without answering" if not self.received else "before the response was complete"
            raise ConnectionLost(f"{self._where} closed the connection {what}", host=self._host, port=self._port)

    # -- states ------------------------------------------------------------------------------------------------

    def _advance(self) -> bool:
        """Process what is buffered for the current state; False when more bytes are needed."""
        if self.state == self.HEAD:
            return self._parse_head()
        if self.state in (self.LENGTH, self.TO_EOF):
            return self._take_body()
        if self.state == self.CHUNK_SIZE:
            return self._chunk_size()
        if self.state == self.CHUNK_DATA:
            return self._chunk_data()
        if self.state == self.CHUNK_END:
            if len(self._buf) < 2:
                return False
            if self._buf[:2] != b"\r\n":
                raise ProtocolError(f"{self._where} sent a malformed chunk")
            del self._buf[:2]
            self.state = self.CHUNK_SIZE
            return True
        if self.state == self.TRAILERS:
            return self._trailers()
        return False

    def _parse_head(self) -> bool:
        end = self._buf.find(b"\r\n\r\n")
        if end < 0:
            if len(self._buf) > self.limits.max_head_bytes:
                raise LimitExceeded(f"{self._where} sent a response header larger than "
                                    f"{self.limits.max_head_bytes // 1024} KB")
            return False
        if end + 4 > self.limits.max_head_bytes:
            raise LimitExceeded(f"{self._where} sent a response header larger than "
                                f"{self.limits.max_head_bytes // 1024} KB")
        raw = bytes(self._buf[:end])
        del self._buf[:end + 4]
        lines = raw.split(b"\r\n")
        if any(len(line) > self.limits.max_line_bytes for line in lines):
            raise LimitExceeded(f"{self._where} sent an overlong header line")
        if len(lines) - 1 > self.limits.max_headers:
            raise LimitExceeded(f"{self._where} sent more than {self.limits.max_headers} headers")
        match = re.match(rb"^HTTP/1\.[01] (\d{3})(?: (.*))?$", lines[0])
        if not match:
            raise ProtocolError(f"{self._where} did not answer with HTTP")
        status = int(match.group(1))
        reason = (match.group(2) or b"").decode("latin-1")
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            name, sep, value = line.partition(b":")
            if not sep or not _TOKEN.match(name.decode("latin-1")):
                raise ProtocolError(f"{self._where} sent a malformed header")
            text = value.decode("latin-1").strip(" \t")
            if _CTL.search(text):
                raise ProtocolError(f"{self._where} sent a malformed header")
            headers.append((name.decode("latin-1").lower(), text))
        if 100 <= status < 200:
            if status == 101:
                raise ProtocolError(f"{self._where} tried to switch protocols")
            return True                           # an interim answer (100 Continue): the real one follows
        self.status, self.reason, self.headers = status, reason, headers
        self._frame_body()
        return True

    def _header_values(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k == name]

    def _frame_body(self) -> None:
        if self.method == "HEAD" or self.status in _NO_BODY_STATUS:
            self.state = self.DONE
            return
        encodings = [t.strip().lower() for v in self._header_values("transfer-encoding") for t in v.split(",")]
        lengths = {v.strip() for v in self._header_values("content-length")}
        if encodings:
            if encodings[-1] != "chunked" or any(t not in ("chunked", "identity") for t in encodings):
                raise ProtocolError(f"{self._where} used a transfer encoding that is not supported")
            self.state = self.CHUNK_SIZE
        elif lengths:
            if len(lengths) != 1 or not next(iter(lengths)).isdigit():
                raise ProtocolError(f"{self._where} sent an invalid Content-Length")
            length = int(next(iter(lengths)))
            if length > self.limits.max_body_bytes:
                raise LimitExceeded(f"{self._where} announced an answer of {length} bytes, above the "
                                    f"{self.limits.max_body_bytes} byte limit")
            self._remaining = length
            self.state = self.LENGTH if length else self.DONE
        else:
            self.state = self.TO_EOF

    def _take_body(self) -> bool:
        if self.state == self.LENGTH:
            take = bytes(self._buf[:self._remaining])
            del self._buf[:len(take)]
            self._remaining -= len(take)
            self.body += take
            if not self._remaining:
                self.state = self.DONE
            return False
        self._add_body(bytes(self._buf))
        self._buf.clear()
        return False

    def _add_body(self, data: bytes) -> None:
        if len(self.body) + len(data) > self.limits.max_body_bytes:
            raise LimitExceeded(f"{self._where} sent more than {self.limits.max_body_bytes} bytes")
        self.body += data

    def _chunk_size(self) -> bool:
        end = self._buf.find(b"\r\n")
        if end < 0:
            if len(self._buf) > self.limits.max_line_bytes:
                raise LimitExceeded(f"{self._where} sent an overlong chunk header")
            return False
        line = bytes(self._buf[:end]).split(b";", 1)[0].strip()
        del self._buf[:end + 2]
        if not re.fullmatch(rb"[0-9a-fA-F]{1,16}", line):
            raise ProtocolError(f"{self._where} sent a malformed chunk size")
        size = int(line, 16)
        if size == 0:
            self.state = self.TRAILERS
            self._head_bytes = 0
        else:
            if len(self.body) + size > self.limits.max_body_bytes:
                raise LimitExceeded(f"{self._where} sent more than {self.limits.max_body_bytes} bytes")
            self._remaining = size
            self.state = self.CHUNK_DATA
        return True

    def _chunk_data(self) -> bool:
        take = bytes(self._buf[:self._remaining])
        del self._buf[:len(take)]
        self._remaining -= len(take)
        self.body += take
        if not self._remaining:
            self.state = self.CHUNK_END
            return True
        return False

    def _trailers(self) -> bool:
        end = self._buf.find(b"\r\n")
        if end < 0:
            if len(self._buf) > self.limits.max_line_bytes:
                raise LimitExceeded(f"{self._where} sent an overlong trailer")
            return False
        line = bytes(self._buf[:end])
        del self._buf[:end + 2]
        self._head_bytes += end + 2
        if self._head_bytes > self.limits.max_head_bytes:
            raise LimitExceeded(f"{self._where} sent too many trailers")
        if not line:
            self.state = self.DONE
        return True


# -- requests ----------------------------------------------------------------------------------------------------

def _validate_headers(headers: Mapping[str, str] | None) -> list[tuple[str, str]]:
    out = []
    for name, value in (headers or {}).items():
        if not _TOKEN.match(name):
            raise InvalidRequest("A request header has an invalid name")
        if name.lower() in _FORBIDDEN_HEADERS:
            raise InvalidRequest(f"The {name} header is set by the client and cannot be passed in")
        if _CTL.search(str(value)):
            raise InvalidRequest(f"The {name} header contains a control character")   # (never the value itself)
        out.append((name, str(value)))
    return out


def build_head(method: str, tgt: Target, headers: list[tuple[str, str]], body) -> bytes:
    method = method.upper()
    if not _TOKEN.match(method):
        raise InvalidRequest("The HTTP method is not valid")
    lines = [f"{method} {tgt.path} HTTP/1.1", f"Host: {tgt.authority}", f"User-Agent: {USER_AGENT}",
             "Accept: */*", "Connection: close"]
    if body is not None:
        lines += [f"Content-Type: {body.content_type}", f"Content-Length: {body.length}"]
    elif method in ("POST", "PUT", "PATCH"):
        lines.append("Content-Length: 0")
    lines += [f"{k}: {v}" for k, v in headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "strict")


def _exchange(conn: Connection, method: str, head: bytes, body, limits: Limits, timeouts: Timeouts,
              cancel: CancelToken, clock: Clock, started: float,
              on_progress: Callable[[int, int], None] | None) -> Iterator:
    """Send ``head`` and ``body`` while reading the response; returns ``(Response, upload_complete)``."""
    parser = ResponseParser(method, limits, host=conn.host, port=conn.port)
    chunks = iter(body.chunks()) if body is not None else iter(())
    out = memoryview(head)
    head_left = len(head)
    total = body.length if body is not None else 0
    body_sent = 0
    sending = True
    send_failure: NetError | None = None
    last_move = clock()
    eof = False
    while not parser.done:
        cancel.check(conn.host, conn.port)
        now = clock()
        if timeouts.total is not None and now - started > timeouts.total:
            raise Timeout(f"The exchange with {target(conn.host, conn.port)} took longer than "
                          f"{timeouts.total:g} s", phase="total", host=conn.host, port=conn.port)
        if now - last_move > timeouts.idle:
            raise Timeout(f"{target(conn.host, conn.port)} did not respond for {timeouts.idle:g} s",
                          phase="idle", host=conn.host, port=conn.port)
        moved = False
        if sending and parser.head_done and parser.status >= 200:
            sending = False                       # answered early (401, 413, ...): stop sending, finish reading
        if sending:
            try:
                if not len(out):
                    out = memoryview(next(chunks))
                    if not len(out):
                        continue
                n = conn.send(out[:SEND_SLICE])
                if n:
                    moved = True
                    out = out[n:]
                    if head_left:
                        used = min(n, head_left)
                        head_left -= used
                        n -= used
                    body_sent += n
                    if on_progress is not None and n:
                        on_progress(body_sent, total)
            except StopIteration:
                sending = False
            except ConnectionLost as exc:
                send_failure = exc                # maybe the server already answered: read what it said
                sending = False
        if not eof:
            data = conn.recv()
            if data is None:
                pass
            elif data == b"":
                eof = True
                try:
                    parser.feed_eof()
                except ConnectionLost as lost:
                    raise (send_failure or lost) from None
            else:
                moved = True
                parser.feed(data)
        elif not parser.done:
            raise send_failure or ConnectionLost(f"{target(conn.host, conn.port)} closed the connection",
                                                 host=conn.host, port=conn.port)
        if moved:
            last_move = clock()
            yield (min(0.99, body_sent / total) if total else None)
        elif not parser.done:
            yield IDLE
    upload_complete = body is None or (not len(out) and body_sent >= total)
    return Response(parser.status, parser.reason, parser.headers, bytes(parser.body)), upload_complete


def _once(method, tgt: Target, headers, body, tls, limits, timeouts, cancel, clock, resolver, on_progress) -> Iterator:
    head = build_head(method, tgt, headers, body)
    policy = (tls or TlsPolicy()) if tgt.scheme == "https" else None
    started = clock()
    conn = yield from connect(tgt.host, tgt.port, tls=policy, timeouts=timeouts, cancel=cancel, clock=clock,
                              resolver=resolver)
    try:
        response, complete = yield from _exchange(conn, method, head, body, limits, timeouts, cancel, clock, started,
                                                  on_progress)
    finally:
        conn.close()
    response.url = tgt.origin
    response.upload_complete = complete
    if not complete and response.ok:
        raise ProtocolError(f"{tgt.origin} answered before the whole upload was sent")
    return response


def request(method: str, url: str, *, headers: Mapping[str, str] | None = None, body: BytesBody | MultipartBody | None = None,
            tls: TlsPolicy | None = None, limits: Limits = Limits(), timeouts: Timeouts = Timeouts(),
            redirects: str = NO_REDIRECTS, max_redirects: int = 3, cancel: CancelToken | None = None,
            clock: Clock = time.monotonic, resolver: Resolver = default_resolver,
            on_progress: Callable[[int, int], None] | None = None) -> Iterator:
    """Generator: perform one HTTP request and return the final :class:`Response` (any status: call
    ``raise_for_status``). ``redirects`` is ``"none"`` (a 3xx is returned as is) or ``"same-host"``."""
    if redirects not in (NO_REDIRECTS, SAME_HOST):
        raise ValueError(f"unknown redirect policy {redirects!r}")
    cancel = cancel or CancelToken()
    custom = _validate_headers(headers)
    tgt = parse_url(url)
    method = method.upper()
    for hop in range(max_redirects + 1):
        response = yield from _once(method, tgt, custom, body, tls, limits, timeouts, cancel, clock, resolver,
                                    on_progress)
        if redirects == NO_REDIRECTS or response.status not in _REDIRECTS:
            return response
        location = response.header("location")
        if not location:
            return response
        if hop == max_redirects:
            raise RedirectRefused(f"{tgt.origin} redirected more than {max_redirects} times", host=tgt.host,
                                  port=tgt.port)
        tgt, method = _follow(tgt, method, response, location, body)
        if method in ("GET", "HEAD"):
            body = None                           # (307/308 of a POST keeps the body: chunks() replays it)
    raise AssertionError("unreachable")


def _follow(tgt: Target, method: str, response: Response, location: str, body) -> tuple[Target, str]:
    try:
        new = parse_url(urljoin(f"{tgt.origin}{tgt.path}", location))
    except InvalidRequest:
        raise RedirectRefused(f"{tgt.origin} redirected to an address that cannot be used", host=tgt.host,
                              port=tgt.port) from None
    shown = new.origin
    if new.host != tgt.host:
        raise RedirectRefused(f"{tgt.origin} redirected to a different host ({shown}); not followed",
                              location=shown, host=tgt.host, port=tgt.port)
    if tgt.scheme == "https" and new.scheme == "http":
        raise RedirectRefused(f"{tgt.origin} redirected from HTTPS to plain HTTP; not followed", location=shown,
                              host=tgt.host, port=tgt.port)
    if new.port != tgt.port and not (tgt.scheme == "http" and new.scheme == "https"):
        raise RedirectRefused(f"{tgt.origin} redirected to another port ({shown}); not followed", location=shown,
                              host=tgt.host, port=tgt.port)
    if method not in ("GET", "HEAD"):
        if response.status in (307, 308):
            return new, method
        raise RedirectRefused(f"{tgt.origin} answered {method} with HTTP {response.status}; the request is not "
                              f"repeated automatically", location=shown, host=tgt.host, port=tgt.port)
    return new, method
