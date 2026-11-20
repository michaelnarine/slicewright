# SPDX-License-Identifier: GPL-3.0-or-later
"""The OctoPrint client (03 section 8.2). Pure Python generators on top of ``httpclient``; no ``bpy``.

Behaviour reference [O ``OctoPrint.cpp``], written independently: the version check is ``GET /api/version``; the upload
is ``POST /api/files/local`` as ``multipart/form-data`` with the file in ``file`` and the optional text fields ``select``,
``print`` and ``path`` (a folder under "local"). The key travels in ``X-Api-Key``. One request per connection; the file is
streamed from disk in 256 KB chunks and the answer is read while it is sent, so a ``401``/``413`` stops the upload.

A print is started **only** when the caller passes ``start_print=True``; the add-on does that only after the user ticked
the box and confirmed the popup naming the printer and the file. ``select`` alone is never implied by anything else.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterator

from .errors import HttpStatusError, NetError, NotThisServer, ProtocolError
from .multipart import MultipartBody
from .webapi import (Endpoint, Transfer, call, check_filename, check_subfolder, json_object, require_key,
                     status_error)

PRODUCT = "OctoPrint"
CONTENT_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class ConnectionInfo:
    """What a connection test learned. ``key_accepted`` is True whenever the version call was answered (OctoPrint demands
    a valid key for it), ``printer_state`` is the text of ``GET /api/connection`` (``Operational``, ``Printing``,
    ``Closed``...) or ``None`` when that could not be read (the key may lack the permission: not an error)."""
    product: str
    server_version: str
    api_version: str
    key_accepted: bool
    printer_state: str | None = None

    def summary(self) -> str:
        text = f"OctoPrint {self.server_version}".rstrip() if self.server_version else "OctoPrint"
        text += f" (API {self.api_version})" if self.api_version else ""
        text += ", API key accepted"
        if self.printer_state:
            text += f", printer: {self.printer_state}"
        return text


@dataclass(frozen=True)
class UploadResult:
    name: str                                # the file name OctoPrint stored it under
    path: str                                # its path under "local" (folder/name)
    size: int                                # bytes sent
    selected: bool                           # OctoPrint says the file is now selected
    printing: bool                           # OctoPrint says a print was started

    def summary(self) -> str:
        text = f"Sent {self.path or self.name} to OctoPrint"
        return text + (" and started the print" if self.printing else (" and selected it" if self.selected else ""))


def test_connection(ep: Endpoint) -> Iterator:
    """Generator: check that the address is OctoPrint and that the key works; returns :class:`ConnectionInfo`. Raises
    :class:`~slicewright.core.network.errors.AuthRejected` for a rejected key, ``NotThisServer`` when something else
    answers, and the transport errors (refused, timeout, TLS...) otherwise."""
    require_key(ep, PRODUCT)
    response = yield from call(ep, "GET", "/api/version", follow_redirects=True)
    if not response.ok:
        raise status_error(response, PRODUCT, doing="the connection test")
    info = json_object(response, PRODUCT)
    server, api = info.get("server"), info.get("api")
    if not isinstance(server, str) and not isinstance(api, str):
        raise NotThisServer(f"{response.url} answered, but not like OctoPrint (the port, HTTPS setting or base path "
                            f"may be wrong)")
    state = yield from _printer_state(ep)
    return ConnectionInfo(PRODUCT, _text(server), _text(api), True, state)


test_connection.__test__ = False       # (a name starting with "test" is not a pytest test)


def _text(value, limit: int = 40) -> str:
    return "".join(c for c in value if c.isprintable())[:limit] if isinstance(value, str) else ""


def _printer_state(ep: Endpoint) -> Iterator:
    """The connection state text, or ``None``: this call is a nicety, so only a cancel or a lost network ends the test."""
    try:
        response = yield from call(ep, "GET", "/api/connection", follow_redirects=True)
        if not response.ok:
            return None
        current = json_object(response, PRODUCT).get("current")
        state = current.get("state") if isinstance(current, dict) else None
        return _text(state) or None
    except (HttpStatusError, ProtocolError):
        return None
    except NetError as exc:
        if exc.kind in ("cancelled", "online_access", "tls_unknown", "tls_pin_mismatch"):
            raise
        return None


def upload(ep: Endpoint, file_path: str, name: str, *, subfolder: str = "", select: bool = False,
           start_print: bool = False, transfer: Transfer | None = None) -> Iterator:
    """Generator: upload ``file_path`` to OctoPrint's "local" storage as ``name`` (inside ``subfolder`` when given);
    returns :class:`UploadResult`. ``start_print`` starts printing it once stored (OctoPrint then selects it too); it is
    ``False`` unless the caller passes ``True``. Progress and the phase go to ``transfer``. Raises ``InvalidRequest`` for a
    bad name or folder before anything is sent, ``UploadError`` if the file cannot be read, and the same errors as
    :func:`test_connection` plus ``Conflict`` (409, the printer is busy) and ``PayloadTooLarge`` (413)."""
    require_key(ep, PRODUCT)
    name = check_filename(name)
    subfolder = check_subfolder(subfolder)
    fields = []
    if subfolder:
        fields.append(("path", subfolder))
    if select or start_print:
        fields.append(("select", "true"))
    if start_print is True:
        fields.append(("print", "true"))
    body = MultipartBody(fields, file_field="file", file_path=file_path, filename=name, content_type=CONTENT_TYPE)
    transfer = transfer if transfer is not None else Transfer()
    transfer.total, transfer.sent, transfer.phase = body.length, 0, "connecting"
    response = yield from call(ep, "POST", "/api/files/local", body=body, transfer=transfer, upload=True)
    if not response.ok:
        raise status_error(response, PRODUCT, doing="the file")
    transfer.phase = "done"
    return _upload_result(response, name, subfolder, body.file_size)


def _upload_result(response, name: str, subfolder: str, size: int) -> UploadResult:
    """What OctoPrint said about the stored file; a body that is not what we expect is not a failure (the file is
    there), so the result falls back to the name we sent."""
    stored, path = name, f"{subfolder}/{name}" if subfolder else name
    selected, printing = False, False
    try:
        data = response.json()
    except ProtocolError:
        data = None
    if isinstance(data, dict):
        local = (data.get("files") or {}).get("local") if isinstance(data.get("files"), dict) else None
        if isinstance(local, dict):
            stored = _text(local.get("name"), 255) or stored
            path = _text(local.get("path"), 512) or path
        selected = bool(data.get("effectiveSelect", False))     # only what OctoPrint confirms counts
        printing = bool(data.get("effectivePrint", False))
    return UploadResult(stored, path, size, selected, printing)
