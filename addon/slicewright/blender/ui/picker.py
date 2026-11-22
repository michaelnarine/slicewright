# SPDX-License-Identifier: GPL-3.0-or-later
"""Printer, process and filament picker panel in the N-panel "Slicer" tab (03 sections 1.1 and 3.7)."""
from __future__ import annotations

import bpy

from ...core import preset_names
from ...names import PACKAGE_ID, TAB_NAME, panel_order
from ..labels import draw_wrapped
from .. import library


def draw_preset_button(layout, kind: str, index: int, preset_id: str, icon: str) -> None:
    """The picker of a process or filament preset: a button that shows the readable name (no ``sys:VENDOR/``) and opens
    a search popup; its tooltip has the whole name and the id."""
    text = preset_names.display_name(preset_id) if preset_id else "Choose..."
    op = layout.operator(f"{PACKAGE_ID}.pick_preset", text=text, icon=icon)
    op.kind, op.index = kind, index


class SLICEWRIGHT_PT_printer(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_printer"
    bl_label = "Printer and materials"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME
    bl_order = panel_order("printer")

    def draw(self, context):
        layout = self.layout
        lib = library.get()
        if lib is None:
            library.request()
            state, fraction, message = library.status()
            if state == "failed":
                col = layout.column(align=True)
                col.alert = True
                col.label(text="Could not load the printer library", icon="ERROR")
                col.alert = False
                col.label(text=message)
                layout.operator(f"{PACKAGE_ID}.load_library", icon="FILE_REFRESH")
            else:
                text = message or "Loading printer library…"
                if "%" not in text:
                    text = f"{text} {int(fraction * 100)}%"
                layout.progress(factor=fraction, type="BAR", text=text)
            return
        pg = context.scene.slicewright
        col = layout.column(align=True)
        col.prop(pg, "pick_vendor", text="Vendor", icon="COMMUNITY")
        col.prop(pg, "pick_model", text="Model", icon="MESH_CUBE")
        col.prop(pg, "pick_nozzle", text="Nozzle")
        if pg.printer_id:
            draw_wrapped(layout, preset_names.display_name(pg.printer_id), "CHECKMARK")
        else:
            layout.label(text="Choose a vendor and model", icon="INFO")
        layout.separator()
        layout.label(text="Process")                             # the label above, so the field has the full width
        draw_preset_button(layout, "process", -1, pg.process_id, "PRESET")
        layout.separator()
        header = layout.row()
        header.label(text="Filaments")
        header.operator(f"{PACKAGE_ID}.filament_add_defaults", text="Defaults", icon="PRESET")
        header.operator(f"{PACKAGE_ID}.filament_add", text="", icon="ADD")
        for i, slot in enumerate(pg.filaments):
            row = layout.row(align=True)
            draw_preset_button(row, "filament", i, slot.preset_id, "NONE")
            swatch = row.row(align=True)
            swatch.ui_units_x = 2.5
            swatch.prop(slot, "color", text="")
            remove = row.operator(f"{PACKAGE_ID}.filament_remove", text="", icon="X")
            remove.index = i
        layout.separator()
        layout.operator(f"{PACKAGE_ID}.import_presets", icon="IMPORT")
