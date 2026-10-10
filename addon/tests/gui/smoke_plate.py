# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI smoke (plan M4 layer 2): plate objects, Drop to bed and a red outline on an out-of-volume part.

    Blender --factory-startup --python addon/tests/gui/smoke_plate.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

import bpy  # noqa: E402

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"


def steps():
    import slicewright
    from slicewright.blender import volume
    sys.path.insert(0, os.path.join(g.TESTS_DIR, "blender"))
    import bl_common
    slicewright.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.slicewright.use_mm_scene()
    props = bpy.context.scene.slicewright
    props.bed_exclude_area = "0x0,40x0,40x30,0x30"
    a = bl_common.add_cube("Block", 40.0, (90, 100, 60))          # floating until dropped
    b = bl_common.add_cube("Box", 30.0, (200, 140, 15))
    c = bl_common.add_cube("Overhanging", 40.0, (250, 60, 20))     # sticks out over the bed edge
    bpy.ops.object.select_all(action="DESELECT")
    for o in (a, b, c):
        o.select_set(True)
    bpy.ops.slicewright.plate_add()
    bpy.ops.slicewright.drop_to_bed()
    assert set(volume.flagged) == {"Overhanging"}, volume.flagged
    bpy.ops.object.select_all(action="DESELECT")
    g.hide_ui_clutter()
    g.set_view((128, 110, 0), (55, 0, 30), 520)
    yield 1.0
    g.capture(os.path.join(g.out_dir(), "plate.png"))
    slicewright.unregister()
    yield 0.1


g.drive(steps)
