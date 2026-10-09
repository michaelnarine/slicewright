# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences (03 section 9.1). More arrive with later milestones (printers, cache, modes)."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, IntProperty

from .blender import registry
from .blender.ui.diagnostics import draw_engine_status
from .core import logs
from .names import PACKAGE_ID

REQUIRES_ENGINE = False


def _package() -> str:
    """The preferences ``bl_idname`` must be the extension's package name."""
    return __package__ or PACKAGE_ID


def _on_log_level(self, _context) -> None:
    registry.apply_log_level(self.log_level)


class SLICEWRIGHT_AP_Preferences(bpy.types.AddonPreferences):
    bl_idname = _package()

    log_level: EnumProperty(
        name="Log level", description="Verbosity of the add-on and engine logs",
        items=[(k, k.title(), "") for k in logs.LEVELS], default="WARNING", update=_on_log_level)
    threads: IntProperty(
        name="Engine threads", min=0, max=256, default=0,
        description="Threads the engine may use while slicing; 0 uses all cores but one")

    preview_vram_mb: IntProperty(
        name="Preview VRAM budget (MB)", min=0, max=65536, default=0,
        description="GPU memory the toolpath preview may use; 0 picks 384 MB on Intel or small "
                    "Apple Silicon GPUs and 1024 MB otherwise")
    preview_lines_threshold: IntProperty(
        name="Lines above (segments)", min=100_000, max=1_000_000_000, default=12_000_000,
        description="Switch the preview from tubes to flat lines when more segments than this are visible")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "log_level")
        layout.prop(self, "threads")
        layout.prop(self, "preview_vram_mb")
        layout.prop(self, "preview_lines_threshold")
        box = layout.box()
        box.label(text="Engine")
        draw_engine_status(box, registry.state.status)
        box.operator(f"{PACKAGE_ID}.copy_diagnostics", icon="COPYDOWN")


classes = (SLICEWRIGHT_AP_Preferences,)


def register() -> None:
    registry.register_classes(classes)


def unregister() -> None:
    registry.unregister_classes(classes)


def current_log_level() -> str:
    """The saved log level, or the default when the add-on is not enabled in preferences."""
    try:
        return bpy.context.preferences.addons[_package()].preferences.log_level
    except (KeyError, AttributeError):
        return "WARNING"


def _preference(name: str, default):
    try:
        return getattr(bpy.context.preferences.addons[_package()].preferences, name)
    except (KeyError, AttributeError):
        return default


def preview_vram_mb() -> int:
    """The preview VRAM budget preference in MB (0: automatic)."""
    return int(_preference("preview_vram_mb", 0))


def preview_lines_threshold() -> int:
    return int(_preference("preview_lines_threshold", 12_000_000))
