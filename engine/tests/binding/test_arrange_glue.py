# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 8: validate() caching is in test_validating_errors; this is arrange (04 section 4.5) with the CLI/GUI
glue of 02 section 5.9: the bed minus bed_exclude_area and the wipe tower as obstacles."""
import numpy as np
import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402
from test_validating_errors import box  # noqa: E402


def job_with(objects, machine_extra=None, filaments=1, process_extra=None, project=None):
    p = cube_case.profiles()
    machine = {**p["machine"], **(machine_extra or {})}
    fil = [p["filament"]] + [{**p["filament"], "name": f"F{i}", "filament_colour": [f"#11223{i}"]} for i in range(1, filaments)]
    proj = {"flush_volumes_matrix": ",".join("0" if i == j else "100" for i in range(filaments) for j in range(filaments))} if filaments > 1 else {}
    proj.update(project or {})
    flat = sc.normalize_config(sc.compose_config(machine, {**p["process"], **(process_extra or {})}, fil, proj or None))["config"]
    j = sc.SliceJob()
    j.set_config(flat)
    for name, v, t in objects:
        j.add_object(name, v, t)
    return j


def stack(n, size=40.0, cx=125.0, cy=125.0):
    return [(f"o{i}", *box(cx, cy, size=size)) for i in range(n)]


def footprints(placements, objects):
    out = []
    for p in placements:
        _, v, _ = objects[p["index"]]
        moved = (p["transform"] @ np.c_[v.astype(np.float64), np.ones(len(v))].T).T[:, :3]
        out.append((moved[:, 0].min(), moved[:, 1].min(), moved[:, 0].max(), moved[:, 1].max()))
    return out


def overlap(a, b, gap=0.0):
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def test_exclude_areas_are_obstacles():
    """bed_exclude_area groups of four points are rectangles (PartPlateList::preprocess_exclude_areas)."""
    objs = stack(3)
    excl = ["0x0", "100x0", "100x250", "0x250"]  # the left 100 mm of the 250 mm bed
    placements = job_with(objs, machine_extra={"bed_exclude_area": excl}).arrange()
    for box_ in footprints(placements, objs):
        assert box_[0] >= 100.0 - 1e-3, box_
        assert box_[2] <= 250.0 + 1e-3 and box_[1] >= -1e-3 and box_[3] <= 250.0 + 1e-3
    # without the exclude area the same plate uses the left part too
    free = footprints(job_with(objs).arrange(), objs)
    assert min(b[0] for b in free) < 100.0


def test_an_exclude_area_that_leaves_no_room_raises_arrange_error():
    objs = stack(2, size=40.0)
    excl = ["0x0", "250x0", "250x230", "0x230"]  # leaves a 20 mm strip
    with pytest.raises(sc.ArrangeError) as err:
        job_with(objs, machine_extra={"bed_exclude_area": excl}).arrange()
    assert set(err.value.object_names) <= {"o0", "o1"} and err.value.object_names


def test_the_wipe_tower_is_an_obstacle_when_one_will_be_generated():
    objs = stack(4, size=40.0)
    j = job_with(objs, filaments=2, process_extra={"enable_prime_tower": "1"}, project={"wipe_tower_x": "100", "wipe_tower_y": "100"})
    tower = j._wipe_tower_estimate()
    assert tower is not None
    x, y, w, d = tower
    assert w > 20 and d > 5
    placements = j.arrange()
    for f in footprints(placements, objs):
        assert not overlap(f, (x, y, x + w, y + d)), (f, tower)
    # one filament: no tower, nothing reserved
    assert job_with(objs).__class__ is sc.SliceJob and job_with(objs)._wipe_tower_estimate() is None


def test_no_tower_when_printing_by_object_or_with_the_prime_tower_off():
    objs = stack(2)
    assert job_with(objs, filaments=2, process_extra={"print_sequence": "by object"})._wipe_tower_estimate() is None
    assert job_with(objs, filaments=2, process_extra={"enable_prime_tower": "0"})._wipe_tower_estimate() is None


def test_spacing_is_honoured():
    objs = stack(3, size=30.0)
    spaced = footprints(job_with(objs).arrange(spacing_mm=12.0), objs)
    for i in range(3):
        for k in range(i + 1, 3):
            assert not overlap(spaced[i], spaced[k], gap=11.0), (i, k)
    with pytest.raises(ValueError):
        job_with(objs).arrange(spacing_mm=-1.0)


def test_rotation_is_only_used_when_allowed():
    objs = [(f"o{i}", *box(125.0, 125.0, size=30.0)) for i in range(5)]
    still = job_with(objs).arrange(allow_rotation=False)
    assert all(abs(p["rotation_z"]) < 1e-9 for p in still)
    for p in job_with(objs).arrange(allow_rotation=True):
        t = p["transform"]
        assert abs(t[0, 0] - np.cos(p["rotation_z"])) < 1e-9 and abs(t[1, 0] - np.sin(p["rotation_z"])) < 1e-9


def test_the_placement_transform_moves_the_object_to_its_translation():
    objs = stack(2)
    for p in job_with(objs).arrange():
        t = p["transform"]
        _, v, _ = objs[p["index"]]
        centre = np.array([*v[:, :2].mean(axis=0), 0.0])  # the cube's centre in the bed frame (z unchanged)
        moved = t @ np.append(centre, 1.0)
        assert abs(moved[2] - centre[2]) < 1e-9  # arrange never moves in z
        assert np.allclose(p["translation"], t[:3, 3])


def test_arrange_leaves_the_job_unchanged_and_sliceable():
    objs = stack(2)
    j = job_with(objs)
    before = j.validate()
    j.arrange()
    assert j.validate() == before
    r = j.run()
    assert r.objects == ["o0", "o1"]


def test_arrange_takes_the_engine_lock_and_releases_it():
    objs = stack(2)
    running = cube_case.build_job(sc)
    running.start()
    with pytest.raises(sc.Busy):
        job_with(objs).arrange()
    running.cancel()
    try:
        running.result()
    except sc.Cancelled:
        pass
    job_with(objs).arrange()  # free again


def test_arrange_is_only_valid_when_idle_and_configured():
    j = job_with(stack(1))
    j.start()
    with pytest.raises(sc.StateError):
        j.arrange()
    j.result()
    with pytest.raises(sc.StateError):
        j.arrange()
    with pytest.raises(sc.StateError):
        sc.SliceJob().arrange()


def test_arrange_with_no_objects_returns_nothing():
    assert job_with([]).arrange() == []
