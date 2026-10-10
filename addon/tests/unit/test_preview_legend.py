# SPDX-License-Identifier: GPL-3.0-or-later
"""Legend rows and summary from real ``from_gcode`` stats; legend times add up to the total."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fake_engine import api
from fake_engine.gcode import from_gcode
from slicewright.core import preview_legend as lg

GCODE = Path(__file__).resolve().parents[1] / "fixtures" / "gcode"
sys.path.insert(0, str(GCODE))
import make_large  # noqa: E402

ROLES = api.enums()["role"]


@pytest.fixture(scope="module")
def stats(tmp_path_factory):
    path = tmp_path_factory.mktemp("g") / "p.gcode"
    lines = ["; filament_diameter = 1.75", "M83", "G90"]
    for layer in range(4):
        lines += list(make_large.layer_lines(layer, n=2000))
    path.write_text("\n".join(lines) + "\n")
    return from_gcode(str(path)).stats


def test_legend_times_and_percentages_add_up(stats):
    rows = lg.legend_rows(stats, ROLES)
    assert {r.role for r in rows} == set(stats["time_by_role_s"])
    assert [r.role_id for r in rows] == sorted(r.role_id for r in rows)
    assert sum(r.percent for r in rows) == pytest.approx(100.0)
    role_total = sum(r.time_s for r in rows)
    assert role_total == pytest.approx(sum(v[0] for v in stats["time_by_role_s"].values()))
    # per-role plus per-move-type time is the total print time (04 5.4: within 1 %)
    assert lg.accounted_time(stats) == pytest.approx(stats["time_s"]["normal"], rel=0.01)
    assert role_total <= stats["time_s"]["normal"]
    assert sum(r.filament_g for r in rows) == pytest.approx(
        sum(v["g"] for v in stats["used_filament_per_role"].values()))


def test_rows_carry_appendix_a_labels_and_colours(stats):
    by = {r.role: r for r in lg.legend_rows(stats, ROLES)}
    assert by["ExternalPerimeter"].label == "Outer wall" and by["ExternalPerimeter"].color == 0xFF7D38
    assert by["InternalInfill"].color == 0xB03029


def test_silent_mode_uses_the_second_time_column(stats):
    normal = sum(r.time_s for r in lg.legend_rows(stats, ROLES))
    silent = sum(r.time_s for r in lg.legend_rows(stats, ROLES, silent=True))
    assert silent > normal


def test_unknown_role_and_empty_stats_do_not_crash():
    odd = {"time_by_role_s": {"Mystery": [3.0, 4.0]}, "used_filament_per_role": {}}
    row = lg.legend_rows(odd, ROLES)[0]
    assert row.label == "Mystery" and row.percent == 100.0 and row.role_id == 31
    assert lg.legend_rows({}, ROLES) == []


def test_summary_lines_and_duration_format(stats):
    text = "\n".join(lg.summary_lines(stats))
    assert "Time:" in text and "Layers: 4" in text and "Filament 1:" in text and "Travel:" in text
    assert lg.format_duration(45) == "45s" and lg.format_duration(125) == "2m 05s"
    assert lg.format_duration(7500) == "2h 05m"


def test_chip_and_gradient_pixels_have_the_right_size():
    assert len(lg.chip_pixels(0xFF0000, 4)) == 4 * 4 * 4 and lg.chip_pixels(0xFF0000, 1) == [1.0, 0.0, 0.0, 1.0]
    g = lg.gradient_pixels(8, 2)
    assert len(g) == 8 * 2 * 4 and g[:3] == list(lg.pp.rgb(lg.pp.RANGE_COLORS[0]))
