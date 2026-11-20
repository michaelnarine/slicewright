# SPDX-License-Identifier: GPL-3.0-or-later
"""Physical printers in the preferences: add, remove, and the secret dialog (03 section 8.5).

The records live in ``prefs.printers`` (``printers_pg.py``) and never hold a secret. The secret is typed into the dialog of
``slicewright.printer_secret``: a ``PASSWORD`` string property of the *operator* (``SKIP_SAVE``, so Blender does not
remember it as the last-used value, and the operator has no ``REGISTER`` flag, so it is not logged in the Info editor or
offered for redo). ``execute`` hands it to the secret store and blanks the property before returning; it is never copied
into a scene, preference or ID property. Nothing here touches the network.
"""
from __future__ import annotations

from dataclasses import dataclass

import bpy
from bpy.props import EnumProperty, StringProperty

from ..core import logs, printers as core
from ..core.network import tls
from ..core.network.errors import TlsPinMismatch, TlsUnknownCertificate
from ..core.network.secrets import SecretStoreError
from . import printers_pg, registry, secrets_rt

REQUIRES_ENGINE = False
log = logs.get_logger("printers")


def _prefs():
    from .. import prefs
    return prefs.get()


def records(prefs=None) -> list[core.PrinterRecord]:
    """The printers as plain records (empty when the add-on is not enabled in the preferences)."""
    return [printers_pg.to_record(pg) for pg in (prefs if prefs is not None else _prefs()).printers]


def find(printer_id: str, prefs=None):
    """The property group of printer ``printer_id``, or ``None``."""
    for pg in (prefs if prefs is not None else _prefs()).printers:
        if pg.id == printer_id:
            return pg
    return None


def problems(pg, *, complete: bool = False) -> list[core.Problem]:
    """What is wrong with this printer's settings (see ``core.printers.validate``)."""
    record = printers_pg.to_record(pg)
    others = [printers_pg.to_record(o) for o in _prefs().printers if o.id != pg.id]
    return core.validate(record, others, complete=complete)


def _live_prefs(operator):
    prefs = _prefs()
    if not hasattr(prefs, "printers") or isinstance(prefs.printers, tuple):
        operator.report({"ERROR"}, "Enable the Slicewright add-on in Preferences to manage printers")
        return None
    return prefs


# -- certificate pins (03 section 8.4 "TLS trust") ----------------------------------------------------------------

class PrefsPinStore:
    """The :class:`~slicewright.core.network.tls.PinStore` of the add-on: a printer's pinned certificate fingerprint is
    kept in the printer's own preferences entry (``pin``, which ends up in ``options["pin"]`` of its record). It is
    public data, so unlike the secret it lives with the record and goes wherever the preferences go. Keyed by the printer
    ``id``; a printer that no longer exists has no pin. Reads the live preferences on every call, so a removed or edited
    printer is never answered from a stale copy."""

    def __init__(self, prefs=None) -> None:
        self._prefs = prefs

    def _pg(self, key: str):
        return find(key, self._prefs)

    def get(self, key: str) -> str | None:
        pg = self._pg(key)
        if pg is None or not pg.pin:
            return None
        try:
            return tls.normalize_fingerprint(pg.pin)
        except ValueError:
            return None                      # damaged: treated as no pin, so the user is asked again, never trusted

    def set(self, key: str, fingerprint: str) -> None:
        pg = self._pg(key)
        if pg is None:
            raise KeyError(key)
        pg.pin = tls.normalize_fingerprint(fingerprint)

    def remove(self, key: str) -> None:
        pg = self._pg(key)
        if pg is not None:
            pg.pin = ""


def pin_store(prefs=None) -> PrefsPinStore:
    return PrefsPinStore(prefs)


def tls_policy_for(record: core.PrinterRecord, prefs=None) -> tls.TlsPolicy:
    """The TLS policy a client uses for ``record``, pinning against the preferences when the printer asks for it."""
    return core.tls_policy(record, pin_store(prefs))


@dataclass(frozen=True)
class TlsProblem:
    """The last TLS refusal of a printer that needs the user's decision. ``kind`` is ``unknown`` (a certificate that is
    not trusted yet: ``fingerprint`` is what the device showed) or ``mismatch`` (not the pinned one: ``expected`` and
    ``fingerprint`` differ). ``host`` and ``port`` are what was contacted, so a confirmation after the address was edited
    is refused."""
    kind: str
    fingerprint: str
    expected: str
    host: str
    port: int
    message: str


_problems: dict[str, TlsProblem] = {}


