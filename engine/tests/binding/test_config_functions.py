# SPDX-License-Identifier: AGPL-3.0-only
"""Engine-side checks of the config functions that go beyond the add-on's contract suite (02 section 8, item 2).

Needs the built package on PYTHONPATH (``<build>/python``)."""
import pytest

sc = pytest.importorskip("slicewright_engine")

PRINTER = {"name": "P", "printable_area": ["0x0", "200x0", "200x200", "0x200"], "printable_height": "180",
           "nozzle_diameter": ["0.4"], "gcode_flavor": "marlin2"}
PROCESS = {"name": "Q", "layer_height": "0.16", "wall_loops": "3", "sparse_infill_pattern": "gyroid",
           "inherits": "ignored"}
FILAMENT = {"name": "F", "filament_type": ["PLA"], "filament_colour": ["#AABBCC"], "nozzle_temperature": ["215"]}


def test_schema_is_complete_and_well_typed():
    schema = sc.config_schema()
    assert len(schema) > 500
    entry = schema["sparse_infill_pattern"]
    assert entry["type"] == "enum"
    assert {e["value"] for e in entry["enum"]} >= {"gyroid", "grid"}
    assert schema["layer_height"]["scope"] in ("region", "object")
    assert schema["filament_type"]["preset"] == "filament"
    assert schema["filament_type"]["per_extruder"] is True
    assert schema["printable_area"]["per_extruder"] is False
    assert schema["nozzle_diameter"]["preset"] == "printer"
    for key, e in schema.items():
        assert isinstance(e["default"], str), key


def test_schema_defaults_deserialize():
    """Every default is accepted by normalize_config unchanged (the schema and the loader agree)."""
    schema = sc.config_schema()
    flat = {k: e["default"] for k, e in schema.items() if e["preset"] != "none"}
    out = sc.normalize_config(flat)
    assert out["errors"] == {}, out["errors"]
    assert out["issues"] == []


def test_compose_normalize_roundtrip_and_slot_colours():
    flat = sc.compose_config(PRINTER, PROCESS, [FILAMENT, {**FILAMENT, "name": "G", "filament_colour": ["#112233"]}])
    assert flat["layer_height"] == "0.16"
    assert "gyroid" in flat["sparse_infill_pattern"]
    assert flat["filament_colour"].replace('"', "").split(";") == ["#AABBCC", "#112233"]
    out = sc.normalize_config(flat)
    assert out["errors"] == {}
    assert sc.normalize_config(out["config"])["config"] == out["config"]


def test_obsolete_keys_are_not_reported_as_unknown():
    out = sc.normalize_config({"silent_mode": "1", "layer_height": "0.2"})
    assert out["issues"] == []


def test_functions_are_repeatable_and_independent():
    # Static definitions only: calling them repeatedly (as the add-on does per tick) gives equal results.
    assert sc.config_schema() == sc.config_schema()
    ctx = sc.ConditionContext({"printer_model": "X", "nozzle_diameter": ["0.4"]})
    assert [ctx.eval('printer_model == "X"') for _ in range(50)] == [True] * 50
