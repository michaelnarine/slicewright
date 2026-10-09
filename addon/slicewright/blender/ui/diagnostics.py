# SPDX-License-Identifier: GPL-3.0-or-later
"""Engine status panel in the 3D Viewport N-panel "Slicer" tab (03 sections 1.1 and 9.1)."""
from __future__ import annotations

import textwrap

import bpy

from ...engine.adapter import EngineStatus, fmt_api
from ...names import PACKAGE_ID, PRODUCT_NAME, TAB_NAME
from .. import registry


def draw_engine_status(layout: bpy.types.UILayout, status: EngineStatus | None) -> None:
    """Shared by the panel and the preferences. Shows both versions and the import error."""
    if status is None:
        layout.label(text="Engine not checked yet", icon="QUESTION")
        return
    col = layout.column(align=True)
    if status.ok:
        col.label(text=status.summary, icon="CHECKMARK")
        build = status.info.get("build", {})
        col.label(text=f"Orca {status.info.get('orca_tag', '?')}, {build.get('platform', '?')}")
        return
    col.alert = True
    col.label(text="Engine unavailable: slicing is disabled", icon="ERROR")
    col.alert = False
    col.label(text=f"Add-on needs engine API {fmt_api(status.required_api)}")
    col.label(text=f"Found engine API {fmt_api(status.found_api)}")
    for line in textwrap.wrap(status.error or "unknown error", 48):
        col.label(text=line)


class SLICEWRIGHT_PT_diagnostics(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_diagnostics"
    bl_label = f"{PRODUCT_NAME} engine"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME

    def draw(self, context):
        draw_engine_status(self.layout, registry.state.status)
        self.layout.operator(f"{PACKAGE_ID}.copy_diagnostics", icon="COPYDOWN")
