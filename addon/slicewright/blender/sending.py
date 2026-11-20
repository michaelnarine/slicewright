# SPDX-License-Identifier: GPL-3.0-or-later
"""Sending the last slice to a physical printer (03 section 8): the runtime of the Send panel and operators.

What is sent is what the Export panel would write: the result the preview shows (``exporting.source_for``). The file goes
to a private temporary directory first (G-code through the same atomic write as an export; a ``.gcode.3mf`` through
``exporting.export_3mf(quiet=True)``, which slices again first when the engine's result is gone) and is uploaded **from that
file** by the client generator on the tick runner: nothing blocks the UI, every step is bounded, Cancel closes the socket.
The temp directory is removed when the send ends, however it ends (done, failed, cancelled, file load, unregister).

Nothing here contacts a printer unless the user starts a send, and only while ``online.network_allowed()`` says yes. A
secret is read from the keychain just before the upload (``connections.build_endpoint``) and lives only in that upload's
endpoint. A print is started only for ``start_print`` after the operator's confirmation popup (``operators/send.py``).

Results: a line in the Send panel and the status bar, and for a failure also an entry in the Issues list (source ``send``).
"""
from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

import bpy

from ..core import export as core_export, printers as core_printers, redact, send as core_send
from ..core.export import ExportError
from ..core.network import access
from ..core.network.errors import Cancelled, NetError
from ..core.network.webapi import Transfer
from ..names import PACKAGE_ID
from . import connections, exporting, issues, printers, printers_pg, registry, send_pg, slicing, timers
from .preview import runtime

REQUIRES_ENGINE = True
TASK = "send"
STATUS_HOLD_S = 8.0
log = logging.getLogger("slicewright.send")


class SendError(Exception):
    """A send that cannot start; the message is for the user."""


@dataclass
class _Send:
    scene: str
    printer_id: str
    printer_name: str
    record: core_printers.PrinterRecord
    filename: str
    subfolder: str
    fmt: str
    start_print: bool
    temp_dir: str
    temp_path: str
    transfer: Transfer = field(default_factory=Transfer)
    phase: str = "preparing"                  # preparing | slicing | uploading
    text: str = ""
    task: Any = None
    last_draw: float = 0.0
    finished: bool = False


class _Runtime:
    def __init__(self) -> None:
        self.job: _Send | None = None
        self.messages: dict[str, tuple[str, str]] = {}      # scene name -> (level, text) of the last send


_rt = _Runtime()


# -- the panel's state --------------------------------------------------------------------------------------------

def state(context=None):
    """The window manager's Send choices (``SLICEWRIGHT_PG_Send``), or ``None`` before registration."""
    wm = (context or bpy.context).window_manager
    return getattr(wm, send_pg.WM_PROP, None)


def all_records() -> list[core_printers.PrinterRecord]:
    try:
        return printers.records()
    except Exception:  # noqa: BLE001 - preferences not available: no printers
        return []


def eligible(scene, *, show_all: bool = False) -> list[core_printers.PrinterRecord]:
    """The printers offered for the scene's printer preset (``core.send.eligible_printers``)."""
    sw = getattr(scene, PACKAGE_ID, None)
    return core_send.eligible_printers(all_records(), sw.printer_id if sw is not None else "", show_all=show_all)


def chosen(scene, send=None) -> core_printers.PrinterRecord | None:
    """The record the panel's printer field names; with nothing named, the only eligible printer, if there is exactly one."""
    send = send if send is not None else state()
    show_all = bool(send is not None and send.show_all)
    name = (send.printer if send is not None else "").strip().lower()
    if name:
        for record in all_records():
            if record.name.lower() == name:
                return record
        return None
    offered = eligible(scene, show_all=show_all)
    return offered[0] if len(offered) == 1 else None


def format_of(record: core_printers.PrinterRecord | None, send=None) -> str:
    send = send if send is not None else state()
    return core_send.resolve_format(record.kind if record is not None else core_printers.OCTOPRINT,
                                    send.file_format if send is not None else core_send.AUTO)


def default_name(scene, fmt: str) -> str:
    """The name the file gets on the printer when the user typed none."""
    source = exporting.source_for(scene)
    engine_name = exporting.engine_name(source) if source is not None else f"{exporting.basename()}.gcode"
    return core_send.target_filename(engine_name, fmt)


def filename_for(scene, fmt: str, send=None) -> str:
    send = send if send is not None else state()
    source = exporting.source_for(scene)
    engine_name = exporting.engine_name(source) if source is not None else f"{exporting.basename()}.gcode"
    return core_send.target_filename(engine_name, fmt, send.filename if send is not None else "")


