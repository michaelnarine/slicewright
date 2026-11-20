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
