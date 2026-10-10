# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI smoke (plan M4 layer 6): the Slicer Paint tool in a real viewport.

    Blender --factory-startup --python addon/tests/gui/smoke_paint_brush.py -- <out_dir>

Activates the WorkSpaceTool, then drags a stroke across a cube through ``tool.dab_at`` with the real
region and view matrices (the same per-event code the modal operator runs), checks the faces
painted, screenshots the overlay, and checks that one stroke is one undo step. ``Window.event_simulate``
did not deliver events in this environment, so the final click-through of the keymap is manual.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

import bmesh  # noqa: E402
import numpy as np  # noqa: E402
import bpy  # noqa: E402
from bpy_extras import view3d_utils  # noqa: E402

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"


def region_xy(region, rv3d, world):
    p = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
    return (p.x, p.y)


def steps():
    import slicewright
    from slicewright.blender.paint import attributes, brush, tool
    sys.path.insert(0, os.path.join(g.TESTS_DIR, "blender"))
    import bl_common
    slicewright.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.slicewright.use_mm_scene()
    ob = bl_common.add_cube("Brushable", 60.0, (128, 110, 30))
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=5, use_grid_fill=True)    # 6x6 faces per side
    bm.to_mesh(ob.data)
    bm.free()
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.slicewright.plate_add()
    bpy.ops.object.select_all(action="DESELECT")
    props = bpy.context.scene.slicewright
    props.brush_kind = "SUPPORT_BLOCK"
    props.brush_radius = 12.0
    props.brush_smart = True
    g.hide_ui_clutter()
    g.set_view((128, 110, 30), (62, 0, 20), 300)
    win, area, region, space = g.find_view3d()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.wm.tool_set_by_id(name=tool.TOOL_ID)
        assert bpy.ops.slicewright.paint_brush.poll(), "the brush operator must be available in Object Mode"
    active = win.workspace.tools.from_space_view3d_mode("OBJECT", create=False)
    assert active is not None and active.idname == tool.TOOL_ID, active and active.idname
    yield 0.5
    rv3d = space.region_3d
    scene = bpy.context.scene
    attr, value = tool.core_brush.brush_target(props.brush_kind, props.paint_filament)
    stroke = brush.Stroke(attr, value, props.brush_radius, smart=True, angle_deg=props.brush_angle)             # mm scene: 1 BU = 1 mm
    path = [(108, 92, 60), (122, 102, 60), (136, 112, 60), (150, 122, 60)]            # a diagonal drag over the top
    bpy.ops.ed.undo_push(message="before stroke")
    radius_px = None
    for world in path:
        radius_px = tool.dab_at(scene, stroke, region, rv3d, region_xy(region, rv3d, world))
        assert radius_px is not None, f"the ray under {world} missed the cube"
        yield 0.2
    stroke.end()
    bpy.ops.ed.undo_push(message="stroke")
    values = attributes.read(ob.data, "slicewright_support")
    painted = np.flatnonzero(values)
    assert len(painted) >= 8, f"expected a swathe of faces, got {len(painted)}"
    assert (values[painted] == 2).all(), "SUPPORT_BLOCK paints value 2"
    assert {ob.data.polygons[i].normal.z > 0.9 for i in painted} == {True}, "the drag stays on the top face"
    assert radius_px > 10, radius_px
    # the brush circle (screen radius from the projection) drawn the way the operator does, for the picture
    handle = bpy.types.SpaceView3D.draw_handler_add(circle_draw(region_xy(region, rv3d, path[-1]), radius_px),
                                                    (), "WINDOW", "POST_PIXEL")
    yield 0.3
    g.capture(os.path.join(g.out_dir(), "paint_brush.png"))
    bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
    n = len(painted)
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.ed.undo()
    yield 0.3
    after = attributes.read(bpy.data.objects["Brushable"].data, "slicewright_support")
    assert after is None or not after.any(), "undo should remove the whole stroke"
    print(f"BRUSH: {n} faces painted by one stroke; one undo step restored 0; circle radius {radius_px:.0f}px", flush=True)
    slicewright.unregister()
    yield 0.1


def circle_draw(center, radius):
    import math
    import gpu
    from gpu_extras.batch import batch_for_shader

    def draw():
        pts = [(center[0] + math.cos(t) * radius, center[1] + math.sin(t) * radius)
               for t in (i * math.tau / 48 for i in range(48))]
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINE_LOOP", {"pos": pts})
        shader.bind()
        shader.uniform_float("color", (1.0, 1.0, 1.0, 0.9))
        batch.draw(shader)
    return draw


g.drive(steps)
