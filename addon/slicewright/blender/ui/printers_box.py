# SPDX-License-Identifier: GPL-3.0-or-later
"""The Printers box in the add-on preferences (03 section 8.5): add, edit and remove physical printers."""
from __future__ import annotations

from ...core import printers as core
from .. import connections, online, printers, secrets_rt
from ..labels import draw_wrapped
from . import choice
from ... import names

_OP = names.PACKAGE_ID


def draw(box, prefs) -> None:
    box.label(text="Printers")
    online.draw_gate(box)
    if not hasattr(prefs, "printers") or isinstance(prefs.printers, tuple):
        box.label(text="Enable the add-on to manage printers")
        return
    row = box.row(align=True)
    for kind in core.KINDS:
        op = row.operator(f"{_OP}.printer_add", text=core.KIND_LABELS[kind].split(",")[0].split(" (")[0], icon="ADD")
        op.kind = kind
    if not prefs.printers:
        box.label(text="No printers yet. Printers are only contacted when you start a send.")
    for pg in prefs.printers:
        _draw_printer(box.box(), pg)


def _draw_printer(box, pg) -> None:
    head = box.row()
    head.prop(pg, "name", text="")
    remove = head.operator(f"{_OP}.printer_remove", text="", icon="X")
    remove.printer_id = pg.id
    box.label(text=core.KIND_LABELS.get(pg.kind, pg.kind))
    col = box.column(align=True)
    col.prop(pg, "host")
    col.prop(pg, "port")
    if pg.kind in (core.OCTOPRINT, core.MOONRAKER):
        col.prop(pg, "use_https")
        if pg.use_https:
            choice.draw_prop(col, pg, "tls_mode")
        col.prop(pg, "base_path")
        if pg.use_https and pg.tls_mode == "pin":
            _draw_pin(box, pg)
    else:
        col.prop(pg, "serial")
        draw_wrapped(col, "Needs LAN Only mode with Developer Mode enabled on the printer.", "INFO")
        draw_wrapped(col, "Not supported by Bambu. The certificate is always checked by its fingerprint.")
        _draw_pin(box, pg)
    box.prop(pg, "preset")
    label = core.SECRET_LABELS.get(pg.kind, "Secret")
    box.label(text=f"{label}: " + ("saved" if pg.secret_saved else "not set"), icon="CHECKMARK" if pg.secret_saved else "DOT")
    row = box.row(align=True)                       # the buttons on their own row: the label is never cut ("API key...")
    op = row.operator(f"{_OP}.printer_secret", text="Replace" if pg.secret_saved else "Set", icon="LOCKED")
    op.printer_id = pg.id
    if pg.secret_saved:
        row.operator(f"{_OP}.printer_secret_clear", text="Forget").printer_id = pg.id
    for problem in printers.problems(pg):
        line = box.column(align=True)
        line.alert = True
        draw_wrapped(line, problem.message, "ERROR")
    _draw_test(box, pg)


def _draw_test(box, pg) -> None:
    """The "Test connection" button and what the last test found. A test contacts the printer once, only when pressed."""
    row = box.row(align=True)
    sub = row.row(align=True)
    sub.enabled = online.network_allowed().allowed and not connections.is_running(pg.id)
    sub.operator(f"{_OP}.printer_test", text="Test connection", icon="DRIVER").printer_id = pg.id
    result = connections.state(pg.id)
    if result is not None:
        line = box.column(align=True)
        line.alert = result.level == "ERROR"
        draw_wrapped(line, result.text, "CHECKMARK" if result.level == "INFO" else ("TIME" if result.running else "ERROR"))


def _draw_pin(box, pg) -> None:
    """The trusted certificate of a printer that pins: what is trusted, the pending decision, and the way to forget it."""
    pin = box.column(align=True)
    row = pin.row(align=True)
    if pg.pin:
        row.label(text="Trusted certificate: " + short_fingerprint(pg.pin), icon="CHECKMARK")
        row.operator(f"{_OP}.printer_forget_pin", text="Forget").printer_id = pg.id
    else:
        row.label(text="No certificate trusted yet", icon="DOT")
    problem = printers.tls_problem(pg.id)
    if problem is None:
        return
    if problem.kind == "unknown":
        pin.alert = True
        pin.label(text="This device's certificate is not trusted yet.", icon="ERROR")
        pin.alert = False
        pin.operator(f"{_OP}.printer_trust_cert", text="Review and trust certificate...", icon="LOCKED").printer_id = pg.id
    else:
        pin.alert = True
        pin.label(text="The device presented a different certificate than the one you trusted.", icon="ERROR")
        pin.label(text="Connection refused. If the device was replaced or reset, forget the trusted certificate.")


def short_fingerprint(fingerprint: str) -> str:
    from ...core.network.tls import format_fingerprint
    text = format_fingerprint(fingerprint)
    return f"{text[:11]}...{text[-11:]}"
