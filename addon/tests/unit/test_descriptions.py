# SPDX-License-Identifier: GPL-3.0-or-later
"""Local tooltip corrections (``core/descriptions.py``): applied while the engine's text still has the flaw, never
otherwise, and used by the property spec and the settings filter."""
from slicewright.core import config_codec as cc
from slicewright.core import descriptions as d
from slicewright.core import settings_layout as sl

ORCA_MINIMIZE = ("Reduce the height of top-surface perimeters to match the model edge height.\n"
                 "Affects perimeters with a slope less than this angle (degrees).\n"
                 "A reasonable value is 35. Set to 0 to disable.")
ORCA_MIN_Z = "Minimum Z-layer height.\nAlso controls the slicing plane."


def test_the_minimize_tooltip_is_replaced_with_the_measured_direction():
    text = d.corrected("zaa_minimize_perimeter_height", ORCA_MINIMIZE)
    assert "GREATER than this angle" in text and "less than this angle" not in text
    assert "35 is the default" in text and "0 turns it off" in text
    assert "opposite" in text                                    # says why it differs from Orca's wording


def test_the_min_z_tooltip_keeps_the_engine_text_and_adds_the_notes():
    text = d.corrected("zaa_min_z", ORCA_MIN_Z)
    assert text.startswith(ORCA_MIN_Z)
    for needle in ("fixed height in mm", "does not scale with the layer height", "0.05", "0.2 mm",
                   "Bad min Z value", "0.25"):
        assert needle in text, needle


def test_a_corrected_engine_text_is_left_alone():
    fixed = "Affects perimeters with a slope greater than this angle (degrees)."
    assert d.corrected("zaa_minimize_perimeter_height", fixed) == fixed
    assert d.corrected("zaa_min_z", "Something else entirely") == "Something else entirely"


def test_other_keys_and_empty_text_pass_through():
    assert d.corrected("layer_height", "Slicing height") == "Slicing height"
    assert d.corrected("layer_height", "") == ""
    assert d.corrected("zaa_min_z", "") == ""


def test_the_property_spec_carries_the_corrected_text():
    spec = cc.spec_for("zaa_minimize_perimeter_height", {"type": "float", "label": "Minimize wall height angle",
                                                         "tooltip": ORCA_MINIMIZE, "default": "35"})
    assert "GREATER than" in spec.description and "less than this angle" not in spec.description
    spec = cc.spec_for("zaa_min_z", {"type": "float", "label": "Minimum Z height", "tooltip": ORCA_MIN_Z,
                                     "default": "0.05"})
    assert "Bad min Z value" in spec.description


def test_the_settings_filter_searches_the_corrected_text():
    entry = {"label": "Minimum Z height", "tooltip": ORCA_MIN_Z}
    assert sl.matches("zaa_min_z", entry, "scale layer height")
    assert not sl.matches("zaa_min_z", {**entry, "tooltip": ORCA_MIN_Z}, "bogus")
    assert not sl.matches("other_key", entry, "scale layer height")      # only the corrected keys gain words
