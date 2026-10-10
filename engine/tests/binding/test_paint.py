# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 4: per-face paint becomes TriangleSelector states and FacetsAnnotation (04 section 2.4, 02 section 5.3).

The mesh is a stem with a wide slab on top, so the slab's underside is a 90 degree overhang: support enforcers,
blockers and the "enforcer with supports off" question (04 A.2 #11) are all observable in the G-code."""
import re

import numpy as np
import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402
from test_validating_errors import box  # noqa: E402

SLAB_BOTTOM = (12, 13)  # the two triangles of the slab's bottom face in the mesh built below (box() order)


def tee(cx, cy):
    """A 10 mm stem (z 0..10) and a 40 mm slab (z 10..15) in one mesh; triangles 0..11 are the stem's, 12..23 the slab's."""
    v1, t1 = box(cx, cy, size=10.0, height=10.0)
    v2, t2 = box(cx, cy, size=40.0, height=5.0, z0=10.0)
    return np.ascontiguousarray(np.vstack([v1, v2])), np.ascontiguousarray(np.vstack([t1, t2 + len(v1)]).astype(np.int32))


def config(filaments=1, **process):
    p = cube_case.profiles()
    fil = [p["filament"]] + [{**p["filament"], "name": f"F{i}", "filament_colour": [f"#11223{i}"]} for i in range(1, filaments)]
    project = {"flush_volumes_matrix": ",".join("0" if i == j else "100" for i in range(filaments) for j in range(filaments))}
    flat = sc.compose_config(p["machine"], {**p["process"], **process}, fil, project if filaments > 1 else None)
    return sc.normalize_config(flat)["config"]


def slice_tee(filaments=1, process=None, **paint):
    cx, cy = cube_case.bed_centre(cube_case.profiles()["machine"])
    j = sc.SliceJob()
    j.set_config(config(filaments, **(process or {})))
    v, t = tee(cx, cy)
    arrays = {k: a for k, a in paint.items()}
    j.add_object("tee", v, t, **arrays)
    r = j.run()
    return open(r.gcode_path, errors="replace").read(), r


def support_lines(text):
    return len(re.findall(r"^;TYPE:Support( interface)?$", text, re.M))


def face_array(value, faces=SLAB_BOTTOM, n=24):
    a = np.zeros(n, np.uint8)
    for f in faces:
        a[f] = value
    return a


def test_without_paint_and_without_supports_there_is_no_support():
    text, _ = slice_tee()
    assert support_lines(text) == 0


MANUAL = {"enable_support": "1", "support_type": "normal(manual)"}


def test_a_support_enforcer_generates_support_in_manual_mode():
    """With manual supports nothing is generated until faces are painted: this proves the enforcer reaches Orca."""
    unpainted, _ = slice_tee(process=MANUAL)
    painted, _ = slice_tee(process=MANUAL, face_support=face_array(1))
    assert support_lines(unpainted) == 0
    assert support_lines(painted) > 0


def test_a_support_enforcer_with_supports_off_does_nothing():
    """Pins 04 A.2 #11 (the 'enforcer with supports off' row): with enable_support = 0 the paint passes through
    unchanged and Orca v2.4.2 generates no support at all, so an enforcer is inert while supports are disabled."""
    text, _ = slice_tee(face_support=face_array(1))
    assert support_lines(text) == 0
    assert cube_case.normalize_gcode.normalize(text) == cube_case.normalize_gcode.normalize(slice_tee()[0])


def test_a_support_blocker_removes_support_that_would_be_generated():
    plain, _ = slice_tee(process={"enable_support": "1", "support_type": "normal(auto)"})
    blocked, _ = slice_tee(process={"enable_support": "1", "support_type": "normal(auto)"}, face_support=face_array(2))
    assert support_lines(plain) > 0
    assert support_lines(blocked) < support_lines(plain)


def test_an_enforcer_changes_nothing_outside_the_painted_faces():
    text_off, _ = slice_tee(process={"enable_support": "1", "support_type": "normal(auto)"}, face_support=face_array(1, faces=(0,)))
    text_plain, _ = slice_tee(process={"enable_support": "1", "support_type": "normal(auto)"})
    # a stem face is not an overhang: the paint must not remove the automatic support under the slab
    assert support_lines(text_off) >= support_lines(text_plain) > 0


def test_seam_paint_changes_the_gcode_deterministically():
    plain, _ = slice_tee()
    painted_a, _ = slice_tee(face_seam=face_array(1, faces=(14, 15)))
    painted_b, _ = slice_tee(face_seam=face_array(1, faces=(14, 15)))
    assert painted_a == painted_b
    assert painted_a != plain


def test_multi_material_paint_selects_the_second_filament():
    text, _ = slice_tee(filaments=2, face_extruder=face_array(2, faces=range(12, 24)))
    assert "\nT1" in text
    only_first, _ = slice_tee(filaments=2, face_extruder=face_array(1, faces=range(12, 24)))
    assert "\nT1" not in only_first


def _job_with(filaments, value):
    cx, cy = cube_case.bed_centre(cube_case.profiles()["machine"])
    j = sc.SliceJob()
    j.set_config(config(filaments))
    v, t = tee(cx, cy)
    j.add_object("painted", v, t, face_extruder=face_array(value, faces=(3,)))
    return j


@pytest.mark.parametrize("filaments,value,ok", [(1, 1, True), (1, 2, False), (2, 2, True), (2, 3, False), (3, 3, True), (3, 4, False)])
def test_paint_out_of_range_against_the_filament_count(filaments, value, ok):
    issues = _job_with(filaments, value).validate()
    errors = [i for i in issues if i["level"] == "error"]
    if ok:
        assert not errors
    else:
        assert [(i["code"], i["object_name"]) for i in errors] == [("paint_out_of_range", "painted")]
        assert errors[0]["opt_key"] == "filament_colour"


def test_paint_out_of_range_fails_the_job_with_a_validation_error():
    j = _job_with(1, 5)
    j.start()
    with pytest.raises(sc.ValidationError) as err:
        j.result()
    assert err.value.object_name == "painted"
    assert [i["code"] for i in err.value.issues if i["level"] == "error"] == ["paint_out_of_range"]


@pytest.mark.parametrize("kw,bad", [("face_support", 3), ("face_seam", 3), ("face_extruder", 17)])
def test_values_beyond_the_encodable_range_are_value_errors(kw, bad):
    cx, cy = cube_case.bed_centre(cube_case.profiles()["machine"])
    j = sc.SliceJob()
    j.set_config(config())
    v, t = tee(cx, cy)
    with pytest.raises(ValueError):
        j.add_object("x", v, t, **{kw: face_array(bad)})


def test_the_top_of_the_range_is_accepted_with_sixteen_filaments():
    # 16 is TriangleSelector's Extruder16, the largest state: it must be accepted and used when 16 filaments exist
    cx, cy = cube_case.bed_centre(cube_case.profiles()["machine"])
    p = cube_case.profiles()
    fil = [{**p["filament"], "name": f"F{i}", "filament_colour": [f"#0000{i:02X}"]} for i in range(16)]
    project = {"flush_volumes_matrix": ",".join("0" if i == j else "100" for i in range(16) for j in range(16))}
    flat = sc.normalize_config(sc.compose_config(p["machine"], p["process"], fil, project))["config"]
    j = sc.SliceJob()
    j.set_config(flat)
    v, t = tee(cx, cy)
    j.add_object("sixteen", v, t, face_extruder=face_array(16, faces=range(12, 24)))
    assert not [i for i in j.validate() if i["level"] == "error"]


def test_paint_arrays_are_copied():
    cx, cy = cube_case.bed_centre(cube_case.profiles()["machine"])
    j = sc.SliceJob()
    j.set_config(config(**MANUAL))
    v, t = tee(cx, cy)
    arr = face_array(1)
    j.add_object("tee", v, t, face_support=arr)
    arr[:] = 0
    text = open(j.run().gcode_path, errors="replace").read()
    assert support_lines(text) > 0
