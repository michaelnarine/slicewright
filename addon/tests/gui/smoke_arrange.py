# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI smoke (plan M4 layer 7): Arrange with the fake engine, and one undo step restoring the positions.

    Blender --factory-startup --python addon/tests/gui/smoke_arrange.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

import bpy  # noqa: E402

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"


def steps():
    import slicewright
    sys.path.insert(0, os.path.join(g.TESTS_DIR, "blender"))
    import bl_common
    slicewright.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.slicewright.use_mm_scene()
    cubes = [bl_common.add_cube(f"Part{i}", s, (60 + 4 * i, 70 + 3 * i, s / 2))
             for i, s in enumerate((40, 30, 50, 25, 35, 45))]
    bpy.ops.object.select_all(action="DESELECT")
    for c in cubes:
        c.select_set(True)
    bpy.context.view_layer.objects.active = cubes[0]
    bpy.ops.slicewright.plate_add()
    bpy.ops.object.select_all(action="DESELECT")
    before = [tuple(c.location) for c in cubes]
    g.hide_ui_clutter()
    g.set_view((128, 128, 0), (45, 0, 20), 450)
    yield 0.5
    g.capture(os.path.join(g.out_dir(), "arrange_before.png"))
    win, area, region, _ = g.find_view3d()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.ed.undo_push(message="before arrange")
        assert bpy.ops.slicewright.arrange() == {"FINISHED"}
        bpy.ops.ed.undo_push(message="Arrange")      # the UI pushes this for a REGISTER|UNDO operator; scripts do not
    yield 0.5
    from slicewright.blender import volume
    assert not volume.compute(bpy.context), f"arranged parts must fit the bed: {volume.flagged}"
    g.capture(os.path.join(g.out_dir(), "arrange_after.png"))
    moved = [tuple(bpy.data.objects[f"Part{i}"].location) for i in range(6)]
    assert moved != before
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.ed.undo()
    yield 0.3
    restored = [tuple(bpy.data.objects[f"Part{i}"].location) for i in range(6)]
    assert all(abs(a - b) < 1e-4 for r, o in zip(restored, before) for a, b in zip(r, o)), "undo restores the layout"
    print("ARRANGE: six parts packed inside the bed; one undo step restored the stacked layout", flush=True)
    slicewright.unregister()
    yield 0.1


g.drive(steps)
