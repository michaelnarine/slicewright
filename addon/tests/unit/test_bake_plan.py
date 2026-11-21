# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake planning (03 section 7.11, ``core/bake_plan.py``): estimates, limits, names, materials."""
from __future__ import annotations

import pytest

from fake_engine.api import ROLES, enums
from slicewright.core import bake, bake_plan as bp


def test_estimates_count_points_and_vertices_and_grow_with_sides():
    c = bp.estimate(bp.CURVES, 1000, 10)
    assert (c.vertices, c.faces, c.paths) == (1010, 0, 10)
    m6, m8 = bp.estimate(bp.MESH, 1000, 10, 6), bp.estimate(bp.MESH, 1000, 10, 8)
    assert (m6.vertices, m6.faces) == (12_000, 6_020) and m8.vertices == 16_000
    assert m8.nbytes > m6.nbytes > c.nbytes
    with pytest.raises(ValueError):
        bp.estimate("RIBBON", 1, 1)


def test_levels_ok_confirm_refuse_follow_the_byte_thresholds():
    small = bp.estimate(bp.MESH, 10_000, 100)
    assert small.level == "ok" and "vertices" in small.describe() and bp.verdict_text(small) == small.describe()
    big = bp.estimate(bp.MESH, 1_000_000, 10_000)
    assert big.level == "confirm" and "120,000" not in bp.verdict_text(big) and "12,000,000 vertices" in bp.verdict_text(big)
    huge = bp.estimate(bp.MESH, 10_000_000, 100_000)
    assert huge.level == "refuse" and "too large" in bp.verdict_text(huge) and "curves" in bp.verdict_text(huge)
    assert bp.estimate(bp.CURVES, 5_000_000, 50_000).level == "ok"            # curves are cheap: ~270 MB
    assert bp.estimate(bp.CURVES, 20_000_000, 100_000).level == "confirm"


def test_the_thresholds_sit_where_the_doc_says():
    """About 0.7M hexagonal-prism segments ask first; a few million are refused (03 7.11)."""
    per = bp.estimate(bp.MESH, 1000, 0).nbytes / 1000
    assert per == pytest.approx(696)
    assert 600_000 < bp.CONFIRM_BYTES / per < 900_000
    assert bp.REFUSE_BYTES / per > 5_000_000


def test_names_are_cut_on_a_character_boundary_and_numbered_for_parts():
    assert bp.object_name("Cube", bp.CURVES) == "Cube Bake (curves)"
    assert bp.object_name("Cube", bp.MESH) == "Cube Bake (mesh)"
    assert bp.object_name("Cube", bp.MESH, 1, 12) == "Cube Bake (mesh 02)"
    long = "ü" * 300
    cut = bp.truncate_name(long)
    assert len(cut.encode()) <= bp.MAX_NAME_BYTES and cut == "ü" * (bp.MAX_NAME_BYTES // 2)
    assert len(bp.object_name(long, bp.MESH, 99, 100).encode()) <= 255
    assert bp.truncate_name("short") == "short"


def test_source_names_come_from_the_results_objects_and_the_rest_is_the_extras():
    assert bp.source_name(["A", "B#2"], 1) == "B#2"
    assert bp.source_name(["A"], -1) == bp.EXTRAS_NAME and bp.source_name(["A"], 5) == bp.EXTRAS_NAME
    assert bp.source_name([""], 0) == bp.EXTRAS_NAME


def test_materials_use_the_previews_names_and_colours():
    names = bp.role_names_of(enums())
    assert names[ROLES["ExternalPerimeter"]] == "ExternalPerimeter"
    assert bp.feature_label(ROLES["ExternalPerimeter"], names) == "Outer wall"
    assert bp.feature_label(bake.TRAVEL_KEY, names) == "Travel"
    assert bp.feature_label(30, names) == "Feature 30"
    assert bp.material_name(ROLES["Perimeter"], names) == "Slicewright Inner wall"
    assert bp.feature_color(ROLES["Perimeter"], names) == pytest.approx((1.0, 0.9019608, 0.3019608))
    assert bp.feature_color(30, names) == pytest.approx((0.5019608,) * 3)
    assert bp.feature_names([1, bake.TRAVEL_KEY], names) == {"1": "Inner wall", "32": "Travel"}
