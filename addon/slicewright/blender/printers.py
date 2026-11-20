# SPDX-License-Identifier: GPL-3.0-or-later
"""Physical printers in the preferences: add, remove, and the secret dialog (03 section 8.5).

The records live in ``prefs.printers`` (``printers_pg.py``) and never hold a secret. The secret is typed into the dialog of
``slicewright.printer_secret``: a ``PASSWORD`` string property of the *operator* (``SKIP_SAVE``, so Blender does not
remember it as the last-used value, and the operator has no ``REGISTER`` flag, so it is not logged in the Info editor or
offered for redo). ``execute`` hands it to the secret store and blanks the property before returning; it is never copied
into a scene, preference or ID property. Nothing here touches the network.
"""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, StringProperty

from ..core import logs, printers as core
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
           SLICEWRIGHT_OT_printer_secret_clear)


def register() -> None:
    registry.register_classes(classes)


def unregister() -> None:
    registry.unregister_classes(classes)
