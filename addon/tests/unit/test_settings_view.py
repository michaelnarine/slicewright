# SPDX-License-Identifier: GPL-3.0-or-later
"""Sections, favourites, search plans, summaries and width decisions of the Settings panel (core/settings_view)."""
import fake_engine

from slicewright.core import settings_layout as sl
from slicewright.core import settings_view as sv

SCHEMA = fake_engine.config_schema()
LAYOUT = fake_engine.tab_layout()
PROCESS = sl.build_pages("process", LAYOUT, SCHEMA)


def page(name):
    return sl.find_page(PROCESS, name)


def test_ordered_sets_toggle_and_ignore_blanks_and_repeats():
    text = sv.toggled("", "a")
    text = sv.toggled(text, "b")
    assert sv.parse_set(text) == ["a", "b"]
    assert sv.parse_set("a\n\n b \na") == ["a", "b"]
    assert sv.toggled(text, "a") == "b"
    assert sv.contains(text, "b") and not sv.contains(text, "c")
    assert sv.parse_set(sv.added("a", ["a", "x", "y"])) == ["a", "x", "y"]


def test_ids_name_the_role_page_and_group_position():
    assert sv.section_id("process", "Quality") == "process/Quality"
    assert sv.group_id("process", "Quality", 2) == "process/Quality/2"
    assert sv.group_id("process", "Quality", 2) != sv.group_id("process", "Quality", 3)


def test_pinned_keeps_pin_order_and_drops_keys_the_engine_lost():
    text = sv.join_set(["wall_loops", "gone_key", "layer_height"])
    assert sv.pinned(text, SCHEMA) == ["wall_loops", "layer_height"]


def test_visible_keys_follow_the_detail_level_and_keep_group_positions():
    simple = sv.visible_keys(page("Others"), SCHEMA, "simple")
    advanced = sv.visible_keys(page("Others"), SCHEMA, "advanced")
    keys = lambda v: [k for _i, _g, ks in v for k in ks]   # noqa: E731
    assert "skirt_loops" in keys(simple) and "filename_format" not in keys(simple)
    assert "filename_format" in keys(advanced)
    assert [i for i, _g, _k in advanced] == sorted(i for i, _g, _k in advanced)


def test_a_plan_counts_modified_keys_per_group():
    visible = sv.visible_keys(page("Strength"), SCHEMA, "advanced")
    plan = sv.plan_page(page("Strength"), visible, {"wall_loops", "sparse_infill_density", "not_here"})
    assert plan.count == sum(len(k) for _i, _g, k in visible)
    assert plan.modified == 2
    assert [g.modified for g in plan.groups[:2]] == [1, 1]


def test_search_narrows_keys_and_drops_empty_groups():
    index = sv.SearchIndex(SCHEMA)
    visible = sv.visible_keys(page("Strength"), SCHEMA, "advanced")
    plan = sv.plan_page(page("Strength"), visible, set(), index, "sparse pattern")
    assert [k for g in plan.groups for k in g.keys] == ["sparse_infill_pattern"]
    assert sv.plan_page(page("Strength"), visible, set(), index, "zzz").groups == []
    assert sv.plan_page(page("Strength"), visible, set(), index, "  ").count == sv.plan_page(page("Strength"), visible, set()).count


def test_search_matches_label_key_and_tooltip_and_caches_the_text():
    index = sv.SearchIndex(SCHEMA)
    assert index.match("wall_loops", ["wall"])
    assert index.match("wall_loops", sv.words_of("LOOPS Wall"))
    assert not index.match("wall_loops", ["nozzle_zzz"])
    assert index.haystack("wall_loops") is index.haystack("wall_loops")


def test_a_page_with_one_group_of_its_own_name_is_flat():
    notes = sv.PagePlan(sl.Page("Notes", "", [sl.Group("Notes", ["a"])]))
    assert notes.flat
    assert not sv.PagePlan(page("Strength")).flat


def test_summary_prefers_modified_then_found_then_the_count():
    assert sv.summary(12, 3) == "3 modified"
    assert sv.summary(12, 0) == "12"
    assert sv.summary(4, 0, searching=True) == "4 found"
    assert sv.summary(4, 2, searching=True) == "2 modified"


def test_columns_grow_with_the_region_width():
    assert sv.columns_for(34) == 1                        # the default sidebar
    assert sv.columns_for(sv.WIDE_CHARS - 1) == 1
    assert sv.columns_for(sv.WIDE_CHARS) == 2
    assert sv.columns_for(sv.WIDER_CHARS) == 3
    assert sv.cell_chars(34, 1) < sv.cell_chars(100, 2) + 100
    assert sv.cell_chars(0, 1) >= 8.0


def test_row_style_puts_the_label_left_when_it_fits_and_above_when_not():
    narrow = sv.cell_chars(34, 1)
    assert sv.row_style("Outer wall", False, 40).kind == sv.NATIVE
    s = sv.row_style("Layer height", False, narrow)
    assert s.kind == sv.SPLIT and 0.4 < s.factor <= sv.MAX_LABEL_SHARE
    assert sv.row_style("Slow down for better layer cooling", False, narrow).kind == sv.STACKED
    assert sv.row_style("Slow down for better layer", True, narrow).kind in (sv.INLINE, sv.STACKED)
    assert sv.row_style("Slow down for better layer", False, sv.cell_chars(120, 2)).kind != sv.STACKED


def test_choice_width_shrinks_with_columns():
    assert sv.choice_width(100, 2) < sv.choice_width(100, 1)
    assert sv.region_chars(None) == 34
