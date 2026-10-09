# SPDX-License-Identifier: GPL-3.0-or-later
"""MoveTable, layer building and stats, and SliceResult basics, built from hand-made moves."""
from __future__ import annotations

import gc
import os
import zipfile

import numpy as np
import pytest
from fake_engine.api import temp_dir
from fake_engine.config import defaults
from fake_engine.movetable import MoveTable, build_layers, build_stats, renumber_layers
from fake_engine.result import SliceResult, format_time, png_bytes


def make_table() -> MoveTable:
    mt = MoveTable()
    mt.add("Noop", 0, 0, 0, layer_id=7)
    mt.add("Travel", 10, 0, 0.2, feedrate=100, duration=0.1, layer_id=7, print_z=0.2)
    mt.add("Extrude", 20, 0, 0.2, role="ExternalPerimeter", width=0.4, height=0.2,
           mm3_per_mm=0.08, feedrate=50, duration=0.2, layer_id=7, print_z=0.2)
    mt.add("Retract", 20, 0, 0.2, duration=0.03, layer_id=7)
    mt.add("Travel", 20, 0, 0.4, duration=0.05, layer_id=9, print_z=0.4)
    mt.add("Extrude", 30, 0, 0.4, role="InternalInfill", mm3_per_mm=0.08, feedrate=50,
           duration=0.2, layer_id=9, print_z=0.4)
    return mt


def test_arrays_have_the_documented_dtypes_and_shapes():
    m = make_table().arrays()
    assert m["position"].shape == (6, 3) and m["position"].dtype == np.float32
    assert m["time"].shape == (6, 2) and m["time"].dtype == np.float32
    assert m["type"].dtype == np.uint8 and m["layer_id"].dtype == np.uint32
    assert m["object_id"].dtype == np.int32 and m["gcode_line"].dtype == np.uint32
    assert (m["time"][:, 1] >= m["time"][:, 0]).all()        # silent mode is never faster


def test_renumber_layers_makes_ids_contiguous():
    ids = np.array([7, 7, 7, 9, 9, 3], np.uint32)
    assert renumber_layers(ids).tolist() == [0, 0, 0, 1, 1, 2]
    assert renumber_layers(np.zeros(0, np.uint32)).tolist() == []


def test_build_layers_ranges_cover_every_move():
    m = make_table().arrays()
    layers = build_layers(m)
    assert m["layer_id"].tolist() == [0, 0, 0, 0, 1, 1]
    assert layers["first"].tolist() == [0, 4] and layers["last"].tolist() == [3, 5]
    assert np.allclose(layers["z"], [0.2, 0.4])


def test_build_layers_of_an_empty_table():
    layers = build_layers(MoveTable().arrays())
    assert len(layers["z"]) == 0


def test_stats_time_volume_and_travel():
    m = make_table().arrays()
    layers = build_layers(m)
    s = build_stats(m, layer_count=len(layers["z"]))
    assert s["time_s"]["normal"] == pytest.approx(0.58, abs=1e-6)
    assert set(s["time_by_role_s"]) == {"ExternalPerimeter", "InternalInfill"}
    assert set(s["time_by_move_type_s"]) == {"Travel", "Retract"}
    parts = (sum(v[0] for v in s["time_by_role_s"].values())
             + sum(v[0] for v in s["time_by_move_type_s"].values()))
    assert parts == pytest.approx(s["time_s"]["normal"])
    assert s["total_travel_mm"] == pytest.approx(10.202, abs=1e-3)
    assert s["filament_per_extruder"][0]["cm3"] * 1000 == pytest.approx(2 * 10 * 0.08, rel=1e-5)
    assert s["layer_count"] == 2


def _result(tmp_path, thumbs=()):
    m = make_table().arrays()
    layers = build_layers(m)
    path = os.path.join(temp_dir(), "unit_result.gcode")
    with open(path, "w") as f:
        f.write("G1 X1\n" * 6)
    return SliceResult(
        gcode_path=path, moves=m, layers=layers,
        gcode_line_ends=np.arange(1, 7, dtype=np.uint64) * 6, stats=build_stats(m, layer_count=2),
        warnings=[], objects=["cube"], wipe_tower=None, config=defaults(), thumbnails=list(thumbs))


def test_result_arrays_are_read_only(tmp_path):
    r = _result(tmp_path)
    for a in (*r.moves.values(), *r.layers.values(), r.gcode_line_ends):
        assert not a.flags.writeable


def test_result_removes_its_gcode_file_when_freed(tmp_path):
    r = _result(tmp_path)
    path = r.gcode_path
    del r
    gc.collect()
    assert not os.path.exists(path)


def test_write_gcode_copies_bytes(tmp_path):
    r = _result(tmp_path)
    r.write_gcode(str(tmp_path / "out.gcode"))
    assert (tmp_path / "out.gcode").read_bytes() == b"G1 X1\n" * 6


def test_gcode_3mf_contains_the_bambu_parts_and_the_largest_thumbnail(tmp_path):
    thumbs = [np.zeros((8, 8, 4), np.uint8), np.full((16, 16, 4), 255, np.uint8)]
    r = _result(tmp_path, thumbs)
    r.write_gcode_3mf(str(tmp_path / "p.gcode.3mf"), {"plate_name": "Bed"})
    z = zipfile.ZipFile(tmp_path / "p.gcode.3mf")
    names = set(z.namelist())
    assert {"Metadata/plate_1.gcode", "Metadata/plate_1.gcode.md5", "Metadata/slice_info.config",
            "Metadata/model_settings.config", "Metadata/project_settings.config",
            "Metadata/plate_1.png"} <= names
    assert z.read("Metadata/plate_1.png").startswith(b"\x89PNG")
    assert b"Bed" in z.read("Metadata/model_settings.config")


def test_png_encoder_writes_a_valid_header():
    data = png_bytes(np.zeros((3, 5, 4), np.uint8))
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    assert int.from_bytes(data[16:20], "big") == 5 and int.from_bytes(data[20:24], "big") == 3


@pytest.mark.parametrize("seconds,text", [(45, "45s"), (800, "13m20s"), (7980, "2h13m")])
def test_format_time(seconds, text):
    assert format_time(seconds) == text
