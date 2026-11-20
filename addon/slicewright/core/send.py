# SPDX-License-Identifier: GPL-3.0-or-later
"""Sending a slice to a physical printer: which printers, which file, what name (03 section 8). No ``bpy``.

The pure decisions behind the Send panel and operators: which printer records may be offered for a printer preset,
which file format a kind of printer takes by default, the name the file gets on the printer, and the dispatch to the
OctoPrint and Moonraker clients. Nothing here touches the network by itself: the generators it returns are driven by the
caller (the tick runner in Blender, ``run_blocking`` in tests).
"""
from __future__ import annotations

import os
from typing import Iterable, Iterator

from . import export as core_export
from . import printers as core_printers
from .issues import ERROR
from .network import bambu, moonraker, octoprint
from .network.errors import NetError
from .network.webapi import Endpoint, Transfer
from .printers import BAMBU_LAN, MOONRAKER, OCTOPRINT, PrinterRecord

GCODE, THREEMF = "gcode", "3mf"
FORMATS = (GCODE, THREEMF)
FORMAT_LABELS = {GCODE: "G-code", THREEMF: ".gcode.3mf"}
AUTO = "auto"
SUPPORTED = (OCTOPRINT, MOONRAKER, BAMBU_LAN)
ISSUES_SOURCE = "send"
NOT_SUPPORTED: dict[str, str] = {}          # kind -> why it cannot be sent to (every kind is supported now)


def default_format(kind: str) -> str:
    """The file a kind of printer takes by default: plain G-code for OctoPrint and Moonraker, a ``.gcode.3mf`` for a
    Bambu printer (its firmware prints projects, not bare G-code)."""
    return THREEMF if kind == BAMBU_LAN else GCODE


def resolve_format(kind: str, chosen: str) -> str:
    """``chosen`` (``auto``, ``gcode`` or ``3mf``) as a concrete format."""
    return default_format(kind) if chosen in ("", AUTO) else chosen


def eligible_printers(records: Iterable[PrinterRecord], preset: str, *, show_all: bool = False) -> list[PrinterRecord]:
    """The printers to offer for the printer preset ``preset``: those whose paired preset is that preset, and those with
    no pairing (they take any), or all of them with ``show_all``. Order of the preferences."""
    return [r for r in records if show_all or not r.preset or r.preset == preset]


def target_filename(engine_name: str, fmt: str, typed: str = "") -> str:
    """The file name on the printer: what the user typed (made portable, extension added only if missing), else the
    engine's ``output_filename`` made into a name of the chosen format."""
    base = typed.strip() or engine_name
    return core_export.threemf_filename(base) if fmt == THREEMF else core_export.gcode_filename(base)


def send_blocker(record: PrinterRecord | None) -> str | None:
    """Why this printer cannot be sent to at all (no printer, a kind not supported yet, an incomplete record), or
    ``None``. The text is for the panel."""
    if record is None:
        return "Choose a printer to send to"
    if record.kind not in SUPPORTED:
        return NOT_SUPPORTED.get(record.kind, "This kind of printer cannot be sent to yet")
    problems = core_printers.validate(record, complete=True)
    if problems:
        return problems[0].message
    return None


def start_print_blocker(record: PrinterRecord, fmt: str) -> str | None:
    """Why a print cannot be started after the upload. A ``.gcode.3mf`` is a container: neither OctoPrint nor Moonraker
    can print one. A Bambu printer prints nothing else, and starting one is not available yet (layer 2)."""
    if record.kind == BAMBU_LAN:
        return "Starting a print on a Bambu printer is not available yet"
    if fmt == THREEMF:
        return "Starting a print needs a plain G-code file for this printer"
    return None


