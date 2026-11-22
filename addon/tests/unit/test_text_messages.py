# SPDX-License-Identifier: GPL-3.0-or-later
"""``core.text`` (label wrapping) and ``core.messages`` (plain wording of the engine's messages), 03 section 6.5a."""
from slicewright.core import messages, text

ENGINE = "Object 'Far cube' is outside the printable area (printable_area minus bed_exclude_area)."


def test_wrap_keeps_every_word_and_respects_the_width():
    lines = text.wrap(ENGINE, 30)
    assert len(lines) >= 3 and all(len(line) <= 30 for line in lines)
    assert " ".join(lines) == ENGINE


def test_wrap_breaks_a_word_longer_than_a_line_and_loses_nothing():
    lines = text.wrap("bed_exclude_area_with_a_very_long_key", 12)
    assert all(len(line) <= 12 for line in lines) and "".join(lines) == "bed_exclude_area_with_a_very_long_key"


def test_wrap_handles_newlines_empty_text_and_tiny_widths():
    assert text.wrap("") == [] and text.wrap(None) == [] and text.wrap("  \n \n") == []
    assert text.wrap("one\ntwo three", 40) == ["one", "two three"]
    assert all(len(line) <= 4 for line in text.wrap("abcdefghij", 1))          # a width below 4 is raised to 4


def test_a_short_text_is_one_line():
    assert text.wrap("Preview out of date") == ["Preview out of date"]


def test_the_budget_follows_the_region_width():
    # a sidebar 561 px wide at a scale of 2 (Retina): about 42 characters, a bit fewer with an icon
    assert text.char_budget(561, 2.0) == 42 and text.char_budget(561, 2.0, icon=True) == 38
    assert text.char_budget(900, 2.0) > text.char_budget(561, 2.0) > text.char_budget(400, 2.0)
    assert text.char_budget(561, 2.0, indent=3) == text.char_budget(561, 2.0) - 3
    assert text.char_budget(560, 1.0) > text.char_budget(560, 2.0)               # the same pixels hold more at scale 1


def test_the_budget_is_bounded_and_falls_back_without_a_region():
    assert text.char_budget(None) == text.DEFAULT_CHARS == 38
    assert text.char_budget(0, 2.0) == 38 and text.char_budget(500, 0) == 38
    assert text.char_budget(10, 1.0) == text.MIN_CHARS and text.char_budget(100000, 1.0) == text.MAX_CHARS
    assert text.char_budget(None, icon=True) == 34


def issue(code, message, obj=None, key=None):
    return {"level": "error", "code": code, "message": message, "opt_key": key, "object_name": obj}


def test_an_object_outside_the_bed_is_said_plainly_with_the_name():
    i = issue("object_outside_bed", ENGINE, "Far cube")
    assert messages.plain(i) == "Far cube is outside the print area"
    assert messages.plain(issue("object_outside_bed", ENGINE)) == "Far cube is outside the print area"   # name from the text


def test_our_own_check_wording_is_rewritten_too():
    assert messages.plain(issue("object_outside_bed", "Cube is outside the printable area", "Cube")) == \
        "Cube is outside the print area"
    assert messages.plain(issue("object_outside_bed", "Cube overlaps a bed exclude area", "Cube")) == \
        "Cube overlaps a no-print zone of the bed"
    assert messages.plain(issue("object_too_tall", "Cube is taller than the printable height", "Cube")) == \
        "Cube is too tall for this printer"
    assert messages.plain(issue("validation", "printable_area is not usable", None, "printable_area")) == \
        "The printer's bed shape is not usable"


def test_unknown_messages_pass_through_unchanged_and_quoted_objects_lose_the_quotes():
    assert messages.plain(issue("engine", "something odd")) == "something odd"
    assert messages.plain(issue("slicing", "Object 'Bolt' has no layers")) == "Bolt has no layers"
    assert messages.plain({"code": "x"}) == ""


def test_the_tooltip_keeps_the_engine_text_when_the_wording_differs():
    i = issue("object_outside_bed", ENGINE, "Far cube")
    tip = messages.tooltip(i)
    assert tip.startswith("Far cube is outside the print area") and ENGINE in tip
    assert messages.tooltip(issue("engine", "something odd")) == "something odd"


def test_the_panel_order_names_every_panel_once_and_slice_comes_first_diagnostics_last():
    from slicewright import names
    assert len(set(names.PANEL_ORDER)) == len(names.PANEL_ORDER)
    assert names.PANEL_ORDER[0] == "slice" and names.PANEL_ORDER[-1] == "diagnostics"
    assert [names.panel_order(k) for k in names.PANEL_ORDER] == list(range(len(names.PANEL_ORDER)))
