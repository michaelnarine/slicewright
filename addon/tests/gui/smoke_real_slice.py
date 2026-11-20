# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI smoke (M8): the Slice button through the REAL engine, in a real window.

    Blender --factory-startup --python addon/tests/gui/smoke_real_slice.py -- <out_dir>

The engine is found through ``SLICEWRIGHT_ENGINE_PATH`` or ``engine/build/engine/python``. Like ``smoke_slice.py`` it
lets the timers fire (poll timer, tick runner, staleness) with the real Bambu Lab A1 profile: slice a cube, the real
G-code with the rendered plate thumbnails, the preview upload, the result cache (an identical slice never reaches the
engine; reopening the file restores the preview), staleness, cancel, a failed slice and a two-object two-filament plate.
Screenshots go to ``<out_dir>``.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

import bpy  # noqa: E402

BUILD = os.path.join(g.TESTS_DIR, os.pardir, os.pardir, "engine", "build", "engine", "python")
if not os.environ.get("SLICEWRIGHT_ENGINE_PATH") and os.path.isdir(os.path.join(BUILD, "slicewright_engine")):
    os.environ["SLICEWRIGHT_ENGINE_PATH"] = os.path.abspath(BUILD)
os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "slicewright_engine"


def wait_for(predicate, what, timeout=60.0):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {what}")
        yield 0.05


class RecordingJob:
    """A ``SliceJob`` that remembers the thumbnail images it was given."""

    def __init__(self, job, images):
        self._job, self._images = job, images

    def __getattr__(self, name):
        return getattr(self._job, name)

    def set_thumbnails(self, images):
        self._images.append(images)
        return self._job.set_thumbnails(images)


class CountingEngine:
    """The engine module as the runner sees it, counting the jobs it creates (a cache hit must create none)."""

    def __init__(self, sc):
        self._sc, self.jobs, self.thumbs = sc, 0, []

    def __getattr__(self, name):
        return getattr(self._sc, name)

    def SliceJob(self):                                                   # noqa: N802 - the module's name
        self.jobs += 1
        return RecordingJob(self._sc.SliceJob(), self.thumbs)


