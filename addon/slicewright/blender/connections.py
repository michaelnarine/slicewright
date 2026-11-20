# SPDX-License-Identifier: GPL-3.0-or-later
"""Talking to a printer from the add-on: building the client's endpoint, and the "Test connection" operator (03 section 8).

Nothing here runs by itself. A test starts only from the operator ``slicewright.printer_test`` (a button in the Printers box
of the preferences and in the Send panel) and only while ``online.network_allowed()`` says yes; it is a generator on the
tick runner (never blocks the UI), with short timeouts. The result (the version and state the printer reported, or the
error) is runtime state shown next to the printer. A TLS refusal that needs the user's decision (an unknown or a changed
certificate) is handed to ``printers.note_tls_problem``, which offers "trust" or "forget".

:func:`build_endpoint` is where a secret is read: ``secrets_rt.manager().get(record.secret_ref)`` just before the call,
wrapped at once in a ``SecretStr`` and held only by the endpoint of that one operation; it is never stored here, never in a
message and never in a log.
"""
from __future__ import annotations

from dataclasses import dataclass

import bpy
from bpy.props import StringProperty

from ..core import logs, printers as core, redact, send as core_send
from ..core.network.errors import InvalidRequest, NetError
from ..core.network.nbsocket import CancelToken, Timeouts
from ..core.network.secrets import SecretStoreError
from ..core.network.webapi import Endpoint
from . import online, printers, registry, secrets_rt, timers

REQUIRES_ENGINE = False
log = logs.get_logger("connections")
TEST_TIMEOUTS = Timeouts(connect=5.0, idle=8.0)
TASK = "net-test:"


class ConnectionProblem(Exception):
    """Something stopped the call before it started (no secret, an unreadable keychain); the message is for the user."""


@dataclass
class TestState:
    """The last connection test of one printer. ``level`` is ``INFO`` (it worked), ``ERROR`` or ``None`` (running)."""
    running: bool = False
    level: str | None = None
    text: str = ""

    __test__ = False


_tests: dict[str, TestState] = {}


def state(printer_id: str) -> TestState | None:
    return _tests.get(printer_id)


def is_running(printer_id: str) -> bool:
    entry = _tests.get(printer_id)
    return bool(entry is not None and entry.running)


def forget(printer_id: str) -> None:
    timers.runner.cancel(TASK + printer_id)
    _tests.pop(printer_id, None)


def _redraw() -> None:
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ("VIEW_3D", "PREFERENCES"):
                    area.tag_redraw()
    except Exception:  # noqa: BLE001 - background mode, or no window manager yet
        pass


# -- the endpoint -------------------------------------------------------------------------------------------------

def build_endpoint(record: core.PrinterRecord, *, prefs=None, cancel: CancelToken | None = None,
                   timeouts: Timeouts | None = None) -> Endpoint:
    """The client endpoint of ``record``. Reads the secret from the store *now* (a keychain call that may show the system's
    unlock prompt) and wraps it in a ``SecretStr``; a printer whose secret is required but missing, or a store that
    refuses, raises :class:`ConnectionProblem` before anything is sent."""
    key = None
    if record.secret_ref and record.secret_saved:
        try:
            value = secrets_rt.manager().get(record.secret_ref)
        except SecretStoreError as exc:
            raise ConnectionProblem(f"The saved {core.secret_noun(record.kind)} could not be "
                                    f"read: {exc}") from None
        if value:
            redact.register(value)
            key = redact.SecretStr(value)
    if key is None and core.SECRET_REQUIRED.get(record.kind):
        raise ConnectionProblem(f"Set the {core.secret_noun(record.kind)} of "
                                f"{record.name} first")
    kwargs = {"timeouts": timeouts} if timeouts is not None else {}
    return Endpoint(core.base_url(record), key, tls=printers.tls_policy_for(record, prefs), cancel=cancel, **kwargs)


