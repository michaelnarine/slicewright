# SPDX-License-Identifier: GPL-3.0-or-later
"""The export operators (03 section 8.1): "Export G-code" and, later, "Export .gcode.3mf".

Both open a file dialog next to the .blend (``exporting.default_filepath``) and refuse unless there is a slice to
export. Two confirmations guard what a user would not expect:

* an out-of-date preview (the plate or settings changed since the slice): the file would hold the *older* slice, so a
  confirm popup says so before the dialog opens. The simple, unsurprising choice is to ask; the user answers by
  confirming or by slicing again (the Slice button is right above);
* an existing file: Blender's file dialog only reddens the name, it does not ask, so after the dialog the add-on asks
  itself (``export_confirm``) before replacing it.

Run from a script (``execute`` with a ``filepath``) the same rules apply without the popups: an out-of-date slice needs
``allow_stale=True`` and an existing file needs ``overwrite=True``, otherwise the call is cancelled with a warning.
"""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ...core import export as core_export
from ...core.export import ExportError
from ...names import PACKAGE_ID
from .. import exporting, staleness

STALE_TITLE = "Preview out of date"
STALE_MESSAGE = ("The plate or settings changed since this slice, so the file would hold the older slice. "
                 "Slice again first to export the current plate.")


class _ExportBase:
    """Shared by the per-format operators. Subclasses set ``bl_idname``, ``bl_label``, ``kind`` and
    ``filter_glob``'s default."""
    kind = exporting.GCODE
    op_id = ""                      # the operator's name under bpy.ops.slicewright (``self.bl_idname`` is not it)

    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    check_existing: BoolProperty(default=True, options={"HIDDEN"})
    allow_stale: BoolProperty(
        name="Export the out-of-date slice", default=False, options={"SKIP_SAVE"},
        description="Write the older slice that the preview still shows, although the plate or settings have changed")
    overwrite: BoolProperty(name="Replace an existing file", default=False, options={"SKIP_SAVE"},
                            description="Replace the file if it exists (otherwise the export is cancelled)")
    interactive: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})   # set by invoke: popups may be shown

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return getattr(scene, PACKAGE_ID, None) is not None and exporting.availability(scene, cls.kind)[0]

    @classmethod
    def description(cls, context, properties):
        scene = context.scene
        ok, why = exporting.availability(scene, cls.kind) if getattr(scene, PACKAGE_ID, None) else (False, "")
        return cls.bl_description + ("" if ok or not why else f"\n{why}")

    # -- the file dialog ----------------------------------------------------------------------------------------

    def invoke(self, context, event):
        scene = context.scene
        if staleness.settle(scene) and not self.allow_stale:
            bpy.ops.slicewright.export_confirm("INVOKE_DEFAULT", target=self.op_id, reason="STALE")
            return {"CANCELLED"}
        if not self.filepath:
            self.filepath = exporting.default_filepath(scene, self.kind)
        self.interactive = True
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def check(self, context):
        """The dialog's filename hook: add the extension if the typed name lacks it (never replacing a dotted name's tail)."""
        fixed = core_export.with_extension(self.filepath, self.kind)
        if fixed != self.filepath:
            self.filepath = fixed
            return True
        return False

    def draw(self, context):
        if context.scene.slicewright.stale:
            box = self.layout.box()
            box.alert = True
            box.label(text=STALE_TITLE, icon="ERROR")
            box.prop(self, "allow_stale")

    # -- the write ----------------------------------------------------------------------------------------------

    def execute(self, context):
        scene = context.scene
        if not exporting.availability(scene, self.kind)[0]:
            self.report({"WARNING"}, exporting.availability(scene, self.kind)[1] or "There is nothing to export")
            return {"CANCELLED"}
        if staleness.settle(scene) and not self.allow_stale:
            self.report({"WARNING"}, f"{STALE_TITLE}: {STALE_MESSAGE}")
            return {"CANCELLED"}
        dest = core_export.with_extension(self.filepath or exporting.default_filepath(scene, self.kind), self.kind)
        try:
            core_export.check_destination(dest, overwrite=self.overwrite)
        except ExportError as exc:
            if exc.exists and self.interactive and context.window is not None:
                bpy.ops.slicewright.export_confirm("INVOKE_DEFAULT", target=self.op_id, reason="OVERWRITE",
                                                   filepath=dest, allow_stale=self.allow_stale)
                return {"CANCELLED"}
            exporting.set_message(scene, "ERROR", f"{LABELS[self.kind]} not exported: {exc}")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        try:
            text = self.write(scene, dest)
        except ExportError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        if text:
            self.report({"INFO"}, text)
        return {"FINISHED"}

    def write(self, scene, dest: str) -> str:
        raise NotImplementedError


LABELS = exporting.LABEL


class SLICEWRIGHT_OT_export_gcode(_ExportBase, bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.export_gcode"
    bl_label = "Export G-code"
    bl_description = "Save the G-code of the last slice to a file"
    kind = exporting.GCODE
    op_id = "export_gcode"

    filter_glob: StringProperty(default="*.gcode;*.gco", options={"HIDDEN"})

    def write(self, scene, dest: str) -> str:
        return exporting.export_gcode(scene, dest, overwrite=self.overwrite)


class SLICEWRIGHT_OT_export_confirm(bpy.types.Operator):
    """The popups of the export operators: an out-of-date preview, a file that already exists."""
    bl_idname = f"{PACKAGE_ID}.export_confirm"
    bl_label = "Confirm Export"
    bl_options = {"INTERNAL"}

    target: StringProperty(options={"HIDDEN", "SKIP_SAVE"})          # name of the export operator: ``export_gcode``, ...
    reason: EnumProperty(items=[("STALE", "Out of date", ""), ("OVERWRITE", "Replace", "")],
                         options={"HIDDEN", "SKIP_SAVE"})
    filepath: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    allow_stale: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        if self.reason == "STALE":
            return context.window_manager.invoke_confirm(
                self, event, title=STALE_TITLE, message=STALE_MESSAGE, confirm_text="Export anyway", icon="ERROR")
        folder, name = os.path.split(self.filepath)
        return context.window_manager.invoke_confirm(
            self, event, title="Replace file?", message=f"{name} already exists in {folder}.",
            confirm_text="Replace", icon="ERROR")

    def execute(self, context):
        if self.target not in TARGETS:
            return {"CANCELLED"}
        op = getattr(getattr(bpy.ops, PACKAGE_ID), self.target)
        if self.reason == "STALE":
            op("INVOKE_DEFAULT", allow_stale=True)             # on to the file dialog
        else:
            op("EXEC_DEFAULT", filepath=self.filepath, allow_stale=self.allow_stale, overwrite=True)
        return {"FINISHED"}


TARGETS = ("export_gcode",)
classes = (SLICEWRIGHT_OT_export_gcode, SLICEWRIGHT_OT_export_confirm)
