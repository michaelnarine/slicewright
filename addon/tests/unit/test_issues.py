# SPDX-License-Identifier: GPL-3.0-or-later
"""The merged Issues list (``core/issues.py``) and the settings-page lookup it relies on, without Blender."""
from __future__ import annotations

import fake_engine as sc
from slicewright.core import issues
from slicewright.core import settings_layout as sl


def issue(level, code, message="m", opt_key=None, object_name=None):
    return {"level": level, "code": code, "message": message, "opt_key": opt_key, "object_name": object_name}


def test_errors_come_first_then_warnings_then_info_in_input_order():
    w1, w2 = issue("warning", "a", "w1"), issue("warning", "b", "w2")
    i1, e1, e2 = issue("info", "c", "i1"), issue("error", "d", "e1"), issue("error", "e", "e2")
    assert issues.merge([w1, i1], [e1, w2, e2]) == [e1, e2, w1, w2, i1]


def test_identical_issues_from_two_sources_appear_once():
    a = issue("warning", "mesh_open_edges", "cube has open edges", None, "cube")
    assert issues.merge([a], [dict(a)]) == [a]
    other = issue("warning", "mesh_open_edges", "cube has open edges", None, "other")
    assert issues.merge([a], [other]) == [a, other]               # a different object is a different issue


def test_the_same_object_problem_from_the_check_and_the_engine_is_one_issue_and_the_first_wins():
    engine = issue("error", "object_outside_bed", "engine text", "printable_area", "x")
    ours = issue("error", "object_outside_bed", "our text", None, "x")
    assert issues.merge([engine], [ours]) == [engine]
    assert issues.merge([ours], [engine]) == [ours]
    too_tall = issue("error", "object_too_tall", "tall", None, "x")
    assert issues.merge([engine], [ours, too_tall]) == [engine, too_tall]


def test_merge_ignores_none_lists_and_unknown_levels_sort_last():
    odd = issue("fatal", "z")
    assert issues.merge(None, [odd], [issue("info", "i")]) == [issue("info", "i"), odd]


def test_counts_and_the_summary_text():
    rows = [issue("error", "a"), issue("error", "b"), issue("warning", "c"), issue("info", "d")]
    c = issues.counts(rows)
    assert (c.errors, c.warnings, c.infos, c.total) == (2, 1, 1, 4)
    assert issues.summary(c) == "2 errors, 1 warning, 1 note"
    assert issues.summary(issues.counts([issue("error", "a")])) == "1 error"
    assert issues.summary(issues.counts([])) == "No issues"


def test_every_level_has_its_own_icon():
    icons = {issues.icon(level) for level in (issues.ERROR, issues.WARNING, issues.INFO)}
    assert len(icons) == 3 and issues.icon("whatever") == issues.icon(issues.INFO)


def test_base_object_name_strips_instance_and_parenthesis_suffixes():
    assert issues.base_object_name("Bolt#3") == "Bolt"
    assert issues.base_object_name("Cube.001") == "Cube.001"
    assert issues.base_object_name("Cube (Scene)") == "Cube"
    assert issues.base_object_name("A#b") == "A#b"                # only a numeric suffix is an instance index


def test_targets():
    assert issues.targets(issue("error", "x", object_name="Cube", opt_key="printable_area")) == ("Cube", "printable_area")
    assert issues.targets(issue("error", "x", opt_key="")) == (None, None)


def test_the_book_replaces_a_source_and_merges_the_sources_with_the_slice_first():
    book = issues.IssueBook()
    engine = issue("error", "object_outside_bed", "engine", None, "x")
    check = issue("error", "object_outside_bed", "check", None, "x")
    warn = issue("warning", "mesh_open_edges", "open", None, "x")
    assert book.set("check", [check, warn]) == [check, warn]
    assert book.set("slice", [engine]) == [engine, warn]          # the slice's wording wins on a duplicate
    assert book.clear("slice") == [check, warn]
    assert book.set("check", []) == []
    book.set("slice", [warn])
    assert book.clear() == [] and book.merged() == []