def availability(scene, send=None) -> tuple[bool, str]:
    """``(ok, why not)`` for the Send button, given the panel's choices. The reason is what the panel (and the button's
    tooltip) say when it is greyed out."""
    send = send if send is not None else state()
    if getattr(scene, PACKAGE_ID, None) is None or send is None:
        return False, ""
    gate = access.network_allowed()
    if not gate.allowed:
        return False, gate.reason
    if _rt.job is not None:
        return False, "Wait for the send to finish"
    record = chosen(scene, send)
    fmt = format_of(record, send)
    ok, why = exporting.availability(scene, exporting.THREEMF if fmt == core_send.THREEMF else exporting.GCODE, "send")
    if not ok:
        return False, why
    why = core_send.send_blocker(record) or core_send.options_blocker(record, fmt, send.subfolder)
    if why:
        return False, why
    if send.start_print:
        why = core_send.start_print_blocker(record, fmt)
        if why:
            return False, why
    return True, ""


# -- messages -----------------------------------------------------------------------------------------------------

def message(scene) -> tuple[str, str] | None:
    """``(level, text)`` of the last send of ``scene`` (``INFO``, ``WARNING`` or ``ERROR``), or ``None``."""
    return _rt.messages.get(scene.name)


def set_message(scene, level: str | None, text: str = "") -> None:
    if level:
        _rt.messages[scene.name] = (level, text)
    else:
        _rt.messages.pop(scene.name, None)
    runtime.tag_redraw()


def is_live() -> bool:
    return _rt.job is not None


def progress(scene) -> tuple[float, str] | None:
    """``(fraction, text)`` of the send in flight for ``scene``, or ``None``."""
    job = _rt.job
    if job is None or job.scene != scene.name:
        return None
    if job.phase == "slicing":
        shown = exporting.progress(scene)
        return (shown[0], shown[1]) if shown is not None else (0.0, job.text)
    if job.phase == "uploading":
        return job.transfer.fraction, core_send.progress_text(job.transfer, job.filename)
    return 0.0, job.text


def _status_text(text: str | None) -> None:
    slicing._set_status_text(text)


def _clear_status() -> None:
    if _rt.job is None:
        _status_text(None)
    return None


def _announce(job: _Send, text: str) -> None:
    job.text = text
    _status_text(text)
    runtime.tag_redraw()


# -- starting -----------------------------------------------------------------------------------------------------

