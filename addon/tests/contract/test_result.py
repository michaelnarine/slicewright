# SPDX-License-Identifier: GPL-3.0-or-later
"""SliceResult: dtypes, shapes, layer guarantees, stats, G-code files (04 section 5)."""
from __future__ import annotations

import gc
import os
import zipfile

import numpy as np
import pytest
from contract_helpers import assert_issue, box, drive, new_job, sliced

MOVE_FIELDS = {
    "position": (np.float32, 3), "type": (np.uint8, None), "role": (np.uint8, None),
    "filament": (np.uint8, None), "nozzle": (np.uint8, None), "color_id": (np.uint8, None),
    "width": (np.float32, None), "height": (np.float32, None), "mm3_per_mm": (np.float32, None),
    "feedrate": (np.float32, None), "actual_feedrate": (np.float32, None),
    "fan": (np.float32, None), "temperature": (np.float32, None),
    "pressure_advance": (np.float32, None), "acceleration": (np.float32, None),
    "jerk": (np.float32, None), "time": (np.float32, 2), "layer_id": (np.uint32, None),
    "print_z": (np.float32, None), "object_id": (np.int32, None), "gcode_line": (np.uint32, None),
}


@pytest.fixture
def sc(jobs_backend):
    return jobs_backend


@pytest.fixture
def result(sc):
    return sliced(sc)[1]


@pytest.fixture
def two_object_result(sc):
    objs = [("left", *box(cx=80.0)), ("right", *box(cx=170.0, size=14.0))]
    return sliced(sc, objects=objs)[1]


# --- moves -----------------------------------------------------------------------------

def test_moves_fields_dtypes_and_shapes(result):
    moves = result.moves
    assert set(moves) >= set(MOVE_FIELDS)
    k = len(moves["type"])
    assert k > 10
    for name, (dtype, cols) in MOVE_FIELDS.items():
        arr = moves[name]
        assert isinstance(arr, np.ndarray), name
        assert arr.dtype == dtype, f"{name}: {arr.dtype} != {np.dtype(dtype)}"
        assert arr.shape == ((k,) if cols is None else (k, cols)), f"{name}: {arr.shape}"


def test_all_result_arrays_are_read_only(result):
    arrays = list(result.moves.values()) + list(result.layers.values()) + [result.gcode_line_ends]
    for arr in arrays:
        assert arr.flags.writeable is False
        with pytest.raises(ValueError):
            arr.flat[0] = arr.flat[0]


def test_type_and_role_values_fit_the_texture_packing(sc, result):
    enums = sc.enums()
    assert int(result.moves["type"].max()) < 16
    assert int(result.moves["role"].max()) < 32
    assert set(np.unique(result.moves["type"]).tolist()) <= set(enums["move_type"].values())
    assert set(np.unique(result.moves["role"]).tolist()) <= set(enums["role"].values())


def test_a_cube_has_extrusions_and_travel(sc, result):
    types = result.moves["type"]
    assert (types == sc.enums()["move_type"]["Extrude"]).any()
    assert (types == sc.enums()["move_type"]["Travel"]).any()


def test_extrusion_geometry_is_sane(sc, result):
    m = result.moves
    ext = m["type"] == sc.enums()["move_type"]["Extrude"]
    assert (m["width"][ext] > 0).all() and (m["height"][ext] > 0).all()
    assert (m["mm3_per_mm"][ext] > 0).all()
    assert float(m["position"][ext][:, 2].max()) <= 20.0 + 1.0   # a 20 mm cube
    assert (m["time"] >= 0).all()


def test_object_and_filament_indices(result):
    m = result.moves
    assert m["object_id"].min() >= -1 and m["object_id"].max() < len(result.objects)
    assert ((m["filament"] == 0) | (m["filament"] == 255)).all()   # one filament, 0-based


def test_both_objects_are_referenced(two_object_result):
    r = two_object_result
    assert r.objects == ["left", "right"]
    ids = set(np.unique(r.moves["object_id"]).tolist())
    assert {0, 1} <= ids


# --- layers (5.3) ----------------------------------------------------------------------

def test_layer_arrays(result):
    layers = result.layers
    assert set(layers) >= {"z", "first", "last"}
    assert layers["z"].dtype == np.float32
    assert layers["first"].dtype == np.uint32 and layers["last"].dtype == np.uint32
    n = len(layers["z"])
    assert n >= 2 and layers["first"].shape == (n,) and layers["last"].shape == (n,)


def _check_layer_ranges(r):
    first, last = r.layers["first"].astype(np.int64), r.layers["last"].astype(np.int64)
    k = len(r.moves["type"])
    assert first[0] == 0
    assert last[-1] == k - 1
    assert (first[1:] == last[:-1] + 1).all()
    assert (first <= last).all()
    lid = r.moves["layer_id"].astype(np.int64)
    idx = np.arange(k)
    assert (lid >= 0).all() and (lid < len(first)).all()
    assert (first[lid] <= idx).all() and (idx <= last[lid]).all()


def test_layer_ranges_are_contiguous_and_cover_all_moves(result):
    _check_layer_ranges(result)


def test_layer_ranges_with_two_objects(two_object_result):
    _check_layer_ranges(two_object_result)


def test_layer_z_increases_for_normal_printing(result):
    assert (np.diff(result.layers["z"]) > 0).all()


def test_print_z_follows_the_layer_height(result):
    z = result.layers["z"]
    assert abs(float(z[0]) - 0.2) < 1e-3
    assert abs(float(np.diff(z).mean()) - 0.2) < 1e-3
    assert abs(float(z[-1]) - 20.0) < 0.25


# --- G-code ----------------------------------------------------------------------------

