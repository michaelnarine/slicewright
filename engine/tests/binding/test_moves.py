# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 5: the structure-of-arrays moves, layers and line index (04 sections 5.1 to 5.3, 02 section 5.7)."""
import gc
import weakref

import numpy as np
import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402
from test_add_object import config  # noqa: E402
from test_validating_errors import box  # noqa: E402

NATIVE = sc._native


def cube_result(chunk=0):
    NATIVE._set_move_chunk(chunk)
    try:
        return cube_case.build_job(sc).run()
    finally:
        NATIVE._set_move_chunk(0)


def test_chunk_boundaries_do_not_change_the_arrays():
    whole = cube_result()
    for chunk in (1, 7, 1000, 4096):
        part = cube_result(chunk)
        assert set(part.moves) == set(whole.moves)
        for key in whole.moves:
            assert np.array_equal(part.moves[key], whole.moves[key]), (chunk, key)
        for key in whole.layers:
            assert np.array_equal(part.layers[key], whole.layers[key]), (chunk, key)


def test_arrays_share_one_buffer_and_outlive_the_result_and_the_job():
    job = cube_case.build_job(sc)
    result = job.run()
    moves = result.moves
    position = moves["position"]
    layer_z = result.layers["z"]
    ends = result.gcode_line_ends
    expected = position.copy()
    ref = weakref.ref(result)
    del result, job, moves
    gc.collect()
    assert ref() is None
    # the arrays still read the shared native buffer (a freed buffer would show garbage or crash under ASan)
    assert np.array_equal(position, expected)
    assert layer_z.shape[0] > 0 and int(ends[-1]) > 0
    assert position.flags.writeable is False and not position.flags.owndata


def test_every_access_gives_read_only_views_of_the_same_memory():
    result = cube_case.build_job(sc).run()
    a, b = result.moves["position"], result.moves["position"]
    assert a.__array_interface__["data"][0] == b.__array_interface__["data"][0]
    for arr in list(result.moves.values()) + list(result.layers.values()):
        assert arr.flags.writeable is False


def test_single_filament_single_nozzle_values():
    r = cube_result()
    m = r.moves
    assert set(np.unique(m["filament"]).tolist()) <= {0, 255}
    assert (m["nozzle"] == 0).all()
    assert int(m["type"].max()) < 16 and int(m["role"].max()) < 32


def test_two_filaments_use_both_and_the_nozzle_map():
    j = sc.SliceJob()
    j.set_config(config(2))
    cx, cy = cube_case.bed_centre(cube_case.profiles()["machine"])
    j.add_object("a", *box(cx - 30, cy))
    j.add_object("b", *box(cx + 30, cy), extruder=2)
    r = j.run()
    m = r.moves
    used = set(np.unique(m["filament"]).tolist()) - {255}
    assert used == {0, 1}
    assert (m["nozzle"] == 0).all()  # one physical nozzle: both filaments map to it
    ext = m["type"] == sc.enums()["move_type"]["Extrude"]
    by_object = {int(o): set(np.unique(m["filament"][ext & (m["object_id"] == o)]).tolist()) for o in (0, 1)}
    assert by_object[0] == {0} and by_object[1] == {1}


def test_object_ids_follow_add_object_order():
    j = sc.SliceJob()
    cx, cy = cube_case.bed_centre(cube_case.profiles()["machine"])
    j.set_config(config(1))
    j.add_object("left", *box(cx - 60, cy))
    j.add_object("middle", *box(cx, cy, size=14.0))
    j.add_object("right", *box(cx + 60, cy, size=8.0))
    r = j.run()
    m = r.moves
    ext = m["type"] == sc.enums()["move_type"]["Extrude"]
    xs = m["position"][:, 0]
    for oid, centre in ((0, cx - 60), (1, cx), (2, cx + 60)):
        sel = ext & (m["object_id"] == oid)
        assert sel.any(), oid
        assert abs(float(xs[sel].mean()) - centre) < 8.0, (oid, float(xs[sel].mean()))
    assert set(np.unique(m["object_id"]).tolist()) <= {-1, 0, 1, 2}


def test_gcode_line_points_at_the_move_in_the_file():
    r = cube_result()
    text = open(r.gcode_path, "rb").read()
    ends = r.gcode_line_ends.astype(np.int64)
    m = r.moves
    ext = np.where(m["type"] == sc.enums()["move_type"]["Extrude"])[0]
    assert (m["gcode_line"] >= 1).all() and (m["gcode_line"] <= len(ends)).all()
    for i in ext[:: max(1, len(ext) // 200)]:
        g = int(m["gcode_line"][i])
        line = text[(ends[g - 2] if g > 1 else 0):ends[g - 1]]
        assert line.startswith((b"G1", b"G2", b"G3")), (i, g, line[:40])


def test_layers_first_z_is_the_first_layer_height():
    r = cube_result()
    z = r.layers["z"]
    assert abs(float(z[0]) - 0.2) < 1e-3 and (np.diff(z) > 0).all()
    # the start G-code is part of layer 0, not a layer of its own
    assert int(r.layers["first"][0]) == 0 and int(r.moves["layer_id"][0]) == 0


def test_by_object_printing_restarts_z_per_object():
    p = cube_case.profiles()
    flat = sc.normalize_config(sc.compose_config(p["machine"], {**p["process"], "print_sequence": "by object"}, [p["filament"]]))["config"]
    j = sc.SliceJob()
    j.set_config(flat)
    cx, cy = cube_case.bed_centre(p["machine"])
    j.add_object("a", *box(cx - 40, cy, size=10.0))
    j.add_object("b", *box(cx + 40, cy, size=10.0))
    r = j.run()
    z = r.layers["z"]
    assert (np.diff(z) < 0).any(), "z should restart for the second object"
    first, last = r.layers["first"].astype(np.int64), r.layers["last"].astype(np.int64)
    k = len(r.moves["type"])
    assert first[0] == 0 and last[-1] == k - 1 and (first[1:] == last[:-1] + 1).all()
    lid = r.moves["layer_id"].astype(np.int64)
    idx = np.arange(k)
    assert (first[lid] <= idx).all() and (idx <= last[lid]).all()


def test_the_start_gcode_and_travel_have_no_object():
    r = cube_result()
    m = r.moves
    assert m["object_id"][0] == -1
    assert (m["object_id"][m["type"] == sc.enums()["move_type"]["Extrude"]] == 0).any()
