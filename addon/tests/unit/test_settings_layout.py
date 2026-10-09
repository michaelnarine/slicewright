# SPDX-License-Identifier: GPL-3.0-or-later
"""Pages, modes and filter (03 section 2.4)."""
import fake_engine
import pytest

from slicewright.core import settings_layout as sl

SCHEMA = fake_engine.config_schema()
LAYOUT = fake_engine.tab_layout()


def pages(role):
    return sl.build_pages(role, LAYOUT, SCHEMA)


def test_pages_follow_the_engine_layout():
    process = pages("process")
    assert [p.name for p in process[:4]] == ["Quality", "Strength", "Speed", "Support"]
    strength = sl.find_page(process, "Strength")
    assert [(g.title, g.keys) for g in strength.groups[:2]] == [
        ("Walls", ["wall_loops"]), ("Infill", ["sparse_infill_density", "sparse_infill_pattern"])]
    assert sl.find_page(pages("printer"), "Basic information").groups[0].custom == "bed_shape"


def test_keys_missing_from_the_layout_fall_back_to_their_schema_category():
    process = pages("process")
    laid_out = {k for p in LAYOUT["process"] for g in p["groups"] for k in g["keys"]}
    assert "skirt_loops" not in laid_out
    others = sl.find_page(process, "Others")                  # a category the layout has no page for
    assert sorted(k for g in others.groups for k in g.keys) == ["filename_format", "skirt_loops"]
    assert [g.title for g in others.groups] == ["Others"]
    # every process key is reachable, exactly once
    reachable = [k for p in process for k in p.keys()]
    assert sorted(reachable) == sorted(k for k, e in SCHEMA.items() if e["preset"] == "process")


def test_a_fallback_key_on_an_existing_page_gets_a_trailing_group():
    schema = {**SCHEMA, "extra_speed": {**SCHEMA["outer_wall_speed"], "label": "Extra speed"}}
    speed = sl.find_page(sl.build_pages("process", LAYOUT, schema), "Speed")
    assert [g.title for g in speed.groups] == ["Print speed", "More"]
    assert speed.groups[1].keys == ["extra_speed"]


def test_layout_keys_the_schema_dropped_are_ignored_and_empty_pages_vanish():
    layout = {"process": [{"page": "Ghost", "icon": "", "groups": [
        {"title": "g", "keys": ["no_such_key"], "wiki": {}, "custom": None}]}]}
    built = sl.build_pages("process", layout, SCHEMA)
    assert "Ghost" not in [p.name for p in built]
    assert sorted(k for p in built for k in p.keys()) == sorted(
        k for k, e in SCHEMA.items() if e["preset"] == "process")


def test_keys_of_other_roles_never_appear():
    assert not {"layer_height", "nozzle_temperature"} & {k for p in pages("printer") for k in p.keys()}
    assert "preset none" not in [p.name for p in pages("filament")]


def test_find_page_falls_back_to_the_first_page():
    process = pages("process")
    assert sl.find_page(process, "No such page") is process[0]
    assert sl.find_page([], "x") is None


@pytest.mark.parametrize("option,level,develop,shown", [
    ("simple", "simple", False, True), ("advanced", "simple", False, False),
    ("advanced", "advanced", False, True), ("expert", "advanced", False, False),
    ("expert", "expert", False, True), ("develop", "expert", False, False),
    ("develop", "expert", True, True), ("develop", "simple", True, True),
    ("surprise", "simple", False, True)])
def test_mode_allows(option, level, develop, shown):
    assert sl.mode_allows(option, level, develop) is shown


def test_visible_groups_apply_mode_and_drop_empty_groups():
    printer = sl.find_page(pages("printer"), "Basic information")
    simple = sl.visible_groups(printer, SCHEMA, "simple")
    assert [(g.title, ks) for g, ks in simple] == [
        ("Printable space", ["printable_area", "bed_exclude_area", "printable_height"]),
        ("Advanced", ["gcode_flavor"])]
    schema = {**SCHEMA, "gcode_flavor": {**SCHEMA["gcode_flavor"], "mode": "expert"}}
    assert [g.title for g, _ in sl.visible_groups(printer, schema, "simple")] == ["Printable space"]
    assert [g.title for g, _ in sl.visible_groups(printer, schema, "expert")] == [
        "Printable space", "Advanced"]


def test_a_custom_group_stays_when_its_keys_are_hidden_but_not_while_filtering():
    schema = {k: {**v, "mode": "expert"} for k, v in SCHEMA.items()}
    printer = sl.find_page(pages("printer"), "Extruder")
    assert [(g.custom, ks) for g, ks in sl.visible_groups(printer, schema, "simple")] == [
        ("extruder_count", [])]
    assert sl.visible_groups(printer, schema, "simple", "nozzle") == []


def test_filter_matches_label_key_and_tooltip_words():
    strength = sl.find_page(pages("process"), "Strength")
    keys = lambda text: [k for _, ks in sl.visible_groups(strength, SCHEMA, "expert", text) for k in ks]
    assert keys("") == ["wall_loops", "sparse_infill_density", "sparse_infill_pattern",
                        "bottom_shell_layers", "top_shell_layers"]      # the last two via the "More" group
    assert keys("INFILL") == ["sparse_infill_density", "sparse_infill_pattern"]     # label and key
    assert keys("sparse pattern") == ["sparse_infill_pattern"]                     # words in any order
    schema = {**SCHEMA, "wall_loops": {**SCHEMA["wall_loops"], "tooltip": "Number of perimeters"}}
    assert [k for _, ks in sl.visible_groups(strength, schema, "expert", "perimeters") for k in ks] == [
        "wall_loops"]
    assert keys("zzz") == [] and keys("   ") == keys("")


def test_develop_options_need_the_develop_switch():
    schema = {**SCHEMA, "wall_loops": {**SCHEMA["wall_loops"], "mode": "develop"}}
    strength = sl.find_page(sl.build_pages("process", LAYOUT, schema), "Strength")
    shown = lambda dev: [k for _, ks in sl.visible_groups(strength, schema, "expert", "", dev) for k in ks]
    assert "wall_loops" not in shown(False) and "wall_loops" in shown(True)
