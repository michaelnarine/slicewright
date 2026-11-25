# SPDX-License-Identifier: GPL-3.0-or-later
"""The Preview option "Model": when the source objects are hidden and which filters are switched (core, no Blender)."""
from slicewright.core import preview_model as pm

K = pm.space_key
MESH, CURVE = "show_object_viewport_mesh", "show_object_viewport_curve"


def test_hidden_and_ghost_hide_only_in_preview_with_a_result():
    for model in (pm.HIDDEN, pm.GHOST):
        assert pm.wants_hide(model, "PREVIEW", True)
        assert not pm.wants_hide(model, "PREPARE", True)
        assert not pm.wants_hide(model, "PREVIEW", False)
    assert not pm.wants_hide(pm.SHOWN, "PREVIEW", True)


def test_filter_props_cover_the_plate_types_and_skip_unknown_ones():
    assert pm.filter_props(["MESH", "MESH", "FONT"]) == ("show_object_viewport_font", MESH)
    assert pm.filter_props(["LIGHT", "ARMATURE"]) == ()
    assert pm.filter_props([]) == ()


def test_record_round_trip_and_damaged_records():
    keys = {K("Layout", 3, 0, MESH), K("Modeling", 1, 0, CURVE)}
    assert pm.decode_record(pm.encode_record(keys)) == keys
    for bad in ("", "nonsense", "{}", "[1, 2]", '["no-separators"]', "null"):
        assert pm.decode_record(bad) == set()


def test_hiding_switches_off_wanted_filters_of_eligible_screens_and_records_them():
    state = {K("A", 0, 0, MESH): True, K("A", 0, 0, CURVE): True, K("B", 0, 0, MESH): True}
    off, on, keep = pm.plan(state, {"A"}, {MESH}, True, set())
    assert off == [K("A", 0, 0, MESH)] and on == []
    assert keep == {K("A", 0, 0, MESH)}


def test_a_filter_the_user_turned_off_is_not_ours_and_never_switched_on():
    mine, theirs = K("A", 0, 0, MESH), K("A", 1, 0, MESH)
    state = {mine: True, theirs: False}
    off, _on, keep = pm.plan(state, {"A"}, {MESH}, True, set())
    assert off == [mine] and keep == {mine}
    off, on, keep = pm.plan({mine: False, theirs: False}, {"A"}, set(), False, keep)
    assert off == [] and on == [mine] and keep == set()          # only ours comes back


def test_leaving_hide_restores_every_recorded_filter_and_a_type_no_longer_wanted():
    a, b = K("A", 0, 0, MESH), K("A", 0, 0, CURVE)
    state = {a: False, b: False}
    _off, on, keep = pm.plan(state, {"A"}, {MESH}, True, {a, b})
    assert on == [b] and keep == {a}                              # the curve type left the plate
    _off, on, keep = pm.plan(state, {"A"}, {MESH}, False, {a, b})
    assert sorted(on) == sorted([a, b]) and keep == set()


def test_a_screen_that_stopped_being_eligible_is_restored():
    a = K("A", 0, 0, MESH)
    _off, on, keep = pm.plan({a: False}, {"B"}, {MESH}, True, {a})
    assert on == [a] and keep == set()


def test_a_view_that_is_gone_or_already_shown_is_just_forgotten():
    gone, shown = K("A", 5, 0, MESH), K("A", 0, 0, MESH)
    off, on, keep = pm.plan({shown: True}, {"B"}, set(), False, {gone, shown})
    assert off == [] and on == [] and keep == set()


def test_planning_twice_is_a_no_op():
    state = {K("A", 0, 0, MESH): True}
    off, _on, keep = pm.plan(state, {"A"}, {MESH}, True, set())
    state = {k: False for k in off} | {k: v for k, v in state.items() if k not in off}
    off2, on2, keep2 = pm.plan(state, {"A"}, {MESH}, True, keep)
    assert off2 == [] and on2 == [] and keep2 == keep
