# SPDX-License-Identifier: GPL-3.0-or-later
"""The Send panel's choices (03 section 8): which printer, which file, the name, and whether to start the print.

Stored on the **window manager**, not on the scene: a window manager's custom properties are not written to the .blend, so
"Start print after upload" can never come back switched on from a file, and a printer id from another machine's
preferences never ends up in a shared scene. The choices last for the session.

No ``from __future__ import annotations`` here: Blender evaluates stringified property annotations without this module's
globals (see ``props.py``).
"""
import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ..core import send as core_send


def _search_printers(self, context, text):
    from . import sending
    names = [r.name for r in sending.eligible(context.scene, show_all=self.show_all)]
    return [n for n in names if text.lower() in n.lower()]


class SLICEWRIGHT_PG_Send(bpy.types.PropertyGroup):
    printer: StringProperty(
        name="Printer", search=_search_printers, maxlen=64,
        description="The printer to send to (its name in the add-on preferences)")
    show_all: BoolProperty(
        name="All printers", default=False,
        description="Also offer printers that are set up for a different printer preset")
    file_format: EnumProperty(
        name="File", default=core_send.AUTO,
        items=[(core_send.AUTO, "Printer default", "The file this kind of printer takes: plain G-code"),
               (core_send.GCODE, "G-code", "A plain G-code file"),
               (core_send.THREEMF, ".gcode.3mf", "A Bambu-style package: G-code, plate data and thumbnail")])
    filename: StringProperty(
        name="Name", maxlen=255, description="The file name on the printer; empty uses the name the slice came with")
    subfolder: StringProperty(
        name="Folder", maxlen=255, description="A folder on the printer to put the file in (optional, e.g. cases/new)")
    start_print: BoolProperty(
        name="Start print after upload", default=False,
        description="Ask the printer to start printing the file as soon as it is stored. You are asked to confirm")


classes = (SLICEWRIGHT_PG_Send,)
WM_PROP = "slicewright_send"
