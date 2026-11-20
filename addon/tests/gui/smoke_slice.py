# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI smoke (plan M6 orchestration layer 1): Slice through the real timer, Cancel, and a failed slice.

    Blender --factory-startup --python addon/tests/gui/smoke_slice.py -- <out_dir>

Unlike the headless tests this lets ``bpy.app.timers`` fire, so it proves the poll timer arms, advances the
job and stops itself, the status shows in the scene properties, and the result reaches the preview. It also
covers the result cache (03 section 6.4): the slice is written to disk by the tick timer, an identical slice is
served from it without an engine job, and reopening the saved file restores the preview without slicing.
"""
import os
import sys
import tempfile
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
    from slicewright.blender import slicing, staleness
    from slicewright.blender.preview import runtime
    sys.path.insert(0, os.path.join(g.TESTS_DIR, "blender"))
    import bl_common
    slicewright.register()
    from slicewright.blender import paths
    import fake_engine
    tmp = tempfile.mkdtemp(prefix="sw-smoke-")
    paths.set_override(os.path.join(tmp, "user"))              # the smoke never touches the real cache directory
    starts = []
    real_start = fake_engine.SliceJob.start
    fake_engine.SliceJob.start = lambda job: (starts.append(1), real_start(job))[1]
    thumbs = []
    real_thumbs = fake_engine.SliceJob.set_thumbnails
    fake_engine.SliceJob.set_thumbnails = lambda job, images: (thumbs.append(images), real_thumbs(job, images))[1]
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
    sw.printer_edits.thumbnails = "48x48/PNG,300x300/PNG"      # the real window has a GPU: these are rendered

    # a full slice, started like the button does (INVOKE: the modal watcher runs while the job lives)
    assert bpy.ops.slicewright.slice("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
    assert sw.job_state in ("validating", "slicing"), sw.job_state
    assert bpy.app.timers.is_registered(slicing.poll_job)
    yield from wait_for(lambda: sw.job_state == "done", "the slice to finish")
    assert sw.mode == "PREVIEW" and len(sw.last_cache_key) == 32
    assert [(i.shape, i.dtype.name) for i in thumbs[0]] == [((48, 48, 4), "uint8"), ((300, 300, 4), "uint8")], thumbs
    assert max(int(i[..., 3].max()) for i in thumbs[0]) == 255            # drawn, not blank
    from slicewright.blender import registry
    assert not [i for i in registry.state.plate_issues if i["code"] in ("thumbnail_missing", "thumbnails_skipped")]
    assert runtime.get(scene) is not None
    yield from wait_for(lambda: runtime.get(scene).controller.done, "the preview upload")
    yield from wait_for(lambda: not bpy.app.timers.is_registered(slicing.poll_job), "the poll timer to stop")
    g.capture(os.path.join(g.out_dir(), "sliced.png"))
    key = sw.last_cache_key
    assert len(starts) == 1

    # the result cache: written by the tick timer, then an identical slice never reaches the engine
    yield from wait_for(lambda: slicing.result_cache().has(key), "the slice to be written to the cache")
    assert bpy.ops.slicewright.slice() == {"FINISHED"}
    assert (sw.job_state, sw.job_message) == ("done", "Done (from cache)"), (sw.job_state, sw.job_message)
    yield from wait_for(lambda: getattr(runtime.get(scene).result, "from_cache", False)
                        and runtime.get(scene).controller.done, "the cached preview to load")
    assert len(starts) == 1
    g.capture(os.path.join(g.out_dir(), "from_cache.png"))

    # reopening the saved file restores the preview from the cache, without slicing
    blend = os.path.join(tmp, "plate.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)
    scene = bpy.context.scene
    sw = scene.slicewright
    assert sw.last_cache_key == key and sw.job_state == "idle"
    yield from wait_for(lambda: runtime.get(scene) is not None and runtime.get(scene).controller.done,
                        "the preview to be restored after the reload")
    assert (sw.job_state, sw.job_message, len(starts)) == ("done", "Restored from cache", 1)
    yield 0.3
    assert not sw.stale
    g.capture(os.path.join(g.out_dir(), "restored.png"))
    cube = bpy.data.objects["Cube"]

    # a slice result goes stale when the plate changes, and fresh again when it changes back (real timers)
    assert not sw.stale
    cube.location.x += 10.0
    yield from wait_for(lambda: sw.stale, "the preview to be marked out of date")
    g.capture(os.path.join(g.out_dir(), "stale.png"))
    cube.location.x -= 10.0
    yield from wait_for(lambda: not sw.stale, "the preview to be fresh again")
    sw.process_edits.layer_height = 0.3
    yield from wait_for(lambda: sw.stale, "a setting change to mark the preview out of date")
    sw.process_edits.layer_height = 0.2
    yield from wait_for(lambda: not sw.stale, "the setting change to be undone")
    assert not bpy.app.timers.is_registered(staleness._tick)

    # cancel while running (a different plate, so the cache cannot answer it)
    sw.process_edits.layer_height = 0.25
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

    # the Issues list shows the validation error and a click selects its object (the panel draws for real here)
    rows = bpy.context.window_manager.slicewright_issues
    assert [(r.level, r.code, r.object_name) for r in rows] == [("error", "object_outside_bed", "Cube")], list(rows)
    bpy.ops.object.select_all(action="DESELECT")
    assert bpy.ops.slicewright.issue_goto(index=0) == {"FINISHED"}
    assert [o.name for o in bpy.context.selected_objects] == ["Cube"]
    g.capture(os.path.join(g.out_dir(), "issues.png"))
    bpy.ops.slicewright.slice()                                  # a new slice starts with an empty list
    assert len(rows) == 0
    slicewright.unregister()
    yield 0.1


g.drive(steps)
