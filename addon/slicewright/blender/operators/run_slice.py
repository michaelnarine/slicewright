# SPDX-License-Identifier: GPL-3.0-or-later
"""The Slice and Cancel operators (03 sections 1.2 and 6.1).

``execute`` starts the job and returns (scripts, tests and the keymap use it); ``invoke`` additionally keeps a
pass-through modal watcher alive for as long as the job runs, so Esc or a right click cancels it. Nothing in the
watcher slices: the persistent timer in ``blender/slicing.py`` polls the runner.
"""
from __future__ import annotations

import bpy

from ...engine import jobs
from ...names import PACKAGE_ID
from .. import registry, slicing

CANCEL_KEYS = ("ESC", "RIGHTMOUSE")
WATCH_S = 0.25


def _engine_ok() -> bool:
    status = registry.state.status
    return status is not None and status.ok


class SLICEWRIGHT_OT_slice(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.slice"
    bl_label = "Slice"
    bl_description = ("Slice the Print plate. Starting a new slice cancels the running one. "
                      "Esc or a right click cancels while it runs")

    _timer = None

    @classmethod
    def poll(cls, context):
        return _engine_ok() and getattr(context.scene, PACKAGE_ID, None) is not None

    def execute(self, context):
        return self._report(slicing.start(context))

    def invoke(self, context, event):
        status = slicing.start(context)
        result = self._report(status)
        if status.active and context.window is not None:
            wm = context.window_manager
            self._timer = wm.event_timer_add(WATCH_S, window=context.window)
            wm.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        return result

    def modal(self, context, event):
        if not slicing.is_live():
            self._end(context)
            return {"FINISHED"}
        if event.type in CANCEL_KEYS and event.value == "PRESS":
            slicing.cancel()
        return {"PASS_THROUGH"}

    def cancel(self, context):
        self._end(context)

    def _end(self, context) -> None:
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _report(self, status: jobs.JobStatus):
        """A slice that failed before an engine job started (bad setting, empty plate, Busy) is CANCELLED with a
        warning. Never an ERROR report: that would make the operator raise for script callers."""
        if status.state == jobs.FAILED and status.finished:
            self.report({"WARNING"}, status.message)
            return {"CANCELLED"}
        return {"FINISHED"}


class SLICEWRIGHT_OT_slice_cancel(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.slice_cancel"
    bl_label = "Cancel"
    bl_description = "Cancel the running slice"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return slicing.is_live()

    def execute(self, context):
        slicing.cancel()
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_slice, SLICEWRIGHT_OT_slice_cancel)