def test_the_book_copies_what_it_is_given():
    book = issues.IssueBook()
    row = issue("info", "x")
    book.set("slice", [row])
    row["message"] = "changed"
    assert book.merged()[0]["message"] == "m"


def _pages(role):
    return sl.build_pages(role, sc.tab_layout(), sc.config_schema())


def test_a_key_is_found_on_its_home_page_and_in_its_role():
    schema = sc.config_schema()
    assert sl.role_of_key(schema, "layer_height") == "process"
    assert sl.role_of_key(schema, "printable_area") == "printer"
    assert sl.role_of_key(schema, "filament_type") == "filament"
    assert sl.role_of_key(schema, "no_such_key") is None
    assert sl.find_key_page(_pages("process"), "layer_height").name == "Quality"
    assert sl.find_key_page(_pages("process"), "no_such_key") is None
    assert sl.find_key_page(_pages("filament"), "layer_height") is None


# -- engine-unknown keys that came from a preset (03 section 6.5) ----------------------------------------------

def unknown(key):
    return issue("warning", "unknown_key", f"unknown configuration key '{key}' was dropped", key)


def test_preset_unknown_keys_collapse_into_one_info_row_with_the_keys_in_its_detail():
    rows = [unknown("b_key"), issue("warning", "gcode_conflict", "g"), unknown("a_key"), unknown("b_key")]
    out = issues.collapse_preset_unknown_keys(rows, {"a_key", "b_key"})
    assert [i["code"] for i in out] == [issues.PRESET_KEYS_UNKNOWN, "gcode_conflict"]    # the summary takes the first one's place
    summary = out[0]
    assert summary["level"] == issues.INFO and summary["opt_key"] is None and summary["object_name"] is None
    assert summary["message"] == "2 profile settings ignored"
    assert summary["detail"] == "a_key, b_key"                                           # sorted, each key once


def test_one_demoted_key_reads_in_the_singular():
    out = issues.collapse_preset_unknown_keys([unknown("k")], ["k"])
    assert [i["message"] for i in out] == ["1 profile setting ignored"]


def test_an_unknown_key_the_user_put_there_stays_a_warning_next_to_the_summary():
    mine = unknown("typed_key")
    out = issues.collapse_preset_unknown_keys([unknown("from_preset"), mine], {"from_preset"})
    assert out[1] == mine and out[1]["level"] == issues.WARNING
    assert out[0]["code"] == issues.PRESET_KEYS_UNKNOWN and out[0]["detail"] == "from_preset"


def test_nothing_changes_without_preset_keys_and_other_issues_are_untouched():
    rows = [unknown("k"), issue("error", "validation", "bad", "layer_height")]
    assert issues.collapse_preset_unknown_keys(rows, frozenset()) == rows
    assert issues.collapse_preset_unknown_keys(rows, None) == rows
    assert issues.collapse_preset_unknown_keys(None, {"k"}) == []
    only_other = [issue("warning", "unknown_key_but_not", "m", "k")]
    assert issues.collapse_preset_unknown_keys(only_other, {"k"}) == only_other     # only the engine's code is demoted


def test_the_summary_survives_a_merge_and_sorts_with_the_notes():
    out = issues.collapse_preset_unknown_keys([unknown("k")], {"k"})
    merged = issues.merge(out, [issue("warning", "w"), issue("error", "e")])
    assert [i["level"] for i in merged] == ["error", "warning", "info"] and merged[-1]["detail"] == "k"


def test_the_ignored_keys_row_is_quiet_it_is_neither_listed_nor_counted():
    rows = issues.collapse_preset_unknown_keys([unknown("a"), unknown("b")], {"a", "b"}) + [issue("warning", "w")]
    assert [i["code"] for i in issues.visible(rows)] == ["w"]
    assert issues.counts(rows) == issues.Counts(0, 1, 0) and issues.summary(issues.counts(rows)) == "1 warning"
    assert issues.counts(issues.collapse_preset_unknown_keys([unknown("a")], {"a"})).total == 0
    assert issues.ignored_keys(rows) == ["a", "b"] and issues.ignored_keys([issue("warning", "w")]) == []
    assert issues.ignored_label(24) == "24 profile settings ignored" and issues.ignored_label(1) == "1 profile setting ignored"