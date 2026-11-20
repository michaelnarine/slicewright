# SPDX-License-Identifier: GPL-3.0-or-later
"""The Send panel under the Export panel (03 section 8): choose a printer, a file and a name, and send.

Everything that talks to a printer is greyed out, with the reason written under it, unless Blender's "Allow Online
Access" is on, there is a slice, no slice or export is running and the printer is complete. While a file goes out the panel
shows a progress bar with Cancel. The line under the buttons says what the last send did.
"""
from __future__ import annotations

import bpy

from ...core import printers as core_printers, send as core_send
from ...names import PACKAGE_ID, TAB_NAME
from .. import connections, online, printers, sending

STALE_HINT = "Preview out of date: sends the older slice"


def draw_send(layout, context) -> None:
    scene = context.scene
    send = sending.state(context)
    if send is None:
        return
    allowed = online.draw_gate(layout)
    running = sending.progress(scene)
    records = sending.all_records()
    if not records:
        layout.label(text="Add a printer in the add-on preferences first", icon="INFO")
        return
    record = sending.chosen(scene, send)
    col = layout.column(align=True)
    col.enabled = running is None
    row = col.row(align=True)
    row.prop(send, "printer", text="Printer", icon="PLUGIN")
    row.prop(send, "show_all", text="", icon="FILTER")
    if record is not None:
        fmt = sending.format_of(record, send)
        col.prop(send, "file_format")
        col.prop(send, "filename", text="Name", placeholder=sending.default_name(scene, fmt))
        if record.kind != core_printers.BAMBU_LAN:         # a Bambu printer stores files in its root
            col.prop(send, "subfolder")
        else:
            col.label(text=core_printers.KIND_LABELS[record.kind], icon="INFO")
        sub = col.column()
        sub.enabled = record.kind != core_printers.BAMBU_LAN        # until the MQTT layer can confirm a print
        sub.prop(send, "start_print")
    elif send.printer.strip():
        layout.label(text="No printer with that name", icon="ERROR")
    if running is not None:                     # a bar and Cancel instead of the buttons
        row = layout.row(align=True)
        row.progress(factor=running[0], type="BAR", text=running[1])
        row.operator(f"{PACKAGE_ID}.send_cancel", text="", icon="X")
    else:
        row = layout.row(align=True)
        row.scale_y = 1.3
        label = "Send to printer" if record is None else f"Send to {record.name}"
        row.operator(f"{PACKAGE_ID}.send", text=label, icon="EXPORT")
        if record is not None:
            test = row.row(align=True)
            test.enabled = allowed and not connections.is_running(record.id)
            test.operator(f"{PACKAGE_ID}.printer_test", text="", icon="DRIVER").printer_id = record.id
        ok, why = sending.availability(scene, send)
        if not ok and why and allowed:          # (the gate's own reason is already drawn above)
            layout.label(text=why, icon="INFO")
        elif ok and getattr(scene, PACKAGE_ID).stale:
            layout.label(text=STALE_HINT, icon="INFO")
    if record is not None:
        _draw_test_result(layout, record)
        _draw_tls(layout, record)
    last = sending.message(scene)
    if last is not None:
        level, text = last
        row = layout.row()
        row.alert = level in ("ERROR", "WARNING")
        row.label(text=text, icon="CHECKMARK" if level == "INFO" else "ERROR")


def _draw_test_result(layout, record: core_printers.PrinterRecord) -> None:
    result = connections.state(record.id)
    if result is None:
        return
    row = layout.row()
    row.alert = result.level == "ERROR"
    row.label(text=result.text, icon="CHECKMARK" if result.level == "INFO" else ("TIME" if result.running else "ERROR"))


def _draw_tls(layout, record: core_printers.PrinterRecord) -> None:
    """The certificate decision a refused connection leaves for the user (trust it, or forget the old one)."""
    problem = printers.tls_problem(record.id)
    if problem is None:
        return
    col = layout.column(align=True)
    if problem.kind == "unknown":
        col.operator(f"{PACKAGE_ID}.printer_trust_cert", text="Review and trust certificate...",
                     icon="LOCKED").printer_id = record.id
    else:
        col.alert = True
        col.label(text="The device's certificate changed. Not connecting.", icon="ERROR")
        col.alert = False
        col.operator(f"{PACKAGE_ID}.printer_forget_pin", text="Forget trusted certificate...").printer_id = record.id


class SLICEWRIGHT_PT_send(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_send"
    bl_label = "Send"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME

    def draw(self, context):
        draw_send(self.layout, context)


classes = (SLICEWRIGHT_PT_send,)
