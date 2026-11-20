# SPDX-License-Identifier: GPL-3.0-or-later
"""The Send operators (03 section 8): "Send to printer", Cancel, and the popups that guard it.

``send`` uploads the last slice to the printer chosen in the Send panel. Two confirmations guard what a user would not
expect, in the same style as the export operators:

* an out-of-date preview: the file would hold the *older* slice, so a popup says so first ("Send anyway");
* **starting a print**: when "Start print after upload" is ticked a popup names the printer and the file and says that a
  physical print will begin ("Upload and start print"). Nothing starts a print without it.

Run from a script, the same rules apply without popups: ``allow_stale=True`` for an out-of-date slice, and
``confirm_print=True`` for a print (otherwise the call is cancelled with a warning and nothing is sent). The network gate
(``online.network_allowed()``) greys the buttons with the reason as the tooltip.
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ...names import PACKAGE_ID
from .. import online, sending, staleness

STALE_TITLE = "Preview out of date"
STALE_MESSAGE = ("The plate or settings changed since this slice, so the file would hold the older slice. "
                 "Slice again first to send the current plate.")


def _scene_ok(context) -> bool:
    return getattr(context.scene, PACKAGE_ID, None) is not None


class SLICEWRIGHT_OT_send(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.send"
    bl_label = "Send to printer"
    bl_description = "Upload the last slice to the chosen printer. Nothing is sent until you press this"

    allow_stale: BoolProperty(options={"HIDDEN", "SKIP_SAVE"},
                              description="Send the older slice that the preview still shows")
    confirm_print: BoolProperty(options={"HIDDEN", "SKIP_SAVE"},
                                description="The user confirmed that the print should start after the upload")

    @classmethod
    def poll(cls, context):
        if not _scene_ok(context):
            return False
        ok, why = sending.availability(context.scene)
        if not ok and why:
            cls.poll_message_set(why)
        return ok

    def invoke(self, context, event):
        scene = context.scene
        if staleness.settle(scene) and not self.allow_stale:
            bpy.ops.slicewright.send_confirm("INVOKE_DEFAULT", reason="STALE")
            return {"CANCELLED"}
        send = sending.state(context)
        if send is not None and send.start_print and not self.confirm_print:
            bpy.ops.slicewright.send_confirm("INVOKE_DEFAULT", reason="PRINT", allow_stale=self.allow_stale)
            return {"CANCELLED"}
        return self.execute(context)

    def execute(self, context):
        scene = context.scene
        ok, why = sending.availability(scene)
        if not ok:
            self.report({"WARNING"}, why or "Nothing to send")
            return {"CANCELLED"}
        if staleness.settle(scene) and not self.allow_stale:
            self.report({"WARNING"}, f"{STALE_TITLE}: {STALE_MESSAGE}")
            return {"CANCELLED"}
        send = sending.state(context)
        if send.start_print and not self.confirm_print:
            self.report({"WARNING"}, "Starting a print needs your confirmation: nothing was sent")
            return {"CANCELLED"}
        try:
            text = sending.start(scene, allow_stale=self.allow_stale, confirmed_print=self.confirm_print)
        except sending.SendError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        last = sending.message(scene)
        if not sending.is_live() and last is not None and last[0] == "ERROR":      # failed before the upload began
            self.report({"WARNING"}, last[1])
            return {"CANCELLED"}
        self.report({"INFO"}, text)
        return {"FINISHED"}


class SLICEWRIGHT_OT_send_cancel(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.send_cancel"
    bl_label = "Cancel Send"
    bl_description = "Stop the upload. The printer keeps nothing of a cancelled upload"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return sending.is_live()

    def execute(self, context):
        sending.cancel()
        return {"FINISHED"}


class SLICEWRIGHT_OT_send_confirm(bpy.types.Operator):
    """The popups of the send operator: an out-of-date preview, and starting a print."""
    bl_idname = f"{PACKAGE_ID}.send_confirm"
    bl_label = "Confirm Send"
    bl_options = {"INTERNAL"}

    reason: EnumProperty(items=[("STALE", "Out of date", ""), ("PRINT", "Start print", "")],
                         options={"HIDDEN", "SKIP_SAVE"})
    allow_stale: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        if self.reason == "STALE":
            return context.window_manager.invoke_confirm(
                self, event, title=STALE_TITLE, message=STALE_MESSAGE, confirm_text="Send anyway", icon="ERROR")
        scene = context.scene
        record = sending.chosen(scene)
        fmt = sending.format_of(record)
        name = sending.filename_for(scene, fmt)
        who = record.name if record is not None else "the printer"
        return context.window_manager.invoke_confirm(
            self, event, title="Start printing?",
            message=f"Upload {name} to {who} and START PRINTING it. Make sure the printer is clear and ready.",
            confirm_text="Upload and start print", icon="ERROR")

    def execute(self, context):
        if self.reason == "STALE":
            send = sending.state(context)
            if send is not None and send.start_print:            # on to the print confirmation
                bpy.ops.slicewright.send("INVOKE_DEFAULT", allow_stale=True)
            else:
                bpy.ops.slicewright.send("EXEC_DEFAULT", allow_stale=True)
        else:
            bpy.ops.slicewright.send("EXEC_DEFAULT", allow_stale=self.allow_stale, confirm_print=True)
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_send, SLICEWRIGHT_OT_send_cancel, SLICEWRIGHT_OT_send_confirm)
