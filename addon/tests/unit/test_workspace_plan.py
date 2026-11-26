# SPDX-License-Identifier: GPL-3.0-or-later
"""The Slicer workspace decisions and layout data (03 section 1.3), without Blender."""
from slicewright.core import workspace_plan as wp


def test_a_free_name_is_created_ours_is_reused_a_foreign_one_is_refused():
    assert wp.add_decision(["Layout"], []).action == wp.CREATE
    assert wp.add_decision(["Layout", "Slicer"], ["Slicer"]).action == wp.ACTIVATE
    foreign = wp.add_decision(["Layout", "Slicer"], [])
    assert foreign.action == wp.REFUSE and "not made by Slicewright" in foreign.message


def test_only_tagged_workspaces_may_be_removed():
    assert wp.remove_targets(["Layout", "Slicer", "Modeling"], ["Slicer"]) == ["Slicer"]
    assert wp.remove_targets(["Layout", "Slicer"], []) == []


def test_removal_falls_back_to_layout_then_any_other():
    assert wp.fallback_workspace(["Animation", "Layout", "Slicer"], ["Slicer"]) == "Layout"
    assert wp.fallback_workspace(["Animation", "Slicer"], ["Slicer"]) == "Animation"
    assert wp.fallback_workspace(["Slicer"], ["Slicer"]) is None


def test_the_base_is_layout_or_the_current_workspace_never_ours():
    assert wp.base_workspace(["Layout", "Modeling"], [], "Modeling") == "Layout"
    assert wp.base_workspace(["Modeling", "Slicer"], ["Slicer"], "Slicer") == "Modeling"
    assert wp.base_workspace(["Modeling"], [], "Modeling") == "Modeling"


def test_join_plan_reduces_blenders_default_layout_to_one_area():
    rects = [(2843, 47, 609, 1631), (2843, 1685, 609, 366), (4, 47, 2832, 143), (4, 197, 2832, 1854)]
    assert len(wp.join_plan(rects)) == 3


def test_join_plan_stops_when_nothing_is_adjacent():
    assert wp.join_plan([(0, 0, 100, 100), (500, 500, 100, 100)]) == []
    assert wp.join_plan([(0, 0, 100, 100)]) == []


def test_join_plan_handles_an_l_shaped_arrangement():
    rects = [(0, 0, 400, 1000), (410, 0, 600, 300), (410, 310, 600, 690)]
    assert len(wp.join_plan(rects)) == 2


def test_the_layout_is_consistent():
    assert wp.layout_problems() == []
    assert wp.final_keys() == set(wp.AREA_TYPES)


def test_layout_problems_are_reported():
    bad = (wp.Split("nope", wp.VERTICAL, 0.5, "a", "b"), wp.Split(wp.ROOT, "DIAGONAL", 0.99, "c", "d"))
    problems = wp.layout_problems(bad, {})
    assert any("unknown area" in p for p in problems) and any("direction" in p for p in problems)
    assert any("out of range" in p for p in problems) and any("no area type" in p for p in problems)
