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
    # Enum options whose own default does not serialise come back as "engine" info issues (reported, not silently
    # dropped); nothing else may be reported.
    assert [i for i in out["issues"] if not (i["level"] == "info" and i["code"] == "engine")] == []


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


def test_tab_layout_keys_are_all_in_the_schema():
    schema = sc.config_schema()
    layout = sc.tab_layout()
    assert set(layout) == {"process", "filament", "printer"}
    placed = 0
    for tab, pages in layout.items():
        for page in pages:
            for group in page["groups"]:
                for key in group["keys"]:
                    assert key in schema, f"{tab}/{page['page']}: {key}"
                    placed += 1
    assert placed > 500


def test_functions_are_repeatable_and_independent():
    # Static definitions only: calling them repeatedly (as the add-on does per tick) gives equal results.
    assert sc.config_schema() == sc.config_schema()
    ctx = sc.ConditionContext({"printer_model": "X", "nozzle_diameter": ["0.4"]})
    assert [ctx.eval('printer_model == "X"') for _ in range(50)] == [True] * 50


# --- review fixes (M2 review items 9, 10, 11) ----------------------------------------------------------

@pytest.mark.parametrize("key,expected", [
    ("extruder_offset", True), ("nozzle_diameter", True), ("filament_colour", True), ("filament_diameter", True),
    ("compatible_printers", False), ("compatible_prints", False), ("post_process", False),
    ("upward_compatible_machine", False), ("wipe_tower_x", False), ("wipe_tower_y", False), ("layer_height", False),
])
def test_per_extruder_follows_orcas_lists(key, expected):
    assert sc.config_schema()[key]["per_extruder"] is expected


def test_multi_variant_filaments_are_refused_until_the_collapse_exists():
    two = {**FILAMENT, "filament_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow"]}
    with pytest.raises(sc.ConfigError) as err:
        sc.compose_config(PRINTER, PROCESS, [FILAMENT, two])
    assert err.value.key == "filament_extruder_variant"
    one = {**FILAMENT, "filament_extruder_variant": ["Direct Drive Standard"]}
    sc.compose_config(PRINTER, PROCESS, [one])  # a single variant is fine


def test_enum_vector_values_survive_normalize_config():
    """They used to vanish: values copied into the default enum-vector options (null keys_map) serialise to nothing
    (z_hop_types, extruder_type, nozzle_type, nozzle_volume_type, overhang_fan_threshold)."""
    given = {"overhang_fan_threshold": "25%", "z_hop_types": "Spiral Lift", "nozzle_volume_type": "Standard,High Flow",
             "layer_height": "0.2"}
    out = sc.normalize_config(given)
    for key, value in given.items():
        assert out["config"].get(key) == value, key
    assert not [i for i in out["issues"] if i["opt_key"] in given]
    # and the round trip is stable
    assert sc.normalize_config(out["config"])["config"] == out["config"]


def test_enum_vector_values_are_readable_in_condition_contexts():
    """PlaceholderParser reads an enum vector as the enum's integer values (Orca's enum order: Standard 0, High Flow 1;
    Auto Lift 0, Normal 1, Slope 2, Spiral Lift 3), so the conditions of a preset can test them. ConditionContext
    takes its config over with apply_layer like the other loaders."""
    ctx = sc.ConditionContext({"z_hop_types": "Spiral Lift", "nozzle_volume_type": "Standard,High Flow"})
    assert ctx.eval("z_hop_types[0] == 3") is True
    assert ctx.eval("nozzle_volume_type[0] == 0 and nozzle_volume_type[1] == 1") is True
    assert ctx.eval("nozzle_volume_type[1] == 0") is False


def test_value_substitutions_are_reported():
    """An enum value Orca does not know is replaced by its default (forward compatibility); the replacement is
    recorded in `substitutions` (04 section 6.2), not silent."""
    out = sc.normalize_config({"sparse_infill_pattern": "no_such_pattern", "layer_height": "0.2"})
    subs = [s for s in out["substitutions"] if s["key"] == "sparse_infill_pattern"]
    assert len(subs) == 1 and subs[0]["value"] == "no_such_pattern" and subs[0]["replacement"]
    assert out["config"]["sparse_infill_pattern"] == subs[0]["replacement"]
