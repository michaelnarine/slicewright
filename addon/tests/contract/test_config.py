# SPDX-License-Identifier: GPL-3.0-or-later
"""Config functions: schema, layout, compose, normalize, conditions (04 section 6)."""
from __future__ import annotations

import pytest
from contract_helpers import FILAMENT, PRINTER, PROCESS, split_vector

SCHEMA_FIELDS = {
    "type", "label", "full_label", "category", "tooltip", "sidetext", "min", "max", "max_literal",
    "default", "enum", "enum_open", "gui_type", "gui_flags", "multiline", "full_width", "is_code",
    "readonly", "nullable", "ratio_over", "mode", "per_extruder", "preset", "scope", "variant",
}


# --- config_schema (6.4) and tab_layout (6.5) ------------------------------------------

def test_schema_entries_have_the_documented_fields(backend):
    schema = backend.config_schema()
    assert isinstance(schema, dict) and schema
    for key, entry in schema.items():
        assert isinstance(key, str)
        assert SCHEMA_FIELDS <= set(entry), f"{key}: missing {SCHEMA_FIELDS - set(entry)}"
        assert entry["mode"] in ("simple", "advanced", "expert", "develop"), key
        assert entry["preset"] in ("printer", "process", "filament", "none"), key
        assert entry["scope"] in ("global", "object", "region"), key
        assert entry["variant"] in ("print", "filament", "printer1", "printer2", "none"), key
        assert isinstance(entry["default"], str), key
        assert isinstance(entry["label"], str) and isinstance(entry["tooltip"], str), key
        assert entry["min"] is None or isinstance(entry["min"], (int, float)), key
        assert entry["enum"] is None or all({"value", "label"} <= set(e) for e in entry["enum"]), key
        assert isinstance(entry["per_extruder"], bool), key


def test_schema_covers_the_keys_the_contract_uses(backend):
    schema = backend.config_schema()
    for key in ("layer_height", "wall_loops", "sparse_infill_density", "printable_area",
                "printable_height", "nozzle_diameter", "filament_type", "filament_colour"):
        assert key in schema, key


def test_object_and_region_scopes_exist(backend):
    scopes = {e["scope"] for e in backend.config_schema().values()}
    assert {"global", "object"} <= scopes


def test_tab_layout_shape_and_schema_coverage(backend):
    layout = backend.tab_layout()
    schema = backend.config_schema()
    assert set(layout) <= {"process", "filament", "printer"} and layout
    for tab, pages in layout.items():
        assert pages, tab
        for page in pages:
            assert isinstance(page["page"], str) and isinstance(page["icon"], str)
            for group in page["groups"]:
                assert isinstance(group["title"], str)
                assert isinstance(group["wiki"], dict)
                assert group["custom"] is None or isinstance(group["custom"], str)
                for key in group["keys"]:
                    assert key in schema, f"tab_layout key {key!r} is not in config_schema()"


# --- compose_config (6.1) ---------------------------------------------------------------

def test_compose_returns_flat_string_config(backend):
    flat = backend.compose_config(PRINTER, PROCESS, [FILAMENT])
    assert isinstance(flat, dict) and flat
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in flat.items())
    assert flat["layer_height"] == "0.2"
    assert flat["wall_loops"] == "2"


def test_compose_takes_values_from_each_preset(backend):
    flat = backend.compose_config(
        {**PRINTER, "printable_height": "180"}, {**PROCESS, "layer_height": "0.16"}, [FILAMENT])
    assert flat["printable_height"] == "180"
    assert flat["layer_height"] == "0.16"
    assert split_vector(flat["filament_type"]) == ["PLA"]


def test_compose_concatenates_filaments_in_slot_order(backend):
    second = {"name": "Contract PETG", "filament_type": ["PETG"], "filament_colour": ["#112233"]}
    flat = backend.compose_config(PRINTER, PROCESS, [FILAMENT, second])
    assert split_vector(flat["filament_type"]) == ["PLA", "PETG"]
    assert len(split_vector(flat["filament_colour"])) == 2


def test_compose_applies_project_overrides_last(backend):
    flat = backend.compose_config(PRINTER, PROCESS, [FILAMENT], {"layer_height": "0.3"})
    assert flat["layer_height"] == "0.3"


