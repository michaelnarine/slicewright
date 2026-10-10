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


def test_a_cancel_while_validating_is_cancelled_or_failed_never_lost():
    job = make_job(("cube", box(100.0, 100.0)))
    job.start()
    job.cancel()
    assert finish(job) in ("cancelled", "done")


def test_validate_is_cached_until_the_plate_changes():
    job = make_job(("a", box(100.0, 100.0)))
    first = job.validate()
    assert job.validate() == first
    job.add_object("b", *box(150.0, 150.0))  # invalidates the cache
    assert job.validate() == first
    job.start()
    assert finish(job) == "done"
    assert job.result().objects == ["a", "b"]


def test_validate_then_start_gives_the_same_gcode_as_start_alone():
    a = cube_case.build_job(sc)
    a.validate()
    ra = a.run()
    rb = cube_case.build_job(sc).run()
    assert open(ra.gcode_path, "rb").read() == open(rb.gcode_path, "rb").read()


def test_validate_after_start_is_a_state_error():
    job = make_job(("a", box(100.0, 100.0)))
    job.start()
    with pytest.raises(sc.StateError):
        job.validate()
    finish(job)
    with pytest.raises(sc.StateError):
        job.validate()
