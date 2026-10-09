# SPDX-License-Identifier: GPL-3.0-or-later
"""``slicewright.copy_diagnostics``: copy versions, OS, GPU backend and log tail (03 section 9.4)."""
from __future__ import annotations

import bpy

from ...core import logs
from ...engine import adapter
from ...names import PACKAGE_ID
from .. import registry


def gather_text() -> str:
    """Diagnostics text for the current session. Works without an engine."""
    status = registry.state.status or adapter.load()
    extras = {"Blender": bpy.app.version_string, "Extension package": __package__ or ""}
    try:
        import gpu
        extras["GPU backend"] = gpu.platform.backend_type_get()
        extras["GPU"] = f"{gpu.platform.vendor_get()} {gpu.platform.renderer_get()}"
    except Exception:  # noqa: BLE001 - no GPU context in background mode
        extras["GPU backend"] = "unavailable"
    return adapter.diagnostics_text(status, extras, logs.tail(registry.state.log_dir))


class SLICEWRIGHT_OT_copy_diagnostics(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.copy_diagnostics"
    bl_label = "Copy Diagnostics"
    bl_description = "Copy version, system and log information to the clipboard for bug reports"

    def execute(self, context):
        context.window_manager.clipboard = gather_text()
        self.report({"INFO"}, "Diagnostics copied to the clipboard")
        return {"FINISHED"}
