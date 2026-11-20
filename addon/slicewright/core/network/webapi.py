# SPDX-License-Identifier: GPL-3.0-or-later
"""What the OctoPrint and Moonraker clients share (03 sections 8.2 and 8.3): the endpoint, one call, progress and the
mapping of HTTP answers to messages. No ``bpy``, standard library only; everything is a generator (see ``nbsocket``).

The API key is a :class:`~slicewright.core.redact.SecretStr` held in the :class:`Endpoint`; it is turned into a header
inside :func:`call`, in a local variable, and nowhere else: never in a URL, a message, an exception or a log line.
Messages are built from the product name, the origin (``scheme://host[:port]``, no path, no query) and the status line,
never from a header, a request body or the server's answer text.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from ..redact import SecretStr
from .errors import (AuthRejected, Cancelled, Conflict, HttpStatusError, InvalidRequest, NetError, NotThisServer,
                     PayloadTooLarge, ProtocolError)
from .httpclient import NO_REDIRECTS, SAME_HOST, Limits, Response, request
from .multipart import BytesBody, MultipartBody
from .nbsocket import CancelToken, Clock, Timeouts
from .resolve import Resolver, default_resolver
from .tls import TlsPolicy

API_KEY_HEADER = "X-Api-Key"
MAX_FILENAME = 255
_CTL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class Transfer:
    """Progress of an upload, written by the client and read by whoever draws a progress bar. ``phase`` is
    ``connecting``, ``uploading`` (bytes are moving; ``sent`` of ``total``), ``waiting`` (everything is sent, the
    printer has not answered yet) or ``done``."""
    phase: str = "connecting"
    sent: int = 0
    total: int = 0

    @property
    def fraction(self) -> float:
        return min(1.0, self.sent / self.total) if self.total else 0.0

    def update(self, sent: int, total: int) -> None:
        self.sent, self.total = sent, total
        self.phase = "uploading" if sent < total else "waiting"


@dataclass(frozen=True)
class Endpoint:
    """Where and how to talk to one printer. ``base_url`` includes a reverse-proxy path prefix and has no trailing slash
    (``core.printers.base_url``)."""
    base_url: str
    api_key: SecretStr | None = None
    tls: TlsPolicy | None = None
    timeouts: Timeouts = Timeouts(connect=5.0, idle=15.0)
    upload_timeouts: Timeouts = Timeouts(connect=5.0, idle=60.0)      # the printer may think a while after the last byte
    limits: Limits = Limits()
    resolver: Resolver = default_resolver
    clock: Clock = time.monotonic
    cancel: CancelToken | None = None

    def url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path


def call(ep: Endpoint, method: str, path: str, *, body: BytesBody | MultipartBody | None = None,
         transfer: Transfer | None = None, upload: bool = False, follow_redirects: bool = False,
         extra_headers: Mapping[str, str] | None = None) -> Iterator:
    """Generator: one request to ``ep`` (``path`` starts with ``/``; the API key goes in ``X-Api-Key``). Returns the
    :class:`~slicewright.core.network.httpclient.Response` whatever its status."""
    headers = dict(extra_headers or {})
    if ep.api_key:
        headers[API_KEY_HEADER] = ep.api_key.reveal()      # the one place the secret becomes text
    callback = transfer.update if transfer is not None else None
    response = yield from request(
        method, ep.url(path), headers=headers, body=body, tls=ep.tls, limits=ep.limits,
        timeouts=ep.upload_timeouts if upload else ep.timeouts, cancel=ep.cancel, clock=ep.clock, resolver=ep.resolver,
        redirects=SAME_HOST if follow_redirects else NO_REDIRECTS, on_progress=callback)
    return response


def json_object(response: Response, product: str) -> dict[str, Any]:
    """The body as a JSON object, or :class:`NotThisServer` (the address answers, but not like ``product``)."""
    try:
        value = response.json()
    except ProtocolError:
        raise NotThisServer(f"{response.url} answered, but not like {product} (the port, HTTPS setting or base path "
                            f"may be wrong)") from None
    if not isinstance(value, dict):
        raise NotThisServer(f"{response.url} answered, but not like {product} (the port, HTTPS setting or base path "
                            f"may be wrong)")
    return value


def status_error(response: Response, product: str, *, doing: str = "the request", key_help: str = "API key") -> HttpStatusError:
    """A friendly :class:`HttpStatusError` (or subclass) for a non-2xx ``response``. ``doing`` completes "<product> did
    not accept ..." for the generic case."""
    status, origin = response.status, response.url
    reason = re.sub(r"[^\x20-\x7e]", "", response.reason)[:64].strip()
    line = f"HTTP {status} {reason}".rstrip()
    common = dict(status=status, reason=reason, response=response)
    if status in (401, 403):
        return AuthRejected(f"{product} at {origin} rejected the {key_help} ({line}). Check the {key_help} in the "
                            f"printer's settings and that it is allowed to do this", **common)
    if status == 409:
        return Conflict(f"{product} at {origin} cannot take this right now ({line}): the printer may be busy, not "
                        f"ready, or printing a file with the same name", **common)
    if status == 413:
        return PayloadTooLarge(f"{origin} refused the file because it is too large ({line}). A reverse proxy in front "
                               f"of {product} may limit the upload size", **common)
    if status in (301, 302, 303, 307, 308):
        return HttpStatusError(f"{origin} redirected the request ({line}). Turn HTTPS on, or fix the port or base path "
                               f"of the printer", **common)
    if status == 404:
        return HttpStatusError(f"{origin} does not have what {product} should serve here ({line}). Check the port, "
                               f"the HTTPS setting and the base path", **common)
    if status == 415:
        return HttpStatusError(f"{product} at {origin} does not accept this kind of file ({line})", **common)
    if status >= 500:
        return HttpStatusError(f"{product} at {origin} failed ({line}). Look at its own log", **common)
    return HttpStatusError(f"{product} at {origin} did not accept {doing} ({line})", **common)


