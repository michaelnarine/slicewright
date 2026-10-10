# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 2: the validating state, the engine's own checks and the error mapping (04 sections 4.2, 4.3, 7, 8)."""
import time

import numpy as np
import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402

TERMINAL = ("done", "failed", "cancelled")


def box(cx, cy, size=20.0, height=None, z0=0.0):
    h = size if height is None else height
    r = size / 2.0
    v = np.array([(cx - r, cy - r, z0), (cx + r, cy - r, z0), (cx + r, cy + r, z0), (cx - r, cy + r, z0),
                  (cx - r, cy - r, z0 + h), (cx + r, cy - r, z0 + h), (cx + r, cy + r, z0 + h), (cx - r, cy + r, z0 + h)],
                 dtype=np.float32)
    t = np.array([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
                  (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)], dtype=np.int32)
    return np.ascontiguousarray(v), np.ascontiguousarray(t)


def make_job(*objects):
    p = cube_case.profiles()
    flat = sc.normalize_config(sc.compose_config(p["machine"], p["process"], [p["filament"]]))["config"]
    job = sc.SliceJob()
    job.set_config(flat)
    for name, (v, t) in objects:
        job.add_object(name, v, t)
    return job


def finish(job):
    end = time.monotonic() + 60
    while job.poll()[0] not in TERMINAL:
        assert time.monotonic() < end
        time.sleep(0.001)
    return job.poll()[0]


def test_validate_reports_an_object_outside_the_bed_as_an_error():
    job = make_job(("far", box(500.0, 500.0)))
    errors = [i for i in job.validate() if i["level"] == "error"]
    assert [(i["code"], i["object_name"], i["opt_key"]) for i in errors] == [("object_outside_bed", "far", "printable_area")]


def test_validate_reports_an_object_that_is_too_tall():
    job = make_job(("tall", box(100.0, 100.0, size=20.0, height=900.0)))
    errors = [i for i in job.validate() if i["level"] == "error"]
    assert [(i["code"], i["object_name"]) for i in errors] == [("object_too_tall", "tall")]


def test_an_object_flush_with_the_bed_edge_is_inside():
    p = cube_case.profiles()
    cx, cy = cube_case.bed_centre(p["machine"])
    job = make_job(("a", box(cx, cy)))
    assert not [i for i in job.validate() if i["level"] == "error"]


def test_start_turns_an_engine_error_into_a_failed_job_with_a_validation_error():
    job = make_job(("ok", box(100.0, 100.0)), ("far", box(500.0, 500.0)))
    job.start()
    assert job.poll()[0] in ("validating", "failed")
    assert finish(job) == "failed"
    for _ in range(2):  # raised on every call
        with pytest.raises(sc.ValidationError) as err:
            job.result()
    e = err.value
    assert e.object_name == "far" and e.opt_key == "printable_area"
    assert [i["code"] for i in e.issues if i["level"] == "error"] == ["object_outside_bed"]
    # the lock is free again, immediately
    nxt = cube_case.build_job(sc)
    nxt.start()
    assert finish(nxt) == "done"


def test_a_cancel_right_after_start_ends_cancelled_or_done_and_never_gets_lost():
    """cancel() in `validating` or `running`: the job ends `cancelled` (result() raises Cancelled) unless it finished
    before the cancel took effect (04 section 8); it never stays in `cancelling` and never fails."""
    job = make_job(("cube", box(100.0, 100.0)))
    job.start()
    job.cancel()
    state = finish(job)
    assert state in ("cancelled", "done")
    if state == "cancelled":
        with pytest.raises(sc.Cancelled):
            job.result()
    else:
        assert job.result().objects == ["cube"]
    nxt = cube_case.build_job(sc)  # and the engine lock is free again
    nxt.start()
    assert finish(nxt) == "done"


def test_validate_is_cached_until_the_plate_changes():
    job = make_job(("a", box(100.0, 100.0)))
    assert job._validation_runs() == 0
    first = job.validate()
    assert job.validate() == first
    assert job._validation_runs() == 1, "the second validate() must come from the cache"
    # Adding an object invalidates the cache: the new object is outside the bed, so the result changes, and the
    # work is done again.
    job.add_object("far", *box(500.0, 500.0))
    second = job.validate()
    assert second != first
    assert [i["object_name"] for i in second if i["code"] == "object_outside_bed"] == ["far"]
    assert job._validation_runs() == 2
    job.validate()
    assert job._validation_runs() == 2


def test_start_after_validate_reuses_the_cached_validation():
    job = make_job(("a", box(100.0, 100.0)), ("b", box(150.0, 150.0)))
    job.validate()
    assert job._validation_runs() == 1
    job.start()
    assert finish(job) == "done"
    assert job._validation_runs() == 1, "start() redid the validation"
    assert job.result().objects == ["a", "b"]


def test_orca_naming_the_object_in_a_validation_error_gives_object_name():
    """Print::validate reports the offending PrintObject (StringObjectException::object, a PrintObject*, not the
    ModelObject), so the engine must map it back to the add_object name."""
    # per-object layer height above the nozzle diameter: "Layer height cannot exceed nozzle diameter."
    p = cube_case.profiles()
    flat = sc.normalize_config(sc.compose_config(p["machine"], p["process"], [p["filament"]]))["config"]
    job = sc.SliceJob()
    job.set_config(flat)
    job.add_object("fine", *box(100.0, 100.0))
    job.add_object("coarse", *box(160.0, 100.0), config_overrides={"layer_height": "0.6"})
    errors = [i for i in job.validate() if i["level"] == "error" and i["code"] == "validation"]
    assert errors, "Orca did not reject a layer height above the nozzle diameter"
    assert errors[0]["object_name"] == "coarse" and errors[0]["opt_key"] == "layer_height"
    job.start()
    assert finish(job) == "failed"
    with pytest.raises(sc.ValidationError) as err:
        job.result()
    assert err.value.object_name == "coarse"


def test_orca_naming_the_object_in_a_slicing_error_gives_object_name():
    """GCode.cpp throws SlicingError(..., PrintObject::id().id) for an object whose first layer is empty; the id is a
    PrintObject id, not a ModelObject id. Here a cube floats 3 mm above the bed (ensure_on_bed is off), next to a good one."""
    job = make_job(("good", box(100.0, 100.0)), ("floating", box(160.0, 100.0, z0=3.0)))
    job.start()
    assert finish(job) == "failed"
    with pytest.raises(sc.SliceError) as err:
        job.result()
    assert "empty first layer" in err.value.message
    assert err.value.object_name == "floating"


def test_validate_then_start_gives_the_same_gcode_as_start_alone():
    a = cube_case.build_job(sc)
    a.validate()
    ra = a.run()
    rb = cube_case.build_job(sc).run()
    norm = cube_case.normalize_gcode.normalize  # the header carries a timestamp to the second
    assert norm(open(ra.gcode_path, errors="replace").read()) == norm(open(rb.gcode_path, errors="replace").read())


def test_validate_after_start_is_a_state_error():
    job = make_job(("a", box(100.0, 100.0)))
    job.start()
    with pytest.raises(sc.StateError):
        job.validate()
    finish(job)
    with pytest.raises(sc.StateError):
        job.validate()