def note_tls_problem(printer_id: str, exc: BaseException) -> TlsProblem | None:
    """Remember a ``TlsUnknownCertificate`` or ``TlsPinMismatch`` for the printer so the preferences (and the Send panel)
    can offer "Trust this certificate" or explain the mismatch. Returns the problem, or ``None`` for any other error."""
    if isinstance(exc, TlsUnknownCertificate):
        problem = TlsProblem("unknown", exc.fingerprint, "", exc.host, exc.port or 0, str(exc))
    elif isinstance(exc, TlsPinMismatch):
        problem = TlsProblem("mismatch", exc.got, exc.expected, exc.host, exc.port or 0, str(exc))
    else:
        return None
    _problems[printer_id] = problem
    from .preview import runtime
    runtime.tag_redraw()
    return problem


def tls_problem(printer_id: str) -> TlsProblem | None:
    return _problems.get(printer_id)


def clear_tls_problem(printer_id: str) -> None:
    _problems.pop(printer_id, None)


def fingerprint_lines(fingerprint: str) -> list[str]:
    """``AB:CD:...`` in two lines of 16 bytes, for a dialog."""
    text = tls.format_fingerprint(fingerprint)
    return [text[:47], text[48:]]


class SLICEWRIGHT_OT_printer_trust_cert(bpy.types.Operator):
    bl_idname = "slicewright.printer_trust_cert"
    bl_label = "Trust this certificate?"
    bl_description = ("Remember this device's certificate fingerprint and require it from now on. Only do this if the "
                      "fingerprint is the one your own printer shows")
    bl_options = {"INTERNAL"}

    printer_id: StringProperty(options={"SKIP_SAVE", "HIDDEN"})

    def _problem(self):
        problem = tls_problem(self.printer_id)
        return problem if problem is not None and problem.kind == "unknown" else None

    def invoke(self, context, event):
        if self._problem() is None:
            self.report({"WARNING"}, "There is no certificate waiting to be trusted: test the connection first")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=440, confirm_text="Trust certificate")

    def draw(self, context):
        problem, pg = self._problem(), find(self.printer_id)
        layout = self.layout
        if problem is None or pg is None:
            layout.label(text="Nothing to trust (test the connection again)")
            return
        layout.label(text=f"{pg.name} ({problem.host}:{problem.port}) presented a certificate")
        layout.label(text="that is not trusted yet. SHA-256 fingerprint:")
        col = layout.column(align=True)
        for line in fingerprint_lines(problem.fingerprint):
            col.label(text=line)
        layout.label(text="Trust it only if it matches what the printer shows.")

    def execute(self, context):
        prefs = _live_prefs(self)
        pg = find(self.printer_id, prefs) if prefs is not None else None
        problem = self._problem()
        if pg is None or problem is None:
            self.report({"ERROR"}, "There is no certificate waiting to be trusted: test the connection again")
            return {"CANCELLED"}
        record = printers_pg.to_record(pg)
        if (record.host, record.effective_port) != (problem.host, problem.port):
            clear_tls_problem(pg.id)
            self.report({"ERROR"}, "The printer's address changed since that certificate was seen: test the connection again")
            return {"CANCELLED"}
        if pg.pin:
            self.report({"ERROR"}, "This printer already has a trusted certificate. Forget it first if the device was "
                                   "replaced or reset")
            return {"CANCELLED"}
        pin_store(prefs).set(pg.id, problem.fingerprint)
        clear_tls_problem(pg.id)
        log.info("printer %s: trusted certificate %s", pg.name, problem.fingerprint)
        self.report({"INFO"}, f"Trusted the certificate of {pg.name}. Run the operation again")
        return {"FINISHED"}


