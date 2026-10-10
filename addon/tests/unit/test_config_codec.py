# SPDX-License-Identifier: GPL-3.0-or-later
"""Schema entry -> property spec and FlatConfig text round trips (03 section 2.3)."""
import fake_engine
import pytest

from slicewright.core import config_codec as cc

SCHEMA = fake_engine.config_schema()


def spec(key):
    return cc.spec_for(key, SCHEMA[key])


def test_every_fake_schema_key_gets_a_spec_and_round_trips_its_default():
    for key, entry in SCHEMA.items():
        s = cc.spec_for(key, entry)
        text = cc.format_value(s, s.default)
        assert cc.parse_value(s, text) == s.default, key
        if s.kind in (cc.BOOL, cc.PERCENT, cc.INT) or s.schema_type == "float":
            canon = fake_engine.normalize_config({key: entry["default"]})["config"][key]
            assert text == canon, (key, text, canon)       # the same serialisation the engine uses


@pytest.mark.parametrize("key,kind", [
    ("enable_support", cc.BOOL), ("wall_loops", cc.INT), ("layer_height", cc.FLOAT),
    ("sparse_infill_density", cc.PERCENT), ("sparse_infill_pattern", cc.ENUM),
    ("nozzle_diameter", cc.STRING), ("printable_area", cc.STRING), ("filename_format", cc.STRING),
    ("filament_colour", cc.STRING)])
def test_schema_type_decides_the_property_kind(key, kind):
    assert spec(key).kind == kind


def test_numeric_limits_become_soft_limits_and_enums_keep_order():
    s = spec("layer_height")
    assert (s.soft_min, s.soft_max, s.default) == (0.01, 1.0, 0.2)
    assert [i[0] for i in spec("sparse_infill_pattern").enum_items][:4] == ["grid", "gyroid", "line", "cubic"]
    assert spec("sparse_infill_pattern").default == "grid"
    assert spec("sparse_infill_density").default == 15.0


def test_open_enums_and_odd_defaults_fall_back_to_strings():
    assert cc.spec_for("k", {"type": "enum", "enum": [{"value": "a", "label": "A"}],
                             "enum_open": True, "default": "a"}).kind == cc.STRING
    assert cc.spec_for("k", {"type": "enum", "enum": None, "default": "a"}).kind == cc.STRING
    bad = cc.spec_for("k", {"type": "float", "default": "abc"})
    assert (bad.kind, bad.default) == (cc.STRING, "abc")
    assert cc.spec_for("k", {"type": "enum", "enum": [{"value": "a"}, {"value": "b"}],
                             "default": "zzz"}).default == "a"
    assert cc.spec_for("k", {"type": "float", "default": ""}).default == 0.0


def test_the_description_appends_the_unit_once():
    assert spec("layer_height").description == "Layer height (mm)"
    s = cc.spec_for("k", {"type": "float", "tooltip": "Speed in mm/s", "sidetext": "mm/s", "default": "1"})
    assert s.description == "Speed in mm/s"


@pytest.mark.parametrize("key,value,text", [
    ("enable_support", True, "1"), ("enable_support", False, "0"), ("wall_loops", 3, "3"),
    ("layer_height", 0.2, "0.2"), ("layer_height", 0.125, "0.125"),
    ("sparse_infill_density", 15.0, "15%"), ("sparse_infill_density", 12.5, "12.5%"),
    ("sparse_infill_pattern", "gyroid", "gyroid"), ("filename_format", "{a}_{b}", "{a}_{b}")])
def test_format_and_parse_round_trip(key, value, text):
    s = spec(key)
    assert cc.format_value(s, value) == text
    assert cc.parse_value(s, text) == value


def test_parse_accepts_alternative_spellings_and_rejects_bad_values():
    assert cc.parse_value(spec("enable_support"), "true") is True
    assert cc.parse_value(spec("sparse_infill_density"), "20") == 20.0
    assert cc.parse_value(spec("wall_loops"), "3.0") == 3
    for key, bad in [("enable_support", "maybe"), ("wall_loops", "x"), ("layer_height", ""),
                     ("sparse_infill_pattern", "nope")]:
        with pytest.raises(ValueError, match=key):
            cc.parse_value(spec(key), bad)


def test_flatten_joins_vectors_the_way_orca_serialises_them():
    assert cc.flatten("floats", ["0.4", "0.6"]) == "0.4,0.6"
    assert cc.flatten("points", ["0x0", "220x0"]) == "0x0,220x0"
    assert cc.flatten("strings", ["PLA", "PETG"]) == '"PLA";"PETG"'
    assert cc.flatten("float", "0.2") == "0.2"
    # and the engine's normalisation accepts what flatten produces
    flat = {"nozzle_diameter": cc.flatten("floats", ["0.4"]),
            "printable_area": cc.flatten("points", ["0x0", "220x0", "220x220", "0x220"])}
    assert fake_engine.normalize_config(flat)["errors"] == {}


def test_split_vector():
    assert cc.split_vector("floats", "0.4, 0.6") == ["0.4", "0.6"]
    assert cc.split_vector("strings", '"PLA";"PETG"') == ["PLA", "PETG"]
    assert cc.split_vector("floats", "") == []
