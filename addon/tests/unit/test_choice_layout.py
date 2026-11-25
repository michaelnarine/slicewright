# SPDX-License-Identifier: GPL-3.0-or-later
"""The segmented-or-dropdown rule for choice settings."""
import fake_engine
import pytest

from slicewright.core import choice_layout as cl
from slicewright.core import config_codec as cc


def test_up_to_three_short_options_are_segmented():
    assert cl.choose(["Tubes", "Lines"]) == cl.SEGMENTED
    assert cl.choose(["Hidden", "Ghost", "Shown"]) == cl.SEGMENTED


def test_four_options_are_a_dropdown_even_when_short():
    assert cl.choose(["A", "B", "C"]) == cl.SEGMENTED
    assert cl.choose(["A", "B", "C", "D"]) == cl.DROPDOWN


def test_few_options_with_long_labels_are_a_dropdown():
    assert cl.choose(["x" * cl.MAX_LABEL_CHARS, "y"]) == cl.SEGMENTED
    assert cl.choose(["x" * (cl.MAX_LABEL_CHARS + 1), "y"]) == cl.DROPDOWN
    assert cl.choose(["Everywhere", "Top and bottom surfaces", "Nowhere"]) == cl.DROPDOWN


def test_labels_that_do_not_fit_the_width_are_a_dropdown():
    labels = ["Displacement", "Extrusion", "Combined"]
    assert cl.choose(labels, 60) == cl.SEGMENTED
    assert cl.choose(labels, 20) == cl.DROPDOWN
    assert cl.choose(["Tubes", "Lines"], 5) == cl.DROPDOWN


def test_a_wider_area_allows_what_the_sidebar_does_not():
    labels = ["Displacement", "Extrusion", "Combined"]
    assert cl.choose(labels, cl.DEFAULT_WIDTH_CHARS) == cl.DROPDOWN
    assert cl.choose(labels, cl.width_chars(900)) == cl.SEGMENTED


def test_empty_and_single_option_lists_are_dropdowns():
    assert cl.choose([]) == cl.DROPDOWN
    assert cl.choose(["Only"]) == cl.DROPDOWN


def test_width_chars_from_a_region():
    assert cl.width_chars(None) == cl.DEFAULT_WIDTH_CHARS
    assert cl.width_chars(0) == cl.DEFAULT_WIDTH_CHARS
    assert cl.width_chars(600) > cl.width_chars(300) > 0
    assert cl.width_chars(600, 2.0) < cl.width_chars(600, 1.0)      # a bigger UI scale fits fewer characters
    assert cl.width_chars(10) == 0


def _enum_labels():
    schema = fake_engine.config_schema()
    return {k: [i[1] for i in cc.spec_for(k, e).enum_items] for k, e in schema.items()
            if cc.spec_for(k, e).kind == cc.ENUM}


def test_every_enum_of_the_schema_gets_an_answer():
    enums = _enum_labels()
    assert enums
    for key, labels in enums.items():
        assert cl.choose(labels) in (cl.SEGMENTED, cl.DROPDOWN), key
        if len(labels) > cl.MAX_OPTIONS:
            assert cl.choose(labels) == cl.DROPDOWN, key
