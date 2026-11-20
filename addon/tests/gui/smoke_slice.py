# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI smoke (plan M6 orchestration layer 1): Slice through the real timer, Cancel, and a failed slice.

    Blender --factory-startup --python addon/tests/gui/smoke_slice.py -- <out_dir>

Unlike the headless tests this lets ``bpy.app.timers`` fire, so it proves the poll timer arms, advances the
job and stops itself, the status shows in the scene properties, and the result reaches the preview.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

import bpy  # noqa: E402

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"


def wait_for(predicate, what, timeout=30.0):
    """Yield short waits until ``predicate()``; raise on timeout."""
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {what}")
        yield 0.05


def steps():
    import slicewright
    from slicewright.blender import slicing
    from slicewright.blender.preview import runtime
    sys.path.insert(0, os.path.join(g.TESTS_DIR, "blender"))
    import bl_common
    slicewright.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.slicewright.use_mm_scene()
    scene = bpy.context.scene
    sw = scene.slicewright
    cube = bl_common.add_cube("Cube", 30.0, (100, 100, 15))
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    bpy.ops.slicewright.plate_add()
    g.hide_ui_clutter()
    g.set_view((100, 100, 10), (60, 0, 30), 260)

    # a full slice, started like the button does (INVOKE: the modal watcher runs while the job lives)
    assert bpy.ops.slicewright.slice("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
    assert sw.job_state in ("validating", "slicing"), sw.job_state
    assert bpy.app.timers.is_registered(slicing.poll_job)
    yield from wait_for(lambda: sw.job_state == "done", "the slice to finish")
    assert sw.mode == "PREVIEW" and len(sw.last_cache_key) == 32
    assert runtime.get(scene) is not None
    yield from wait_for(lambda: runtime.get(scene).controller.done, "the preview upload")
    yield from wait_for(lambda: not bpy.app.timers.is_registered(slicing.poll_job), "the poll timer to stop")
    g.capture(os.path.join(g.out_dir(), "sliced.png"))

    # cancel while running
    bpy.ops.slicewright.slice()
    yield 0.0
    bpy.ops.slicewright.slice_cancel()
    yield from wait_for(lambda: sw.job_state == "cancelled", "the cancel")
    assert sw.job_message == "Cancelled"

    # a failed slice shows the typed error
    cube.location.x = 5000.0
    bpy.context.view_layer.update()
    bpy.ops.slicewright.slice()
    yield from wait_for(lambda: sw.job_state == "failed", "the failure")
    assert sw.job_message.startswith("Validation failed:"), sw.job_message
    g.capture(os.path.join(g.out_dir(), "failed.png"))
    slicewright.unregister()
    yield 0.1


g.drive(steps)
