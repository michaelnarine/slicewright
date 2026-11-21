# SPDX-License-Identifier: GPL-3.0-or-later
"""The bake operators (03 section 7.11): "Bake", its Cancel, and "Keep baked objects".

``bake`` asks before anything a user would not expect: a bake large enough to strain memory, and replacing the objects of an
earlier bake (only objects the add-on itself tagged; never anything else). Both questions are one popup. A script calling
``execute`` skips the popup and must say yes with ``allow_large`` / ``replace``, otherwise the call is cancelled with a warning.
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty

from ...names import PACKAGE_ID
from .. import bake_pg, baking


class SLICEWRIGHT_OT_bake(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.bake"
    bl_label = "Bake"
    bl_description = "Bake the slice into plain Blender curves or a mesh, with per-point attributes for Geometry Nodes"

    allow_large: BoolProperty(name="Allow a large bake", default=False, options={"SKIP_SAVE"},
                              description="Bake even when the estimate is above the size that asks first")
    replace: BoolProperty(name="Replace the previous bake", default=False, options={"SKIP_SAVE"},
                          description="Replace the objects of an earlier bake of the same source (only those "
                                      "this add-on made); otherwise the bake is cancelled")

    @classmethod
    def poll(cls, context):
        return bake_pg.of(context) is not None and baking.availability(context.scene)[0]

    @classmethod
    def description(cls, context, properties):
        ok, why = baking.availability(context.scene) if bake_pg.of(context) is not None else (False, "")
        return cls.bl_description + ("" if ok or not why else f"\n{why}")

    def _plan(self, context):
        try:
            return baking.make_plan(context.scene, bake_pg.of(context))
        except baking.BakeError as exc:
            self.report({"WARNING"}, str(exc))
            baking.set_message(context.scene, "ERROR", str(exc))
            return None

    def invoke(self, context, event):
        plan = self._plan(context)
        if plan is None:
            return {"CANCELLED"}
        if plan.estimate.level == "refuse":
            return self.refuse(context, plan)
        ask = baking.confirm_text(plan)
        if ask is None:
            return self.execute(context)
        self.allow_large, self.replace = True, True          # applies only if the user confirms
        return context.window_manager.invoke_confirm(self, event, title="Bake toolpaths", message=ask[0],
                                                     confirm_text=ask[1], icon="WARNING")

    def refuse(self, context, plan):
        from ...core import bake_plan as bp
        text = bp.verdict_text(plan.estimate)
        self.report({"WARNING"}, text)
        baking.set_message(context.scene, "ERROR", text)
        return {"CANCELLED"}

    def execute(self, context):
        scene = context.scene
        ok, why = baking.availability(scene)
        if not ok:
            self.report({"WARNING"}, why or "Nothing to bake")
            return {"CANCELLED"}
        plan = self._plan(context)
        if plan is None:
            return {"CANCELLED"}
        if plan.estimate.level == "refuse":
            return self.refuse(context, plan)
        if plan.estimate.level == "confirm" and not self.allow_large:
            self.report({"WARNING"}, "Large bake: pass allow_large=True to go ahead")
            return {"CANCELLED"}
        if plan.existing and not self.replace:
            self.report({"WARNING"}, f"{len(plan.existing)} object(s) of the previous bake would be replaced: "
                                     "pass replace=True to go ahead")
            return {"CANCELLED"}
        baking.start(scene, plan)
        return {"FINISHED"}


class SLICEWRIGHT_OT_bake_cancel(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.bake_cancel"
    bl_label = "Cancel Bake"
    bl_description = "Stop the bake; nothing is added to the scene"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return baking.is_live()

    def execute(self, context):
        baking.cancel()
        return {"FINISHED"}


class SLICEWRIGHT_OT_bake_keep(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.bake_keep"
    bl_label = "Keep Baked Objects"
    bl_description = ("Make the baked objects yours: the add-on forgets it made them, so baking again adds new objects "
                      "instead of replacing these")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from .. import bake_data
        return not baking.is_live() and bool(bake_data.tagged_objects(context.scene.name))

    def execute(self, context):
        n = baking.keep(context.scene)
        self.report({"INFO"}, f"{n} baked object{'s' if n != 1 else ''} kept")
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_bake, SLICEWRIGHT_OT_bake_cancel, SLICEWRIGHT_OT_bake_keep)
