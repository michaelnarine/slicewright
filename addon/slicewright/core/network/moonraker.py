# SPDX-License-Identifier: GPL-3.0-or-later
"""The Moonraker / Klipper client (03 section 8.3). Pure Python generators on top of ``httpclient``; no ``bpy``.

Behaviour reference [O ``Moonraker.cpp``], written independently from Moonraker's public web API documentation: the
connection test is ``GET /server/info`` (Klippy state) and ``GET /printer/info``; the upload is ``POST /server/files/upload``
as ``multipart/form-data`` with the file in ``file`` and the text fields ``root=gcodes``, ``path`` (a folder under the
root, optional) and ``print=true`` (only when asked for). The API key, when the printer has one, goes in ``X-Api-Key``;
without one nothing is sent (Moonraker's trusted clients need none). Moonraker's JWT/OAuth login is out of scope.

A print is started **only** for ``start_print=True``, which the add-on passes only after the user ticked the box and
confirmed the popup naming the printer and the file.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .errors import AuthRejected, HttpStatusError, NetError, NotThisServer, ProtocolError
from .multipart import MultipartBody
from .webapi import (Endpoint, Transfer, call, check_filename, check_subfolder, json_object, status_error)

PRODUCT = "Moonraker"
ROOT = "gcodes"
CONTENT_TYPE = "application/octet-stream"
_FORBIDDEN_HINT = " (Moonraker also answers 403 when the file is currently being printed)"


@dataclass(frozen=True)
class ConnectionInfo:
    """What a connection test learned. ``klippy_state`` is Klipper's own state as Moonraker reports it (``ready``,
    ``startup``, ``shutdown``, ``error``, ``disconnected``); files can be sent in any state, a print only starts when
    it is ``ready``. ``state_message`` is the text Klipper gives for it (``GET /printer/info``), when it could be read."""
    product: str
    server_version: str
    api_version: str
    key_accepted: bool                        # the request was authorised (with a key, or as a trusted client)
    klippy_state: str
    state_message: str = ""
    key_sent: bool = False

    @property
    def printer_state(self) -> str:
        return self.klippy_state

    @property
    def ready(self) -> bool:
        return self.klippy_state == "ready"

    def summary(self) -> str:
        text = f"Moonraker {self.server_version}".rstrip() if self.server_version else "Moonraker"
        text += f" (API {self.api_version})" if self.api_version else ""
        text += ", API key accepted" if self.key_sent else ", access allowed (no API key set)"
        text += f", Klipper: {self.klippy_state or 'unknown'}"
        if self.state_message and not self.ready:
            text += f" ({self.state_message})"
        return text


@dataclass(frozen=True)
class UploadResult:
    name: str
    path: str                                 # path under the root (folder/name)
    size: int
    printing: bool                            # Moonraker says the print was started

    def summary(self) -> str:
        text = f"Sent {self.path or self.name} to Moonraker"
        return text + (" and started the print" if self.printing else "")


def _text(value, limit: int = 60) -> str:
    return "".join(c for c in value if c.isprintable())[:limit] if isinstance(value, str) else ""


def _auth_error(ep: Endpoint, response, doing: str) -> HttpStatusError:
    if response.status in (401, 403) and not ep.api_key:
        return AuthRejected(f"Moonraker at {response.url} asks for authorization, but this printer has no API key set "
                            f"(HTTP {response.status}). Set the API key, or add this computer to Moonraker's trusted "
                            f"clients", status=response.status, reason="", response=response)
    return status_error(response, PRODUCT, doing=doing, forbidden_hint=_FORBIDDEN_HINT)


def test_connection(ep: Endpoint) -> Iterator:
    """Generator: check that the address is Moonraker and that it lets us in; returns :class:`ConnectionInfo`. Raises
    ``AuthRejected`` (401/403), ``NotThisServer`` when something else answers, and the transport errors otherwise."""
    response = yield from call(ep, "GET", "/server/info", follow_redirects=True)
    if not response.ok:
        raise _auth_error(ep, response, "the connection test")
    data = json_object(response, PRODUCT)
    result = data.get("result")
    if not isinstance(result, dict) or not ("klippy_state" in result or "moonraker_version" in result):
        raise NotThisServer(f"{response.url} answered, but not like Moonraker (the port, HTTPS setting or base path may "
                            f"be wrong)")
    klippy = _text(result.get("klippy_state")) or ("ready" if result.get("klippy_connected") is True else "unknown")
    message = yield from _state_message(ep)
    return ConnectionInfo(PRODUCT, _text(result.get("moonraker_version")), _text(result.get("api_version_string")),
                          True, klippy, message, key_sent=bool(ep.api_key))


test_connection.__test__ = False         # (a name starting with "test" is not a pytest test)


def _state_message(ep: Endpoint) -> Iterator:
    """Klipper's state message from ``GET /printer/info``, or ``""``: Moonraker answers 503 while Klipper is not
    connected, so any HTTP failure here is an absent nicety, not an error."""
    try:
        response = yield from call(ep, "GET", "/printer/info", follow_redirects=True)
        if not response.ok:
            return ""
        result = json_object(response, PRODUCT).get("result")
        return _text(result.get("state_message"), 100) if isinstance(result, dict) else ""
    except (HttpStatusError, ProtocolError, NotThisServer):
        return ""
    except NetError as exc:
        if exc.kind in ("cancelled", "online_access", "tls_unknown", "tls_pin_mismatch"):
            raise
        return ""


def upload(ep: Endpoint, file_path: str, name: str, *, subfolder: str = "", start_print: bool = False,
           transfer: Transfer | None = None) -> Iterator:
    """Generator: upload ``file_path`` to Moonraker's ``gcodes`` root as ``name`` (inside ``subfolder`` when given);
    returns :class:`UploadResult`. ``start_print`` asks Moonraker to print it once stored; it is ``False`` unless the
    caller passes ``True``, and the result says whether Moonraker actually started it. Errors as in the OctoPrint
    client; a 403 is "not allowed" (key, or the file is being printed)."""
    name = check_filename(name)
    subfolder = check_subfolder(subfolder)
    fields = [("root", ROOT)]
    if subfolder:
        fields.append(("path", subfolder))
    if start_print is True:
        fields.append(("print", "true"))
    body = MultipartBody(fields, file_field="file", file_path=file_path, filename=name, content_type=CONTENT_TYPE)
    transfer = transfer if transfer is not None else Transfer()
    transfer.total, transfer.sent, transfer.phase = body.length, 0, "connecting"
    response = yield from call(ep, "POST", "/server/files/upload", body=body, transfer=transfer, upload=True)
    if not response.ok:
        raise _auth_error(ep, response, "the file")
    transfer.phase = "done"
    return _upload_result(response, name, subfolder, body.file_size)


def _upload_result(response, name: str, subfolder: str, size: int) -> UploadResult:
    """The stored path and whether Moonraker started a print. Moonraker documents the answer as
    ``{"item": {...}, "print_started": bool}``; a ``result`` wrapper is tolerated too. Anything else is not a failure (the
    file is there) and claims nothing."""
    stored, path, printing = name, f"{subfolder}/{name}" if subfolder else name, False
    try:
        data = response.json()
    except ProtocolError:
        data = None
    if isinstance(data, dict):
        inner = data["result"] if isinstance(data.get("result"), dict) else data
        item = inner.get("item")
        if isinstance(item, dict):
            path = _text(item.get("path"), 512) or path
            stored = path.rsplit("/", 1)[-1] or stored
        printing = inner.get("print_started") is True
    return UploadResult(stored, path, size, printing)