class SLICEWRIGHT_OT_printer_forget_pin(bpy.types.Operator):
    bl_idname = "slicewright.printer_forget_pin"
    bl_label = "Forget trusted certificate"
    bl_description = ("Forget the certificate trusted for this printer. The next connection asks you to trust whatever "
                      "certificate the device then presents: do this only if you replaced or reset the device")
    bl_options = {"INTERNAL"}

    printer_id: StringProperty(options={"SKIP_SAVE", "HIDDEN"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title="Forget the trusted certificate?",
            message="The next connection will ask you to trust whatever certificate the device presents.",
            confirm_text="Forget")

    def execute(self, context):
        prefs = _live_prefs(self)
        pg = find(self.printer_id, prefs) if prefs is not None else None
        if pg is None:
            return {"CANCELLED"}
        pin_store(prefs).remove(pg.id)
        clear_tls_problem(pg.id)
        log.info("printer %s: forgot the trusted certificate", pg.name)
        self.report({"INFO"}, f"Forgot the trusted certificate of {pg.name}")
        return {"FINISHED"}


class SLICEWRIGHT_OT_printer_add(bpy.types.Operator):
    bl_idname = "slicewright.printer_add"
    bl_label = "Add printer"
    bl_description = "Add a printer the add-on can send G-code to"
    bl_options = {"INTERNAL"}

    kind: EnumProperty(name="Kind", items=[(k, core.KIND_LABELS[k], "") for k in core.KINDS], default=core.OCTOPRINT,
                       options={"SKIP_SAVE"})

    def execute(self, context):
        prefs = _live_prefs(self)
        if prefs is None:
            return {"CANCELLED"}
        record = core.new_record(records(prefs), kind=self.kind)
        printers_pg.fill(prefs.printers.add(), record)
        return {"FINISHED"}


class SLICEWRIGHT_OT_printer_remove(bpy.types.Operator):
    bl_idname = "slicewright.printer_remove"
    bl_label = "Remove printer"
    bl_description = "Remove this printer and delete its saved access code or API key"
    bl_options = {"INTERNAL"}

    printer_id: StringProperty(options={"SKIP_SAVE", "HIDDEN"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title="Remove printer?",
                                                     message="Its saved access code or API key is deleted too.",
                                                     confirm_text="Remove")

    def execute(self, context):
        prefs = _live_prefs(self)
        pg = find(self.printer_id, prefs) if prefs is not None else None
        if pg is None:
            return {"CANCELLED"}
        if pg.secret_ref:                                  # the secret first: if the keychain refuses, keep both
            try:
                secrets_rt.manager().delete(pg.secret_ref)
            except SecretStoreError as exc:
                self.report({"ERROR"}, f"The printer was not removed: {exc}")
                return {"CANCELLED"}
        for index, item in enumerate(prefs.printers):
            if item.id == self.printer_id:
                prefs.printers.remove(index)
                clear_tls_problem(self.printer_id)
                break
        return {"FINISHED"}


class SLICEWRIGHT_OT_printer_secret(bpy.types.Operator):
    bl_idname = "slicewright.printer_secret"
    bl_label = "Set access code or API key"
    bl_description = "Save the printer's access code or API key in the system keychain"
    bl_options = {"INTERNAL"}                              # no REGISTER: not logged, not repeatable

    printer_id: StringProperty(options={"SKIP_SAVE", "HIDDEN"})
    secret: StringProperty(name="Secret", subtype="PASSWORD", options={"SKIP_SAVE"},
                           description="Stored in the system keychain, never in the .blend file or the preferences")

    def invoke(self, context, event):
        self.secret = ""
        return context.window_manager.invoke_props_dialog(self, width=360, confirm_text="Save")

    def draw(self, context):
        pg = find(self.printer_id)
        kind = pg.kind if pg is not None else core.OCTOPRINT
        layout = self.layout
        layout.label(text=f"{pg.name if pg is not None else 'Printer'}: {core.SECRET_LABELS.get(kind, 'Secret')}")
        layout.prop(self, "secret", text="")
        text, warn = secrets_rt.status_lines()[0]
        layout.label(text=text, icon="ERROR" if warn else "LOCKED")

    def execute(self, context):
        secret, self.secret = self.secret, ""              # blank the property before anything else can see it
        prefs = _live_prefs(self)
        pg = find(self.printer_id, prefs) if prefs is not None else None
        if pg is None:
            return {"CANCELLED"}
        try:
            updated = core.save_secret(printers_pg.to_record(pg), secret, secrets_rt.manager())
            warning = secrets_rt.manager().take_warning()
        except ValueError:
            self.report({"ERROR"}, "Enter the access code or API key (no control characters)")
            return {"CANCELLED"}
        except SecretStoreError as exc:
            self.report({"ERROR"}, f"Not saved: {exc}")
            return {"CANCELLED"}
        finally:
            secret = ""
        pg.secret_ref, pg.secret_saved = updated.secret_ref, True
        self.report({"INFO"}, f"Saved the {core.SECRET_LABELS.get(pg.kind, 'secret').lower()} for {pg.name}")
        if warning:
            self.report({"WARNING"}, warning)
        return {"FINISHED"}


class SLICEWRIGHT_OT_printer_secret_clear(bpy.types.Operator):
    bl_idname = "slicewright.printer_secret_clear"
    bl_label = "Forget access code or API key"
    bl_description = "Delete the saved access code or API key of this printer"
    bl_options = {"INTERNAL"}

    printer_id: StringProperty(options={"SKIP_SAVE", "HIDDEN"})

    def execute(self, context):
        prefs = _live_prefs(self)
        pg = find(self.printer_id, prefs) if prefs is not None else None
        if pg is None:
            return {"CANCELLED"}
        try:
            updated = core.clear_secret(printers_pg.to_record(pg), secrets_rt.manager())
        except SecretStoreError as exc:
            self.report({"ERROR"}, f"Not removed: {exc}")
            return {"CANCELLED"}
        pg.secret_saved = updated.secret_saved
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_printer_add, SLICEWRIGHT_OT_printer_remove, SLICEWRIGHT_OT_printer_secret,
           SLICEWRIGHT_OT_printer_secret_clear, SLICEWRIGHT_OT_printer_trust_cert, SLICEWRIGHT_OT_printer_forget_pin)


def register() -> None:
    registry.register_classes(classes)


def unregister() -> None:
    _problems.clear()
    registry.unregister_classes(classes)