def options_blocker(record: PrinterRecord | None, fmt: str, subfolder: str = "") -> str | None:
    """Why the panel's file choices do not suit this printer, or ``None``. A Bambu printer takes a ``.gcode.3mf`` and
    stores it in its root, so a plain G-code file or a folder is refused before anything is made."""
    if record is None or record.kind != BAMBU_LAN:
        return None
    if fmt != THREEMF:
        return "A Bambu printer takes a .gcode.3mf file: choose that file type"
    if subfolder.strip().strip("/"):
        return "A Bambu printer stores files in its root: clear the Folder field"
    return None


def bambu_endpoint(record: PrinterRecord, access_code, pin_store, **kwargs) -> bambu.BambuEndpoint:
    """The :class:`~slicewright.core.network.bambu.BambuEndpoint` of a Bambu record: the TLS policy always pins against
    ``pin_store`` (keyed by the record id), the serial comes from the record's options, ``access_code`` is a
    ``SecretStr`` (or ``None``)."""
    return bambu.BambuEndpoint(record.host, core_printers.tls_policy(record, pin_store), access_code,
                               str(record.options.get("serial", "")), record.effective_port, **kwargs)


def test_gen(record: PrinterRecord, ep: Endpoint | bambu.BambuEndpoint) -> Iterator:
    """Generator for the connection test of ``record``; returns its ``ConnectionInfo``."""
    client = {OCTOPRINT: octoprint, MOONRAKER: moonraker, BAMBU_LAN: bambu}.get(record.kind)
    if client is None:
        raise ValueError(NOT_SUPPORTED.get(record.kind, "unsupported printer kind"))
    return client.test_connection(ep)


test_gen.__test__ = False


def upload_gen(record: PrinterRecord, ep: Endpoint | bambu.BambuEndpoint, file_path: str, name: str, *, subfolder: str = "",
               start_print: bool = False, transfer: Transfer | None = None) -> Iterator:
    """Generator for the upload to ``record``; returns the client's ``UploadResult``."""
    if record.kind == OCTOPRINT:
        return octoprint.upload(ep, file_path, name, subfolder=subfolder, start_print=start_print, transfer=transfer)
    if record.kind == MOONRAKER:
        return moonraker.upload(ep, file_path, name, subfolder=subfolder, start_print=start_print, transfer=transfer)
    if record.kind == BAMBU_LAN:
        return bambu.upload(ep, file_path, name, start_print=start_print, transfer=transfer)
    raise ValueError(NOT_SUPPORTED.get(record.kind, "unsupported printer kind"))


def issue_for(exc: BaseException, printer_name: str, filename: str) -> dict:
    """The Issues-list entry (source ``send``) for a failed send: an ``error`` whose code is the failure's kind and whose
    message is the error's own text (which never holds a secret)."""
    kind = exc.kind if isinstance(exc, NetError) else "error"
    return {"level": ERROR, "code": f"send_{kind}", "message": f"Sending {filename} to {printer_name} failed: {exc}",
            "opt_key": None, "object_name": None}


def size_text(size: int) -> str:
    return f"{size / 1e6:.1f} MB" if size >= 1_000_000 else f"{max(1, round(size / 1e3))} kB"


def progress_text(transfer: Transfer, name: str) -> str:
    """The status text of an upload in flight."""
    if transfer.phase == "connecting":
        return f"Connecting to send {name}…"
    if transfer.phase == "waiting":
        return f"Waiting for the printer to accept {name}…"
    return f"Sending {name}: {size_text(transfer.sent)} of {size_text(transfer.total)}"


def temp_name(fmt: str) -> str:
    """The name of the temp file an upload is read from (never shown; the file name on the printer is separate)."""
    return "send.gcode.3mf" if fmt == THREEMF else "send.gcode"


def clean_temp_dir(path: str | None) -> None:
    """Remove a send's private temp directory and what is in it (never raises; only ever called for a directory the
    add-on made)."""
    if not path:
        return
    try:
        for name in os.listdir(path):
            try:
                os.remove(os.path.join(path, name))
            except OSError:
                pass
        os.rmdir(path)
    except OSError:
        pass