def start(scene, *, allow_stale: bool = False, confirmed_print: bool = False) -> str:
    """Start sending the last slice with the panel's choices. Returns the first status text; the work continues on the
    tick runner. Raises :class:`SendError` (nothing was sent) with the reason. ``start_print`` needs ``confirmed_print``:
    the operator passes it only after its confirmation popup, so no code path starts a print by accident."""
    send = state()
    ok, why = availability(scene, send)
    if not ok:
        raise SendError(why or "Nothing to send")
    record = chosen(scene, send)
    fmt = format_of(record, send)
    start_print = bool(send.start_print)
    if start_print and not confirmed_print:
        raise SendError("Starting a print needs your confirmation")
    sw = getattr(scene, PACKAGE_ID)
    if sw.stale and not allow_stale:
        raise SendError("The preview is out of date: the file would hold the older slice")
    source = exporting.source_for(scene)
    if source is None:
        raise SendError("There is no slice to send")
    try:
        name = core_send.target_filename(exporting.engine_name(source), fmt, send.filename)
        from ..core.network.webapi import check_filename, check_subfolder
        check_filename(name)
        subfolder = "" if record.kind == core_printers.BAMBU_LAN else check_subfolder(send.subfolder)
    except Exception as exc:  # noqa: BLE001 - InvalidRequest from the checks: the user's input
        raise SendError(str(exc)) from None
    issues.clear(core_send.ISSUES_SOURCE)
    set_message(scene, None)
    temp_dir = tempfile.mkdtemp(prefix="slicewright-send-")          # 0700, ours alone
    job = _Send(scene.name, record.id, record.name, record, name, subfolder, fmt, start_print, temp_dir,
                f"{temp_dir}/{core_send.temp_name(fmt)}")
    _rt.job = job
    _announce(job, f"Preparing {name}…")
    try:
        if fmt == core_send.GCODE:
            core_export.atomic_write(job.temp_path, source.result.write_gcode, overwrite=True)
            _begin_upload(job)
        else:
            exporting.export_3mf(scene, job.temp_path, overwrite=True, quiet=True,
                                 issues_source=core_send.ISSUES_SOURCE, then=lambda ok, text: _after_3mf(job, ok, text))
            if exporting.is_live():                                  # sliced again first: the file follows
                job.phase = "slicing"
                _announce(job, "Slicing again to write the .gcode.3mf…")
            else:
                _begin_upload(job)
    except ExportError as exc:
        _finish(job, "ERROR", f"Not sent: {exc}", exc)
        raise SendError(str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - a write or engine failure: say it, leave nothing behind
        log.exception("preparing the file to send failed")
        _finish(job, "ERROR", f"Not sent: {exc}", exc)
        raise SendError(str(exc)) from None
    return job.text


def _after_3mf(job: _Send, written: bool, text: str) -> None:
    """The re-slice for the ``.gcode.3mf`` ended (``exporting``'s callback)."""
    if job.finished or _rt.job is not job:
        return
    if not written:
        if text == "cancelled":
            _finish(job, "INFO", "Send cancelled")
        else:
            _finish(job, "ERROR", f"Not sent: {text}", ExportError(text))
        return
    _begin_upload(job)


def _begin_upload(job: _Send) -> None:
    """The file is ready: build the endpoint (reading the secret now) and hand the upload to the tick runner."""
    try:
        endpoint = connections.build_endpoint(job.record)
    except connections.ConnectionProblem as exc:
        _finish(job, "ERROR", f"Not sent: {exc}", exc)
        return
    job.phase = "uploading"
    job.transfer = Transfer()
    _announce(job, core_send.progress_text(job.transfer, job.filename))
    gen = _drive(job, core_send.upload_gen(job.record, endpoint, job.temp_path, job.filename, subfolder=job.subfolder,
                                           start_print=job.start_print, transfer=job.transfer))
    job.task = timers.runner.submit(
        TASK, gen, on_done=lambda result: _done(job, result), on_error=lambda exc: _failed(job, exc),
        on_cancel=lambda: _finish(job, "INFO", "Send cancelled"), replace=True)


def _drive(job: _Send, gen):
    """Pass the upload's steps through, keeping the status text and the panel up to date (at most ten times a second)."""
    try:
        while True:
            value = next(gen)
            now = time.monotonic()
            if now - job.last_draw >= 0.1:
                job.last_draw = now
                _announce(job, core_send.progress_text(job.transfer, job.filename))
            yield value
    except StopIteration as stop:
        return stop.value
    except NetError as exc:
        return exc                       # a refused or failed upload is an expected outcome, not a crashed task


# -- finishing ----------------------------------------------------------------------------------------------------

def _done(job: _Send, result) -> None:
    if isinstance(result, BaseException):
        _failed(job, result)
        return
    text = f"{result.summary()} ({job.printer_name})"
    level = "INFO"
    if job.start_print and not getattr(result, "printing", False):
        level, text = "WARNING", f"{text}. The printer did not start the print"
    _finish(job, level, text)


def _failed(job: _Send, exc: BaseException) -> None:
    if isinstance(exc, Cancelled):
        _finish(job, "INFO", "Send cancelled")
        return
    _finish(job, "ERROR", f"Not sent: {connections.describe_error(exc, job.printer_id)}", exc)


def _finish(job: _Send, level: str, text: str, exc: BaseException | None = None) -> None:
    """End ``job`` once: remove its temp directory, record the message (and the Issues entry of a failure), clear the busy
    state. Safe to call again."""
    if job.finished:
        return
    job.finished = True
    if _rt.job is job:
        _rt.job = None
    core_send.clean_temp_dir(job.temp_dir)
    scene = bpy.data.scenes.get(job.scene)
    text = redact.redact(text)
    log.info("send to %s: %s", job.printer_name, text)
    if scene is not None:
        set_message(scene, level, text)
        if level == "ERROR":
            issues.set_source(scene, core_send.ISSUES_SOURCE,
                              [core_send.issue_for(exc or RuntimeError(text), job.printer_name, job.filename)])
    _status_text(text)
    registry.register_timer(_clear_status, first_interval=STATUS_HOLD_S)
    runtime.tag_redraw()


def cancel() -> bool:
    """Cancel the send in flight: the upload's socket is closed (the printer stores nothing), or the re-slice for the
    ``.gcode.3mf`` is cancelled. False if nothing is being sent."""
    job = _rt.job
    if job is None:
        return False
    if job.phase == "uploading" and job.task is not None:
        return timers.runner.cancel(job.task)
    if job.phase == "slicing":
        return exporting.cancel()
    _finish(job, "INFO", "Send cancelled")
    return True


# -- lifecycle ----------------------------------------------------------------------------------------------------

def _abort() -> None:
    """Drop everything without messages (a file load, unregister)."""
    job, _rt.job = _rt.job, None
    if job is not None:
        job.finished = True
        timers.runner.cancel(TASK)
        core_send.clean_temp_dir(job.temp_dir)
    _status_text(None)


def _on_load_pre() -> None:
    _abort()
    _rt.messages.clear()
    issues.clear(core_send.ISSUES_SOURCE)


def register() -> None:
    registry.register_classes(send_pg.classes)
    bpy.types.WindowManager.slicewright_send = bpy.props.PointerProperty(type=send_pg.SLICEWRIGHT_PG_Send)
    if _on_load_pre not in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.append(_on_load_pre)


def unregister() -> None:
    while _on_load_pre in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.remove(_on_load_pre)
    _abort()
    _rt.messages.clear()
    if hasattr(bpy.types.WindowManager, send_pg.WM_PROP):
        delattr(bpy.types.WindowManager, send_pg.WM_PROP)
    registry.unregister_classes(send_pg.classes)
