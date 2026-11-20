# SPDX-License-Identifier: GPL-3.0-or-later
"""The property group of one physical printer, stored in the add-on preferences (03 section 8.5).

No property here holds a secret, and none can: the access code, API key or password is typed into a dialog operator
(``printers.SLICEWRIGHT_OT_printer_secret``), goes straight to the secret store and is cleared from that operator's own
property at once. Registered by ``prefs.py`` before the preferences class that holds the collection.
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from ..core import printers as core


def _search_presets(self, _context, text):
    from . import picker
    return picker.search_printers(text)


class SLICEWRIGHT_PG_Printer(bpy.types.PropertyGroup):
    id: StringProperty(options={"HIDDEN"})
    name: StringProperty(name="Name", default="Printer", maxlen=core.MAX_NAME,
                         description="How this printer is named in the add-on")
    kind: EnumProperty(name="Kind", items=[(k, core.KIND_LABELS[k], "") for k in core.KINDS], default=core.OCTOPRINT)
    host: StringProperty(name="Host", maxlen=core.MAX_HOST,
                         description="Host name or IP address of the printer, without http:// or a port")
    port: IntProperty(name="Port", min=0, max=65535, default=0,
                      description="0 uses the default for the kind (80, 7125 for Moonraker; 443 with HTTPS)")
    use_https: BoolProperty(name="HTTPS", default=False, description="Talk to the printer over HTTPS")
    tls_mode: EnumProperty(
        name="Certificate", default="verify",
        items=[("verify", "Verify", "Check the certificate like a web browser"),
               ("pin", "Trust this device's certificate",
                "For a self-signed certificate: remember the certificate's fingerprint the first time and require it "
                "from then on")])
    base_path: StringProperty(name="Base path", maxlen=core.MAX_OPTION_VALUE,
                              description="Path prefix when the printer sits behind a reverse proxy (starts with /)")
    serial: StringProperty(name="Serial number", maxlen=32, description="The printer's serial number (letters and digits)")
    preset: StringProperty(name="Printer preset", search=_search_presets,
                           description="The Slicewright printer preset this machine goes with")
    secret_ref: StringProperty(options={"HIDDEN"})
    secret_saved: BoolProperty(options={"HIDDEN"}, default=False,
                               description="A secret has been saved for this printer (the secret is not stored here)")


def to_record(pg) -> core.PrinterRecord:
    options: dict = {}
    if pg.kind in (core.OCTOPRINT, core.MOONRAKER):
        if pg.use_https:
            options["https"] = True
            if pg.tls_mode != "verify":
                options["tls"] = pg.tls_mode
        if pg.base_path.strip():
            options["base_path"] = pg.base_path.strip()
    elif pg.serial.strip():
        options["serial"] = pg.serial.strip().upper()
    return core.PrinterRecord(id=pg.id, name=pg.name, kind=pg.kind, host=pg.host.strip(), port=pg.port,
                              options=options, preset=pg.preset, secret_ref=pg.secret_ref,
                              secret_saved=pg.secret_saved)


def fill(pg, record: core.PrinterRecord) -> None:
    """Copy a record into a property group (adding a printer, tests)."""
    pg.id, pg.name, pg.kind, pg.host, pg.port = record.id, record.name, record.kind, record.host, record.port
    pg.use_https = bool(record.options.get("https"))
    pg.tls_mode = record.options.get("tls", "verify")
    pg.base_path = record.options.get("base_path", "")
    pg.serial = record.options.get("serial", "")
    pg.preset, pg.secret_ref, pg.secret_saved = record.preset, record.secret_ref, record.secret_saved


classes = (SLICEWRIGHT_PG_Printer,)
