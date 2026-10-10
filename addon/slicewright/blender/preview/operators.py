# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview navigation: step the top or bottom layer or the in-layer move, with keys (03 section 7.4)."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

from ...core import preview_nav as nav
from ...names import PACKAGE_ID
from .. import registry
from . import props as preview_props
from . import runtime

STEP_FUNCS = {"TOP": nav.step_top_layer, "BOTTOM": nav.step_bottom_layer, "MOVE": nav.step_move}

# (key, modifiers, target, delta). Arrows are free in the 3D viewport; Ctrl moves the bottom layer.
KEYS = (("UP_ARROW", {}, "TOP", 1), ("DOWN_ARROW", {}, "TOP", -1),
        ("UP_ARROW", {"shift": True}, "TOP", 10), ("DOWN_ARROW", {"shift": True}, "TOP", -10),
        ("UP_ARROW", {"ctrl": True}, "BOTTOM", 1), ("DOWN_ARROW", {"ctrl": True}, "BOTTOM", -1),
        ("RIGHT_ARROW", {}, "MOVE", 1), ("LEFT_ARROW", {}, "MOVE", -1),
        ("RIGHT_ARROW", {"shift": True}, "MOVE", 100), ("LEFT_ARROW", {"shift": True}, "MOVE", -100))


class SLICEWRIGHT_OT_preview_step(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.preview_step"
    bl_label = "Step preview"
    bl_description = "Step the top layer, bottom layer or in-layer move of the toolpath preview"
    bl_options = {'INTERNAL'}

    target: EnumProperty(items=[("TOP", "Top layer", ""), ("BOTTOM", "Bottom layer", ""),
                                ("MOVE", "Move", "")], default="TOP")
    delta: IntProperty(default=1)

    @classmethod
    def poll(cls, context):
        return runtime.get(context.scene) is not None

    def execute(self, context):
        rt = runtime.get(context.scene)
        props = getattr(context.scene, preview_props.PROP_NAME)
        props.set_scrub(STEP_FUNCS[self.target](props.scrub(), rt.layers, self.delta))
        runtime.tag_redraw()
        return {'FINISHED'}


class SLICEWRIGHT_OT_preview_load_gcode(bpy.types.Operator):
    """Developer tool: preview a G-code file with the fake engine's ``from_gcode`` (blocks while parsing)."""
    bl_idname = f"{PACKAGE_ID}.preview_load_gcode"
    bl_label = "Preview G-code (fake engine)"
    bl_options = {'INTERNAL'}

    filepath: StringProperty(subtype='FILE_PATH')
    vram_mb: IntProperty(name="VRAM budget (MB)", default=0, min=0,
                         description="0 uses the preference or the GPU default")

    @classmethod
    def poll(cls, context):
        status = registry.state.status
        return status is not None and status.ok and hasattr(status.module, "from_gcode")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from . import handler
        result = registry.state.status.module.from_gcode(self.filepath)
        sw = getattr(context.scene, PACKAGE_ID, None)
        if sw is not None:
            sw.mode = 'PREVIEW'
        handler.show_result(context.scene, result, budget_mb=self.vram_mb or None)
        return {'FINISHED'}


classes = (SLICEWRIGHT_OT_preview_step, SLICEWRIGHT_OT_preview_load_gcode)
_keymaps: list = []


def register_keymap() -> None:
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:                       # background mode has no add-on keyconfig
        return
    km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
    for key, mods, target, delta in KEYS:
        kmi = km.keymap_items.new(f"{PACKAGE_ID}.preview_step", key, 'PRESS', **mods)
        kmi.properties.target, kmi.properties.delta = target, delta
        _keymaps.append((km, kmi))


def unregister_keymap() -> None:
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (RuntimeError, ReferenceError):
            pass
    _keymaps.clear()