def test_gcode_file_and_line_index(result):
    assert os.path.isfile(result.gcode_path)
    data = open(result.gcode_path, "rb").read()
    assert data
    ends = result.gcode_line_ends
    assert ends.dtype == np.uint64 and ends.ndim == 1
    assert (np.diff(ends.astype(np.int64)) > 0).all()
    assert int(ends[-1]) == len(data)
    assert data[int(ends[0]) - 1:int(ends[0])] == b"\n"
    assert data.count(b"\n") in (len(ends), len(ends) - 1)
    lines = result.moves["gcode_line"]
    assert lines.min() >= 1 and lines.max() <= len(ends)


def test_write_gcode_is_byte_identical(result, tmp_path):
    out = tmp_path / "copy.gcode"
    result.write_gcode(str(out))
    assert out.read_bytes() == open(result.gcode_path, "rb").read()


def test_output_filename(result):
    name = result.output_filename("Cube")
    assert isinstance(name, str) and name.startswith("Cube") and name.endswith(".gcode")


def test_output_filename_uses_the_basename_verbatim(result):
    """`{input_filename_base}` is the basename as given, like Orca's: a dot in it is not an extension."""
    assert result.output_filename("my.part").startswith("my.part")


def test_write_gcode_3mf_has_the_bambu_metadata_parts(result, tmp_path):
    out = tmp_path / "plate.gcode.3mf"
    result.write_gcode_3mf(str(out), {"plate_name": "Contract plate"})
    assert zipfile.is_zipfile(out)
    names = set(zipfile.ZipFile(out).namelist())
    for part in ("Metadata/plate_1.gcode", "Metadata/plate_1.gcode.md5",
                 "Metadata/slice_info.config", "Metadata/model_settings.config",
                 "Metadata/project_settings.config"):
        assert part in names, part


def test_write_gcode_3mf_without_plate_meta(result, tmp_path):
    result.write_gcode_3mf(str(tmp_path / "x.gcode.3mf"))


def test_write_gcode_3mf_carries_only_the_plate_thumbnails_as_images(sc, tmp_path):
    """04 section 5.1: the largest set_thumbnails image is plate_1.png (Orca's writer derives plate_1_small.png from it);
    the GL-rendered no_light/top/pick images of Orca's GUI are not produced."""
    img = np.zeros((48, 48, 4), np.uint8)
    img[..., 0] = 200
    img[..., 3] = 255
    job = new_job(sc)
    job.set_thumbnails([img])
    job.start()
    drive(job)
    out = tmp_path / "plate.gcode.3mf"
    job.result().write_gcode_3mf(str(out))
    images = [n for n in zipfile.ZipFile(out).namelist() if n.lower().endswith((".png", ".jpg", ".jpeg"))]
    assert "Metadata/plate_1.png" in images
    assert set(images) <= {"Metadata/plate_1.png", "Metadata/plate_1_small.png"}, images


def test_write_gcode_3mf_is_busy_while_a_job_runs(sc, tmp_path):
    """04 sections 5.1, 7 and 9 rule 3: the writer sets the process-global dialect flag, so it takes the engine lock."""
    _, finished = sliced(sc)
    running = new_job(sc)
    running.start()
    try:
        with pytest.raises(sc.Busy):
            finished.write_gcode_3mf(str(tmp_path / "busy.gcode.3mf"))
        assert not (tmp_path / "busy.gcode.3mf").exists()
    finally:
        drive(running)
    finished.write_gcode_3mf(str(tmp_path / "free.gcode.3mf"))  # the lock is free again
    assert zipfile.is_zipfile(tmp_path / "free.gcode.3mf")


# --- stats (5.4) and other fields (5.5) ------------------------------------------------

def test_stats_shape(result):
    s = result.stats
    for key in ("time_s", "prepare_time_s", "time_by_role_s", "time_by_move_type_s",
                "filament_per_extruder", "used_filament_per_role", "flush_per_filament_g",
                "total_filament_changes", "total_tool_changes", "layer_count",
                "total_travel_mm", "display"):
        assert key in s, key
    assert set(s["time_s"]) >= {"normal", "silent"} and s["time_s"]["normal"] > 0
    fe = s["filament_per_extruder"]
    assert len(fe) == 1 and set(fe[0]) >= {"mm", "cm3", "g", "cost"} and fe[0]["mm"] > 0
    assert s["layer_count"] == len(result.layers["z"])
    assert all(isinstance(v, str) for v in s["display"].values())


def test_stats_threads_is_the_arena_the_job_ran_in(sc):
    job = new_job(sc)
    job.set_threads(1)
    job.start()
    drive(job)
    threads = job.result().stats["threads"]
    assert isinstance(threads, int) and not isinstance(threads, bool) and threads == 1
    default = sliced(sc)[1].stats["threads"]
    assert isinstance(default, int) and default >= 1


def test_time_breakdown_adds_up_to_the_total(result):
    s = result.stats
    parts = sum(v[0] for v in s["time_by_role_s"].values())
    parts += sum(v[0] for v in s["time_by_move_type_s"].values())
    total = s["time_s"]["normal"]
    assert abs(parts - total) <= 0.01 * total


def test_time_by_role_names_are_known(sc, result):
    roles = set(sc.enums()["role"]) | {"Undefined"}
    assert set(result.stats["time_by_role_s"]) <= roles


def test_warnings_objects_and_wipe_tower(result):
    assert isinstance(result.warnings, list)
    for w in result.warnings:
        assert_issue(w)
    assert result.objects == ["cube"]
    assert result.wipe_tower is None


def test_result_outlives_its_job(sc):
    job, result = sliced(sc)
    n = len(result.moves["type"])
    path = result.gcode_path
    del job
    gc.collect()
    assert len(result.moves["type"]) == n
    assert os.path.isfile(path)
