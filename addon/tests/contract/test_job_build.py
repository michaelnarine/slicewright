# SPDX-License-Identifier: GPL-3.0-or-later
"""Building a SliceJob: set_config, add_object, set_thumbnails, validate (04 sections 2.4, 4.1, 4.2)."""
from __future__ import annotations

import numpy as np
import pytest
from contract_helpers import assert_issue, box, flat_config, new_job


@pytest.fixture
def sc(jobs_backend):
    return jobs_backend


@pytest.fixture
def job(sc):
    j = sc.SliceJob()
    j.set_config(flat_config(sc))
    return j


# --- set_config / set_threads ----------------------------------------------------------

def test_new_job_is_idle(sc):
    job = sc.SliceJob()
    assert job.poll()[0] == "idle"


def test_set_config_exactly_once(sc, job):
    with pytest.raises(sc.StateError):
        job.set_config(flat_config(sc))


def test_add_object_before_set_config_is_a_state_error(sc):
    v, t = box()
    with pytest.raises(sc.StateError):
        sc.SliceJob().add_object("cube", v, t)


def test_set_config_rejects_unparsable_values(sc):
    job = sc.SliceJob()
    cfg = flat_config(sc)
    cfg["layer_height"] = "banana"
    with pytest.raises(sc.ConfigError) as err:
        job.set_config(cfg)
    assert err.value.key == "layer_height"


def test_set_config_rejects_unknown_keys(sc):
    cfg = flat_config(sc)
    cfg["no_such_option_xyz"] = "1"
    with pytest.raises(sc.ConfigError):
        sc.SliceJob().set_config(cfg)


def test_set_config_rejects_non_string_values(sc):
    with pytest.raises(TypeError):
        sc.SliceJob().set_config({"layer_height": 0.2})


@pytest.mark.parametrize("n", [-1, 0, 1, 4])
def test_set_threads_accepts_ints(job, n):
    job.set_threads(n)


# --- add_object ------------------------------------------------------------------------

def test_add_object_returns_sequential_int_handles(job):
    v, t = box()
    assert job.add_object("a", v, t) == 0
    assert job.add_object("b", v, t) == 1
    assert job.add_object("a", v, t) == 2  # names need not be unique


def test_add_object_copies_its_arrays(sc, job):
    v, t = box()
    job.add_object("cube", v, t)
    v[:] = 0.0
    t[:] = 0  # would make a degenerate mesh if the job still pointed at them
    assert not any(i["level"] == "error" for i in job.validate())


@pytest.mark.parametrize("bad_v", [
    np.zeros((8, 3), dtype=np.float64),      # dtype
    np.zeros((8, 3), dtype=np.int32),        # dtype
    [[0.0, 0.0, 0.0]] * 8,                   # not an array
])
def test_add_object_wrong_vertex_dtype_is_a_type_error(job, bad_v):
    _, t = box()
    with pytest.raises(TypeError):
        job.add_object("cube", bad_v, t)


def test_add_object_wrong_triangle_dtype_is_a_type_error(job):
    v, t = box()
    with pytest.raises(TypeError):
        job.add_object("cube", v, t.astype(np.int64))


@pytest.mark.parametrize("shape", [(8,), (8, 2), (8, 4)])
def test_add_object_wrong_vertex_shape_is_a_value_error(job, shape):
    _, t = box()
    with pytest.raises(ValueError):
        job.add_object("cube", np.zeros(shape, dtype=np.float32), t)


def test_add_object_non_contiguous_is_rejected(job):
    v, t = box()
    wide = np.zeros((8, 6), dtype=np.float32)
    wide[:, :3] = v
    with pytest.raises((TypeError, ValueError)):
        job.add_object("cube", wide[:, :3], t)   # a strided view


def test_add_object_triangle_index_out_of_range_is_a_value_error(job):
    v, t = box()
    t = t.copy()
    t[0, 0] = 99
    with pytest.raises(ValueError):
        job.add_object("cube", v, t)