def test_compose_ignores_metadata_keys(backend):
    process = {**PROCESS, "inherits": "fdm_process_common", "from": "system",
               "instantiation": "true", "setting_id": "GP004"}
    flat = backend.compose_config(PRINTER, process, [FILAMENT])
    assert flat["layer_height"] == "0.2"
    assert "inherits" not in flat and "instantiation" not in flat


def test_compose_rejects_non_string_scalars(backend):
    with pytest.raises(TypeError):
        backend.compose_config(PRINTER, {**PROCESS, "layer_height": 0.2}, [FILAMENT])


def test_compose_raises_config_error_on_unparsable_value(backend):
    with pytest.raises(backend.ConfigError):
        backend.compose_config(PRINTER, {**PROCESS, "layer_height": "thick"}, [FILAMENT])


# --- normalize_config (6.2) -------------------------------------------------------------

def test_normalize_result_shape(backend):
    flat = backend.compose_config(PRINTER, PROCESS, [FILAMENT])
    out = backend.normalize_config(flat)
    assert set(out) >= {"config", "substitutions", "errors"}
    assert out["errors"] == {}
    assert isinstance(out["substitutions"], list)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in out["config"].items())
    assert out["config"]["layer_height"] == "0.2"


def test_normalize_is_idempotent(backend):
    cfg = backend.normalize_config(backend.compose_config(PRINTER, PROCESS, [FILAMENT]))["config"]
    again = backend.normalize_config(cfg)
    assert again["config"] == cfg
    assert again["errors"] == {}


def test_normalize_reports_invalid_values_instead_of_raising(backend):
    flat = backend.compose_config(PRINTER, PROCESS, [FILAMENT])
    flat["sparse_infill_density"] = "150%"
    out = backend.normalize_config(flat)
    assert "sparse_infill_density" in out["errors"]
    assert isinstance(out["errors"]["sparse_infill_density"], str)


def test_normalize_fills_defaults(backend):
    out = backend.normalize_config({"layer_height": "0.12"})
    assert out["config"]["layer_height"] == "0.12"
    assert out["config"]["wall_loops"]  # filled from the default
    assert out["errors"] == {}


# --- eval_condition and ConditionContext (6.3) ------------------------------------------

CONFIG = {
    "printer_model": "A1",
    "nozzle_diameter": ["0.4", "0.6"],
    "printer_notes": "PRINTER_VENDOR_TEST and more",
    "num_extruders": "2",
}


@pytest.mark.parametrize("expr,expected", [
    ('printer_model == "A1"', True),
    ('printer_model != "A1"', False),
    ('printer_model == "X1"', False),
    ("nozzle_diameter[0] == 0.4", True),
    ("nozzle_diameter[1] > 0.5", True),
    ("nozzle_diameter[0] >= 0.5", False),
    ("num_extruders == 2", True),
    ("num_extruders < 2", False),
    ("printer_notes =~ /.*VENDOR_TEST.*/", True),
    ("printer_notes =~ /.*NOPE.*/", False),
    ('printer_model == "A1" and nozzle_diameter[0] == 0.4', True),
    ('printer_model == "X1" or nozzle_diameter[1] == 0.6', True),
    ('not printer_model == "A1"', False),
    ('(printer_model == "X1" or printer_model == "A1") and num_extruders == 2', True),
])
def test_eval_condition(backend, expr, expected):
    assert backend.eval_condition(expr, CONFIG) is expected


def test_eval_condition_converts_scalars_with_str(backend):
    assert backend.eval_condition("num_extruders == 2", {"num_extruders": 2}) is True


@pytest.mark.parametrize("expr", ["((", "printer_model ==", "nozzle_diameter[0] == == 1"])
def test_eval_condition_raises_config_error_on_parse_errors(backend, expr):
    with pytest.raises(backend.ConfigError):
        backend.eval_condition(expr, CONFIG)


def test_condition_context_matches_eval_condition(backend):
    ctx = backend.ConditionContext(CONFIG)
    for expr in ('printer_model == "A1"', "nozzle_diameter[1] == 0.6", "num_extruders > 4"):
        assert ctx.eval(expr) is backend.eval_condition(expr, CONFIG)
    # reusable many times
    assert all(ctx.eval('printer_model == "A1"') for _ in range(200))


def test_condition_context_raises_config_error(backend):
    ctx = backend.ConditionContext(CONFIG)
    with pytest.raises(backend.ConfigError):
        ctx.eval("((")
