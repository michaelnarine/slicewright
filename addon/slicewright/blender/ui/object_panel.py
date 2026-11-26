# SPDX-License-Identifier: GPL-3.0-or-later
"""The "Slicewright object settings" panel in the "Slicer" tab (03 section 1.1): the active object's filament slot and
its per-object setting overrides. The drawing function only calls ``UILayout`` methods, so tests drive it with a
recording layout."""
from __future__ import annotations

import bpy

from ...names import PACKAGE_ID, TAB_NAME, panel_order
from .. import config_pg, object_settings, plate_collection
from ..labels import draw_wrapped
from . import choice


def draw_object_settings(layout, scene, ob) -> None:
    if ob is None or not hasattr(ob, PACKAGE_ID):
        layout.label(text="Select an object", icon="INFO")
        return
    props = ob.slicewright
    layout.label(text=ob.name, icon="OBJECT_DATA")
    if ob.name_full not in {o.name_full for o in plate_collection.plate_objects(scene)}:
        layout.label(text="Not on the Print plate: not sliced", icon="INFO")
    layout.prop(props, "filament")
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Overrides")
    row.operator(f"{PACKAGE_ID}.object_override_add", text="Add", icon="ADD")
    keys, orphans = object_settings.overridden_keys(ob), object_settings.orphan_keys(ob)
    if not keys and not orphans:
        box.label(text="None: this object uses the plate's settings")
    specs = config_pg.specs()
    for key in keys:                 # every override is shown, whatever the detail level: it changes the slice
        def remove(r, key=key):
            op = r.operator(f"{PACKAGE_ID}.object_override_remove", text="", icon="X")
            op.key = key
        if choice.style_of(props.overrides, key) == "segmented":
            choice.draw_segmented(box, props.overrides, key, specs[key].label, header=remove)
            continue
        row = box.row(align=True)
        row.prop(props.overrides, key, text=specs[key].label)
        remove(row)
    for key in orphans:
        row = box.row(align=True)
        row.alert = True
        draw_wrapped(row.column(align=True), f"{key}: not a setting of this engine, ignored", "ERROR", indent=6)
        op = row.operator(f"{PACKAGE_ID}.object_override_remove", text="", icon="X")
        op.key = key
    if len(keys) + len(orphans) > 1:
        op = box.operator(f"{PACKAGE_ID}.object_override_remove", text="Remove all", icon="TRASH")
        op.key = ""


class SLICEWRIGHT_PT_object(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_object"
    bl_label = "Object settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME
    bl_order = panel_order("object")
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return bool(config_pg.specs())

    def draw(self, context):
        draw_object_settings(self.layout, context.scene, context.active_object)


classes = (SLICEWRIGHT_PT_object,)
