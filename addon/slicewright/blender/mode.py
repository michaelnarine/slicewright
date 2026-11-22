# SPDX-License-Identifier: GPL-3.0-or-later
"""The Prepare / Preview switch (03 section 1.1): two operators, the button row, the viewport header entry.

``Scene.slicewright.mode`` decides what the viewport shows: PREPARE draws the plate's own overlays (live out-of-volume
outlines, the paint overlay) and lets Arrange and painting run; PREVIEW draws the toolpaths of the last slice. A slice
that finishes switches to Preview (``slicing._finish``); nothing else switches by itself, so editing the plate after a
slice only marks the preview out of date. Going back to Prepare is the user's choice, from the Slicer tab or the
viewport header, and so is going to Preview again (possible while the scene holds a slice result). The rules are in
``core/mode_rules.py``. Operators that only make sense in Prepare call :func:`require_prepare` from their ``poll`` so they
grey out with a reason instead of doing something nobody can see.

This module is a registration stage: it owns the two operators and the entry appended to ``VIEW3D_HT_header``.
"""
from __future__ import annotations

import bpy

from ..core import mode_rules as rules
from ..names import PACKAGE_ID
from . import registry
from .preview import runtime

REQUIRES_ENGINE = True


def has_preview(scene) -> bool:
    """A slice result is held for ``scene`` (shown, or still being loaded into the preview)."""
    return runtime.get(scene) is not None


def set_mode(scene, target: str) -> str | None:
    """Switch ``scene`` to ``target``. Returns why it cannot be done, or ``None`` when it was done (or already so)."""
    problem = rules.switch_problem(target, has_preview(scene))
    if problem is not None:
        return problem
    sw = scene.slicewright
    if sw.mode != target:
        sw.mode = target                       # ``mode_changed`` does the housekeeping, also for script writes
    return None


def mode_changed(sw) -> None:
    """``Scene.slicewright.mode`` changed (its update callback). Back in Prepare the plate overlays are stale: they
    are not computed in Preview, so rebuild them now rather than at the next edit. Redraws every viewport."""
    scene = sw.id_data
    if sw.mode == rules.PREPARE:
        from . import volume
        from .paint import overlay
        overlay.mark_dirty()
        volume.mark_dirty()
        if getattr(bpy.context, "scene", None) == scene:
            volume.refresh()
    runtime.tag_redraw()


def require_prepare(cls, context) -> bool:
    """For an operator's ``poll``: true in Prepare; in Preview false, with "Switch to Prepare" as the tooltip reason."""
    if context.scene.slicewright.mode == rules.PREPARE:
        return True
    cls.poll_message_set(rules.PREPARE_ONLY_REASON)
    return False


class _SwitchOperator(bpy.types.Operator):
    """Base of the two switch operators (not registered itself)."""
    bl_options = {"INTERNAL"}
    target = ""

    @classmethod
    def poll(cls, context):
        problem = rules.switch_problem(cls.target, has_preview(context.scene))
        if problem is not None:
            cls.poll_message_set(problem)
            return False
        return True

    def execute(self, context):
        problem = set_mode(context.scene, self.target)
        if problem is not None:
            self.report({"WARNING"}, problem)
            return {"CANCELLED"}
        return {"FINISHED"}


class SLICEWRIGHT_OT_mode_prepare(_SwitchOperator):
    bl_idname = f"{PACKAGE_ID}.mode_prepare"
    bl_label = "Prepare"
    bl_description = "Arrange and paint the plate. The slice stays available to come back to"
    target = rules.PREPARE


class SLICEWRIGHT_OT_mode_preview(_SwitchOperator):
    bl_idname = f"{PACKAGE_ID}.mode_preview"
    bl_label = "Preview"
    bl_description = "Inspect the toolpaths of the last slice"
    target = rules.PREVIEW


def draw_switch(layout, scene) -> None:
    """The ``[Prepare | Preview]`` button row. Preview greys out (tooltip: why) while there is no slice result."""
    mode = scene.slicewright.mode
    row = layout.row(align=True)
    row.operator(f"{PACKAGE_ID}.mode_prepare", text="Prepare", depress=mode == rules.PREPARE)
    row.operator(f"{PACKAGE_ID}.mode_preview", text="Preview", depress=mode == rules.PREVIEW)


def draw_header(self, context) -> None:
    """Appended to ``VIEW3D_HT_header``. Never raises into the viewport header."""
    if getattr(context.scene, PACKAGE_ID, None) is None:
        return
    self.layout.separator()
    draw_switch(self.layout, context.scene)


classes = (SLICEWRIGHT_OT_mode_prepare, SLICEWRIGHT_OT_mode_preview)


def header_installed() -> bool:
    funcs = getattr(bpy.types.VIEW3D_HT_header.draw, "_draw_funcs", ())
    return draw_header in funcs


def install_header() -> None:
    """Append the switch to the viewport header (once)."""
    if not header_installed():
        bpy.types.VIEW3D_HT_header.append(draw_header)


def register() -> None:
    registry.register_classes(classes)
    install_header()


def unregister() -> None:
    while header_installed():
        bpy.types.VIEW3D_HT_header.remove(draw_header)
    registry.unregister_classes(classes)