@pytest.mark.parametrize("kw,bad", [
    ("face_support", 3), ("face_seam", 3), ("face_extruder", 17),   # paint state tops out at 16
])
def test_add_object_face_values_out_of_range(job, kw, bad):
    v, t = box()
    arr = np.zeros(len(t), dtype=np.uint8)
    arr[0] = bad
    with pytest.raises(ValueError):
        job.add_object("cube", v, t, **{kw: arr})


@pytest.mark.parametrize("kw", ["face_support", "face_seam", "face_extruder"])
def test_add_object_face_arrays_need_uint8_and_one_per_triangle(job, kw):
    v, t = box()
    with pytest.raises(TypeError):
        job.add_object("cube", v, t, **{kw: np.zeros(len(t), dtype=np.int32)})
    with pytest.raises(ValueError):
        job.add_object("cube", v, t, **{kw: np.zeros(len(t) + 1, dtype=np.uint8)})


def test_add_object_accepts_valid_face_arrays_and_extras(job):
    v, t = box()
    n = len(t)
    h = job.add_object(
        "painted", v, t, extruder=1, face_extruder=np.zeros(n, np.uint8),
        face_support=np.ones(n, np.uint8), face_seam=np.full(n, 2, np.uint8),
        repair=True, ensure_on_bed=True)
    assert isinstance(h, int)


def test_add_object_extruder_is_keyword_only(job):
    v, t = box()
    with pytest.raises(TypeError):
        job.add_object("cube", v, t, 1)


def test_add_object_override_outside_object_region_scope_is_a_config_error(sc, job):
    v, t = box()
    # printable_height is a printer (global) option
    with pytest.raises(sc.ConfigError):
        job.add_object("cube", v, t, config_overrides={"printable_height": "100"})


def test_add_object_accepts_object_scope_overrides(job):
    v, t = box()
    job.add_object("cube", v, t, config_overrides={"layer_height": "0.1"})


def test_mutators_are_rejected_after_start(sc):
    job = new_job(sc)
    job.start()
    v, t = box()
    with pytest.raises(sc.StateError):
        job.add_object("late", v, t)
    with pytest.raises(sc.StateError):
        job.set_config(flat_config(sc))
    job.cancel()


# --- set_thumbnails --------------------------------------------------------------------

def test_set_thumbnails_accepts_rgba_images(job):
    job.set_thumbnails([np.zeros((16, 16, 4), np.uint8), np.zeros((32, 48, 4), np.uint8)])
    job.set_thumbnails([])


@pytest.mark.parametrize("bad", [
    np.zeros((16, 16, 3), np.uint8),     # not RGBA
    np.zeros((16, 16, 4), np.float32),   # wrong dtype
    np.zeros((16, 16), np.uint8),        # wrong rank
])
def test_set_thumbnails_rejects_bad_images(job, bad):
    with pytest.raises((TypeError, ValueError)):
        job.set_thumbnails([bad])


# --- validate --------------------------------------------------------------------------

def test_validate_returns_issues_without_errors_for_a_sane_plate(sc):
    issues = new_job(sc).validate()
    assert isinstance(issues, list)
    for i in issues:
        assert_issue(i)
    assert not [i for i in issues if i["level"] == "error"]


def test_validate_flags_paint_beyond_the_filament_count(sc, job):
    v, t = box()
    fe = np.zeros(len(t), np.uint8)
    fe[3] = 5   # one filament slot is configured
    job.add_object("painted", v, t, face_extruder=fe)
    issues = job.validate()
    errors = [i for i in issues if i["level"] == "error"]
    assert errors and errors[0]["code"] == "paint_out_of_range"
    assert errors[0]["object_name"] == "painted"
    for i in issues:
        assert_issue(i)


def test_validate_is_repeatable(sc):
    job = new_job(sc)
    assert job.validate() == job.validate()
