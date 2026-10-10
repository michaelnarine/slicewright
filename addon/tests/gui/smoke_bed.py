# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI smoke (plan M4 layer 1): the procedural bed with an exclude area, in a millimetre scene.

    Blender --factory-startup --python addon/tests/gui/smoke_bed.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

import bpy  # noqa: E402

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"


def steps():
    import slicewright
    slicewright.register()
    scene = bpy.context.scene
    props = scene.slicewright
    props.bed_exclude_area = "0x0,40x0,40x30,0x30"
    props.printable_height = 200.0
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.slicewright.use_mm_scene()
    g.hide_ui_clutter()
    g.set_view((128, 128, 0), (50, 0, 35), 520)
    yield 1.0
    img = g.capture(os.path.join(g.out_dir(), "bed.png"))
    assert img.std() > 1.0, "capture looks blank"
    from slicewright.blender import bed_draw
    assert bed_draw._cache["batches"], "the bed handler never built batches"
    slicewright.unregister()
    yield 0.1


g.drive(steps)
