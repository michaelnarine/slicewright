# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI smoke (plan M4 layer 5): the paint overlay for support, seam and filament paint.

    Blender --factory-startup --python addon/tests/gui/smoke_paint_overlay.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

import bmesh  # noqa: E402
import bpy  # noqa: E402

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"


def paint(ob, face_index, kind, value):
    """Select one face of ``ob`` in Edit Mode and assign through the add-on's operator."""
    bm = bmesh.from_edit_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(f.index == face_index)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)
    bpy.ops.slicewright.paint_assign(kind=kind, value=value)


def top_and_side(ob):
    """Face indices of the +Z face and the -Y face of a cube mesh."""
    me = ob.data
    top = max(me.polygons, key=lambda p: p.center.z).index
    front = min(me.polygons, key=lambda p: p.center.y).index
    return top, front


def steps():
    import slicewright
    from slicewright.blender.paint import overlay
    sys.path.insert(0, os.path.join(g.TESTS_DIR, "blender"))
    import bl_common
    slicewright.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.slicewright.use_mm_scene()
    cubes = [bl_common.add_cube(n, 40.0, (x, 110, 20)) for n, x in (("Support", 60), ("Seam", 128), ("Filament", 196))]
    bpy.ops.object.select_all(action="DESELECT")
    for c in cubes:
        c.select_set(True)
    bpy.context.view_layer.objects.active = cubes[0]
    bpy.ops.slicewright.plate_add()
    for ob, plan in ((cubes[0], (("SUPPORT", 1, "top"), ("SUPPORT", 2, "front"))),
                     (cubes[1], (("SEAM", 1, "top"), ("SEAM", 2, "front"))),
                     (cubes[2], (("FILAMENT", 1, "top"), ("FILAMENT", 2, "front")))):
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.tool_settings.mesh_select_mode = (False, False, True)
        top, front = top_and_side(ob)
        for kind, value, where in plan:
            paint(ob, top if where == "top" else front, kind, value)
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    g.hide_ui_clutter()
    g.set_view((128, 110, 20), (62, 0, 15), 330)
    yield 1.0
    img = g.capture(os.path.join(g.out_dir(), "paint_overlay.png"))
    assert len(overlay.entries) == 3, overlay.entries
    assert sum(e.triangle_count for e in overlay.entries) == 12, "six painted faces, two triangles each"
    # The overlay colours must actually reach the framebuffer: find a green-ish and a red-ish pixel.
    rgb = img[..., :3].astype(int)
    green = ((rgb[..., 1] > 120) & (rgb[..., 0] < 90) & (rgb[..., 2] < 110)).sum()
    red = ((rgb[..., 0] > 120) & (rgb[..., 1] < 110) & (rgb[..., 2] < 110) & (rgb[..., 0] > 1.5 * rgb[..., 1])).sum()
    assert green > 200 and red > 200, (green, red)
    slicewright.unregister()
    yield 0.1


g.drive(steps)
