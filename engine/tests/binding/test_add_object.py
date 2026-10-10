# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 3: add_object arrays, set_config ordering, the object-level extruder, the overrides scope
(04 sections 2.4, 4.1)."""
import numpy as np
import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402
from test_validating_errors import box  # noqa: E402


def config(filaments=1):
    p = cube_case.profiles()
    fil = [p["filament"]] + [{**p["filament"], "name": f"F{i}", "filament_colour": ["#112233"]} for i in range(1, filaments)]
    # Two filaments need a flush matrix of n*n entries (the project's flush_volumes_matrix, 04 section 6.1).
    project = {"flush_volumes_matrix": ",".join("0" if i == j else "100" for i in range(filaments) for j in range(filaments))}
    return sc.normalize_config(sc.compose_config(p["machine"], p["process"], fil, project if filaments > 1 else None))["config"]


def centre():
    return cube_case.bed_centre(cube_case.profiles()["machine"])


def job(filaments=1):
    j = sc.SliceJob()
    j.set_config(config(filaments))
    return j


def test_set_config_comes_first_and_once():
    j = sc.SliceJob()
    with pytest.raises(sc.StateError):
        j.add_object("a", *box(*centre()))
    j.set_config(config())
    with pytest.raises(sc.StateError):
        j.set_config(config())


def test_unknown_config_keys_raise_config_error():
    cfg = config()
    cfg["no_such_option_xyz"] = "1"
    with pytest.raises(sc.ConfigError) as err:
        sc.SliceJob().set_config(cfg)
    assert err.value.key == "no_such_option_xyz"


def test_handles_are_sequential_and_names_may_repeat():
    j = job()
    cx, cy = centre()
    assert [j.add_object("n", *box(cx + dx, cy)) for dx in (-40, 0, 40)] == [0, 1, 2]


@pytest.mark.parametrize("make,exc", [
    (lambda v, t: (v.astype(np.float64), t), TypeError),
    (lambda v, t: (v, t.astype(np.int64)), TypeError),
    (lambda v, t: (v.tolist(), t), TypeError),
    (lambda v, t: (v[:, :2].copy(), t), ValueError),
    (lambda v, t: (v.reshape(-1), t), ValueError),
    (lambda v, t: (v, t[:, :2].copy()), ValueError),
    (lambda v, t: (np.asfortranarray(v), t), TypeError),
    (lambda v, t: (np.full_like(v, np.nan), t), ValueError),
    (lambda v, t: (v, np.where(t == 3, 99, t).astype(np.int32)), ValueError),
])
def test_bad_mesh_arrays_are_rejected_without_touching_the_job(make, exc):
    j = job()
    v, t = box(*centre())
    bad_v, bad_t = make(v, t)
    with pytest.raises(exc):
        j.add_object("bad", bad_v, bad_t)
    assert j.add_object("good", v, t) == 0  # no phantom object was left behind


def test_the_job_keeps_its_own_copy_of_the_arrays():
    j = job()
    v, t = box(*centre())
    fe = np.zeros(len(t), np.uint8)
    j.add_object("a", v, t, face_extruder=fe)
    reference = open(j.run().gcode_path, "rb").read()
    j2 = job()
    v2, t2 = box(*centre())
    j2.add_object("a", v2, t2)
    v2[:] = 0.0
    t2[:] = 0
    assert open(j2.run().gcode_path, "rb").read() == reference


@pytest.mark.parametrize("kw,bad", [("face_support", 3), ("face_seam", 3), ("face_extruder", 17)])
def test_face_values_outside_their_range_are_value_errors(kw, bad):
    v, t = box(*centre())
    arr = np.zeros(len(t), np.uint8)
    arr[2] = bad
    with pytest.raises(ValueError):
        job().add_object("a", v, t, **{kw: arr})


@pytest.mark.parametrize("kw", ["face_support", "face_seam", "face_extruder"])
def test_face_arrays_must_be_uint8_and_one_per_triangle(kw):
    v, t = box(*centre())
    with pytest.raises(TypeError):
        job().add_object("a", v, t, **{kw: np.zeros(len(t), np.int32)})
    with pytest.raises(ValueError):
        job().add_object("a", v, t, **{kw: np.zeros(len(t) + 1, np.uint8)})
    with pytest.raises(ValueError):
        job().add_object("a", v, t, **{kw: np.zeros((len(t), 1), np.uint8)})


def test_object_level_extruder_selects_the_tool():
    j = job(filaments=2)
    cx, cy = centre()
    j.add_object("first", *box(cx - 30, cy), extruder=0)   # 0 inherits: filament 1
    j.add_object("second", *box(cx + 30, cy), extruder=2)  # slot 2, set on the object
    r = j.run()
    text = open(r.gcode_path, errors="replace").read()
    assert "\nT1" in text, "the second object should print with tool 1"


@pytest.mark.parametrize("extruder,ok", [(0, True), (1, True), (16, True), (17, False), (-1, False)])
def test_extruder_range(extruder, ok):
    v, t = box(*centre())
    if ok:
        job().add_object("a", v, t, extruder=extruder)
    else:
        with pytest.raises(ValueError):
            job().add_object("a", v, t, extruder=extruder)


def test_overrides_apply_to_the_object_only():
    cx, cy = centre()
    base = job()
    base.add_object("a", *box(cx, cy))
    over = job()  # layer_height is object scope; initial_layer_print_height is global
    over.add_object("a", *box(cx, cy), config_overrides={"layer_height": "0.1"})
    assert over.run().stats["layer_count"] > 1.5 * base.run().stats["layer_count"]


@pytest.mark.parametrize("bad", [{"printable_height": "100"}, {"nozzle_diameter": "0.6"}, {"no_such_key": "1"}])
def test_overrides_outside_object_and_region_scope_are_config_errors_and_leave_no_object(bad):
    j = job()
    v, t = box(*centre())
    with pytest.raises(sc.ConfigError):
        j.add_object("a", v, t, config_overrides=bad)
    assert j.add_object("ok", v, t) == 0


def test_an_unparsable_override_value_names_the_key():
    j = job()
    with pytest.raises(sc.ConfigError) as err:
        j.add_object("a", *box(*centre()), config_overrides={"layer_height": "banana"})
    assert err.value.key == "layer_height"


def test_repair_merges_vertices_keeps_faces_and_reports_open_edges():
    cx, cy = centre()
    v, t = box(cx, cy)
    # Unshare the vertices (one copy per triangle corner) and drop one triangle: open, and unmerged.
    tv = v[t.reshape(-1)]
    tt = np.arange(len(tv), dtype=np.int32).reshape(-1, 3)[:-1].copy()
    j = job()
    j.add_object("holey", np.ascontiguousarray(tv), tt, repair=True)
    issues = j.validate()
    open_edges = [i for i in issues if i["code"] == "mesh_open_edges"]
    assert len(open_edges) == 1 and open_edges[0]["level"] == "warning" and open_edges[0]["object_name"] == "holey"
    # without repair nothing is merged or reported
    j2 = job()
    j2.add_object("holey", np.ascontiguousarray(tv), tt)
    assert not [i for i in j2.validate() if i["code"] == "mesh_open_edges"]


def test_ensure_on_bed_reports_moved_to_bed():
    cx, cy = centre()
    j = job()
    j.add_object("floating", *box(cx, cy, z0=5.0), ensure_on_bed=True)
    moved = [i for i in j.validate() if i["code"] == "moved_to_bed"]
    assert len(moved) == 1 and moved[0]["level"] == "info" and moved[0]["object_name"] == "floating"
    j2 = job()
    j2.add_object("grounded", *box(cx, cy), ensure_on_bed=True)
    assert not [i for i in j2.validate() if i["code"] == "moved_to_bed"]