def steps():
    import slicewright
    from slicewright.blender import library, picker, registry, slicing, staleness, timers
    from slicewright.blender.preview import runtime
    sys.path.insert(0, os.path.join(g.TESTS_DIR, "blender"))
    import bl_common
    slicewright.register()
    from slicewright.blender import paths
    assert registry.state.status.ok and registry.state.status.module_name == "slicewright_engine", registry.state.status
    tmp = tempfile.mkdtemp(prefix="sw-real-smoke-")
    paths.set_override(os.path.join(tmp, "user"))              # never touches the real cache directory
    counting = CountingEngine(registry.state.status.module)
    slicing.runner().sc = counting
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.slicewright.use_mm_scene()
    scene = bpy.context.scene
    sw = scene.slicewright
    library.request()
    yield from wait_for(lambda: library.status()[0] == "ready", "the profile library")
    printer = next(e for e in library.get().printers() if e.name == "Bambu Lab A1 0.4 nozzle")
    picker.select_printer(sw, printer)
    cube = bl_common.add_cube("Cube", 30.0, (100, 100, 15))
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    bpy.ops.slicewright.plate_add()
    g.hide_ui_clutter()
    g.set_view((100, 100, 10), (60, 0, 30), 260)
    sw.printer_edits.thumbnails = "48x48/PNG,300x300/PNG"      # the A1 profile has none (Bambu gets them in the 3mf)

    # a full slice, started like the button does
    assert bpy.ops.slicewright.slice("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
    assert sw.job_state in ("validating", "slicing"), sw.job_state
    yield from wait_for(lambda: sw.job_state in ("done", "failed"), "the slice to finish")
    assert sw.job_state == "done", (sw.job_state, sw.job_message)
    assert sw.mode == "PREVIEW" and len(sw.last_cache_key) == 32
    rt = runtime.get(scene)
    assert rt is not None and len(rt.layers["z"]) == 150, len(rt.layers["z"])      # 30 mm at 0.2 mm
    # Bambu printers get the plate thumbnails inside the .gcode.3mf only (04 4.1), so the G-code has none: the check
    # is that the GPU render happened and reached set_thumbnails (the embedding is tested with a Marlin printer)
    assert [(i.shape, i.dtype.name) for i in counting.thumbs[0]] == [((48, 48, 4), "uint8"), ((300, 300, 4), "uint8")]
    assert max(int(i[..., 3].max()) for i in counting.thumbs[0]) == 255            # drawn, not blank
    assert not [i for i in registry.state.plate_issues if i["code"] in ("thumbnail_missing", "thumbnails_skipped")]
    yield from wait_for(lambda: runtime.get(scene).controller.done, "the preview upload")
    yield from wait_for(lambda: not bpy.app.timers.is_registered(slicing.poll_job), "the poll timer to stop")
    g.capture(os.path.join(g.out_dir(), "real_sliced.png"))
    key = sw.last_cache_key
    assert counting.jobs == 1
    print("[slw] stats:", {k: rt.result.stats[k] for k in ("layer_count", "time_s", "threads")}, flush=True)

    # the result cache
    yield from wait_for(lambda: slicing.result_cache().has(key), "the slice to be written to the cache")
    assert bpy.ops.slicewright.slice() == {"FINISHED"}
    assert (sw.job_state, sw.job_message) == ("done", "Done (from cache)"), (sw.job_state, sw.job_message)
    yield from wait_for(lambda: getattr(runtime.get(scene).result, "from_cache", False)
                        and runtime.get(scene).controller.done, "the cached preview to load")
    assert counting.jobs == 1
    g.capture(os.path.join(g.out_dir(), "real_from_cache.png"))

    # reopening the saved file restores the preview without slicing
    blend = os.path.join(tmp, "plate.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)
    scene = bpy.context.scene
    sw = scene.slicewright
    assert sw.last_cache_key == key and sw.job_state == "idle"
    yield from wait_for(lambda: runtime.get(scene) is not None and runtime.get(scene).controller.done,
                        "the preview to be restored after the reload")
    assert (sw.job_state, sw.job_message, counting.jobs) == ("done", "Restored from cache", 1)
    yield 0.3
    assert not sw.stale
    cube = bpy.data.objects["Cube"]

    # staleness with the real config
    cube.location.x += 10.0
    yield from wait_for(lambda: sw.stale, "the preview to be marked out of date")
    cube.location.x -= 10.0
    yield from wait_for(lambda: not sw.stale, "the preview to be fresh again")
    sw.process_edits.layer_height = 0.3
    yield from wait_for(lambda: sw.stale, "a setting change to mark the preview out of date")
    sw.process_edits.layer_height = 0.2
    yield from wait_for(lambda: not sw.stale, "the setting change to be undone")

    # cancel while running (a taller part, so it is still running; a different plate than the cached one)
    cube.scale = (1.0, 1.0, 6.0)
    cube.location.z = 15.0 * 6
    bpy.context.view_layer.update()
    bpy.ops.slicewright.slice()
    yield 0.05
    bpy.ops.slicewright.slice_cancel()
    yield from wait_for(lambda: sw.job_state in ("cancelled", "done"), "the cancel")
    print("[slw] cancel ended as", sw.job_state, flush=True)
    cube.scale = (1.0, 1.0, 1.0)
    cube.location.z = 15.0

    # two objects on two filaments: the wipe tower and the filament colours (the A1 starts with one slot)
    picker.add_filament(sw, picker.missing_default_materials(sw)[0])
    other = bl_common.add_cube("Cube2", 30.0, (170, 100, 15))
    bpy.ops.object.select_all(action="DESELECT")
    other.select_set(True)
    bpy.ops.slicewright.plate_add()
    other.slicewright.filament = 2
    bpy.context.view_layer.update()
    bpy.ops.slicewright.slice()
    yield from wait_for(lambda: sw.job_state in ("done", "failed"), "the two-filament slice")
    assert sw.job_state == "done", (sw.job_state, sw.job_message)
    result = runtime.get(bpy.context.scene).result
    assert sorted(result.objects) == ["Cube", "Cube2"] and result.wipe_tower is not None
    yield from wait_for(lambda: runtime.get(bpy.context.scene).controller.done, "the two-filament preview")
    g.capture(os.path.join(g.out_dir(), "real_two_filaments.png"))

    # a failed slice shows the typed error and the Issues list acts on it
    cube.location.x = 5000.0
    bpy.context.view_layer.update()
    bpy.ops.slicewright.slice()
    yield from wait_for(lambda: sw.job_state == "failed", "the failure")
    assert sw.job_message.startswith("Validation failed:"), sw.job_message
    rows = bpy.context.window_manager.slicewright_issues
    assert [r.level for r in rows][:1] == ["error"] and "Cube" in [r.object_name for r in rows], list(rows)
    bpy.ops.object.select_all(action="DESELECT")
    assert bpy.ops.slicewright.issue_goto(index=0) == {"FINISHED"}
    g.capture(os.path.join(g.out_dir(), "real_issues.png"))
    slicewright.unregister()
    yield 0.1


g.drive(steps)