def describe_error(exc: BaseException, printer_id: str = "") -> str:
    """The user-facing text of a failed call; records a TLS decision for the printer when there is one to make."""
    if printer_id and printers.note_tls_problem(printer_id, exc) is not None:
        if exc.kind == "tls_unknown":
            return f"{exc}. Review and trust it to continue"
        return str(exc)
    if isinstance(exc, (NetError, ConnectionProblem)):
        return redact.redact(str(exc))
    log.exception("unexpected error in a printer call")
    return f"Unexpected error: {exc.__class__.__name__}"


# -- the test ----------------------------------------------------------------------------------------------------

def blocker(printer_id: str, prefs=None) -> str | None:
    """Why a test cannot start now, or ``None``."""
    access = online.network_allowed()
    if not access.allowed:
        return access.reason
    pg = printers.find(printer_id, prefs)
    if pg is None:
        return "This printer no longer exists"
    if is_running(printer_id):
        return "A test is already running"
    return core_send.send_blocker(printers_pg_record(pg))


def printers_pg_record(pg) -> core.PrinterRecord:
    from . import printers_pg
    return printers_pg.to_record(pg)


def _guarded(gen):
    """Run ``gen`` and return its result, or the ``NetError`` it raised: a refused or failed test is an expected outcome,
    not a crashed task (the tick runner logs those with a traceback)."""
    try:
        return (yield from gen)
    except NetError as exc:
        return exc


def start_test(printer_id: str, prefs=None) -> str | None:
    """Start the connection test of the printer. Returns ``None`` when it started, else the reason it did not (also
    recorded as the printer's result)."""
    why = blocker(printer_id, prefs)
    if why:
        if printers.find(printer_id, prefs) is not None and not is_running(printer_id):
            _tests[printer_id] = TestState(False, "ERROR", why)
            _redraw()
        return why
    record = printers_pg_record(printers.find(printer_id, prefs))
    printers.clear_tls_problem(printer_id)
    try:
        endpoint = build_endpoint(record, prefs=prefs, timeouts=TEST_TIMEOUTS)
    except ConnectionProblem as exc:
        _tests[printer_id] = TestState(False, "ERROR", str(exc))
        _redraw()
        return str(exc)
    entry = _tests[printer_id] = TestState(True, None, "Testing the connection…")

    def done(info) -> None:
        if isinstance(info, BaseException):
            failed(info)
            return
        entry.running, entry.level, entry.text = False, "INFO", info.summary()
        _redraw()

    def failed(exc: BaseException) -> None:
        entry.running, entry.level, entry.text = False, "ERROR", describe_error(exc, printer_id)
        _redraw()

    def cancelled() -> None:
        entry.running, entry.level, entry.text = False, "ERROR", "Cancelled"
        _redraw()

    timers.runner.submit(TASK + printer_id, _guarded(core_send.test_gen(record, endpoint)), on_done=done, on_error=failed,
                         on_cancel=cancelled, replace=True)
    _redraw()
    return None


class SLICEWRIGHT_OT_printer_test(bpy.types.Operator):
    bl_idname = "slicewright.printer_test"
    bl_label = "Test connection"
    bl_description = ("Contact the printer once and show what it answers: its software and version, and whether the API "
                      "key is accepted. Nothing is sent or changed")
    bl_options = {"INTERNAL"}

    printer_id: StringProperty(options={"SKIP_SAVE", "HIDDEN"})

    @classmethod
    def poll(cls, context):
        return online.poll_network(cls)

    def execute(self, context):
        prefs = printers._live_prefs(self)
        if prefs is None:
            return {"CANCELLED"}
        why = start_test(self.printer_id, prefs)
        if why:
            self.report({"WARNING"}, why)
            return {"CANCELLED"}
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_printer_test,)


def register() -> None:
    registry.register_classes(classes)


def unregister() -> None:
    for name in [t for t in _tests]:
        timers.runner.cancel(TASK + name)
    _tests.clear()
    registry.unregister_classes(classes)