def check_filename(name: str) -> str:
    """A file name for the printer: no folder part, no control characters, not hidden, 1 to 255 characters.
    :class:`InvalidRequest` otherwise; returns it unchanged."""
    if not isinstance(name, str) or not name.strip():
        raise InvalidRequest("Enter a file name to send")
    if len(name) > MAX_FILENAME or _CTL.search(name):
        raise InvalidRequest("The file name is too long or contains a control character")
    if "/" in name or "\\" in name:
        raise InvalidRequest("The file name must not contain / or \\ (use the folder field for a folder)")
    if name.startswith(".") or name != name.strip():
        raise InvalidRequest("The file name must not start with a dot or begin or end with a space")
    return name


def check_subfolder(path: str) -> str:
    """A folder on the printer, relative (``a/b``): no ``..``, no empty parts, no control characters. ``""`` is the root.
    :class:`InvalidRequest` otherwise; returns it normalised (no leading or trailing slash)."""
    if not isinstance(path, str):
        raise InvalidRequest("The folder is not valid")
    path = path.strip().strip("/")
    if not path:
        return ""
    if len(path) > 255 or _CTL.search(path) or "\\" in path:
        raise InvalidRequest("The folder is too long or contains a character that is not allowed")
    parts = path.split("/")
    if any(part in ("", ".", "..") or part != part.strip() or part.startswith(".") for part in parts):
        raise InvalidRequest("The folder must be a plain relative path (no '..', no empty or hidden parts)")
    return path


def require_key(ep: Endpoint, product: str) -> None:
    if not ep.api_key:
        raise InvalidRequest(f"Set the API key of this printer before connecting to {product}")


def ensure_not_cancelled(ep: Endpoint) -> None:
    if ep.cancel is not None and ep.cancel.cancelled:
        raise Cancelled()
