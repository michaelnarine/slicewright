# SPDX-License-Identifier: GPL-3.0-or-later
"""The numpy-backed move table and the large-file generator that feeds the preview benchmarks."""
from __future__ import annotations

import sys
from array import array
from pathlib import Path

import numpy as np

from fake_engine.gcode import from_gcode
from fake_engine.movetable import MoveTable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures" / "gcode"))
import make_large  # noqa: E402


def test_columns_are_compact_buffers_not_row_tuples():
    assert array("f").itemsize == 4 and array("I").itemsize == 4 and array("i").itemsize == 4
    mt = MoveTable()
    for i in range(1000):
        mt.add("Extrude", i, 0, 0.2, role="Perimeter", width=0.4, height=0.2, duration=0.01)
    m = mt.arrays()
    assert len(mt) == 1000 and m["position"].shape == (1000, 3)
    assert m["position"][:, 0].tolist() == list(range(1000))
    assert sum(a.nbytes for a in m.values()) <= 1000 * 100     # a few bytes per value, no objects


def test_arrays_of_an_empty_table_have_the_right_shapes():
    m = MoveTable().arrays()
    assert m["position"].shape == (0, 3) and m["time"].shape == (0, 2)
    assert m["type"].dtype == np.uint8


def test_generated_large_file_round_trips_through_from_gcode(tmp_path):
    path = tmp_path / "large.gcode"
    lines = ["; filament_diameter = 1.75", "M83", "G90"]
    for layer in range(3):
        lines += list(make_large.layer_lines(layer, n=400))
    path.write_text("\n".join(lines) + "\n")
    r = from_gcode(str(path))
    assert len(r.layers["z"]) == 3
    assert abs(len(r.moves["type"]) - 1200) < 60
    assert int(r.layers["last"][-1]) == len(r.moves["type"]) - 1
    assert r.gcode_line_ends[-1] == path.stat().st_size
    assert set(r.stats["time_by_role_s"]) >= {"ExternalPerimeter", "Perimeter", "InternalInfill"}
