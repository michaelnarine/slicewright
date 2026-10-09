# SPDX-License-Identifier: GPL-3.0-or-later
"""A small hand-written config schema for the fake engine (04 section 6.4).

Key names and types mirror the public Orca profile format, but labels,
tooltips and layout are written here from scratch. This stands in for the real
engine's exported schema until plan M2 layer 14 ("the fake switches to the
exported fixtures"). It is deliberately tiny; the real schema has ~800 keys.
"""
from __future__ import annotations

_FIELDS = (
    "type", "label", "full_label", "category", "tooltip", "sidetext", "min", "max",
    "max_literal", "default", "enum", "enum_open", "gui_type", "gui_flags", "multiline",
    "full_width", "is_code", "readonly", "nullable", "ratio_over", "mode", "per_extruder",
    "preset", "scope", "variant",
)


def _e(type, label, default, *, category, preset, scope="global", mode="simple", min=None,
       max=None, enum=None, per_extruder=False, sidetext="", tooltip="", variant="none"):
    entry = {
        "type": type, "label": label, "full_label": label, "category": category,
        "tooltip": tooltip or label, "sidetext": sidetext, "min": min, "max": max,
        "max_literal": None, "default": default,
        "enum": [{"value": v, "label": v.replace("_", " ").title()} for v in enum] if enum else None,
        "enum_open": False, "gui_type": "", "gui_flags": "", "multiline": False,
        "full_width": False, "is_code": False, "readonly": False, "nullable": False,
        "ratio_over": None, "mode": mode, "per_extruder": per_extruder, "preset": preset,
        "scope": scope, "variant": variant,
    }
    assert set(entry) == set(_FIELDS)
    return entry


_SCHEMA = {
    # printer
    "printable_area": _e("points", "Printable area", "0x0,256x0,256x256,0x256",
                         category="Printer basic information", preset="printer"),
    "bed_exclude_area": _e("points", "Bed exclude area", "", category="Printer basic information",
                           preset="printer"),
    "printable_height": _e("float", "Printable height", "250", category="Printer basic information",
                           preset="printer", min=0.0, max=2000.0, sidetext="mm"),
    "nozzle_diameter": _e("floats", "Nozzle diameter", "0.4", category="Extruder", preset="printer",
                          min=0.1, max=10.0, per_extruder=True, sidetext="mm", variant="printer1"),
    "printer_model": _e("string", "Printer model", "", category="Printer basic information",
                        preset="printer"),
    "gcode_flavor": _e("enum", "G-code flavor", "marlin2", category="Printer basic information",
                       preset="printer", enum=["marlin", "marlin2", "klipper", "reprapfirmware"]),
    "thumbnails": _e("string", "G-code thumbnails", "", category="Printer basic information",
                     preset="printer", mode="advanced"),
    # process
    "layer_height": _e("float", "Layer height", "0.2", category="Quality", preset="process",
                       scope="object", min=0.01, max=1.0, sidetext="mm"),
    "initial_layer_print_height": _e("float", "First layer height", "0.2", category="Quality",
                                     preset="process", scope="object", min=0.01, max=1.0,
                                     sidetext="mm"),
    "wall_loops": _e("int", "Wall loops", "2", category="Strength", preset="process",
                     scope="object", min=1, max=1000),
    "top_shell_layers": _e("int", "Top shell layers", "5", category="Strength", preset="process",
                           scope="region", min=0, max=1000),
    "bottom_shell_layers": _e("int", "Bottom shell layers", "3", category="Strength",
                              preset="process", scope="region", min=0, max=1000),
    "sparse_infill_density": _e("percent", "Sparse infill density", "15%", category="Strength",
                                preset="process", scope="region", min=0, max=100, sidetext="%"),
    "sparse_infill_pattern": _e("enum", "Sparse infill pattern", "grid", category="Strength",
                                preset="process", scope="region",
                                enum=["grid", "gyroid", "line", "cubic"]),
    "outer_wall_speed": _e("float", "Outer wall speed", "60", category="Speed", preset="process",
                           scope="region", min=1, max=1000, sidetext="mm/s"),
    "inner_wall_speed": _e("float", "Inner wall speed", "100", category="Speed", preset="process",
                           scope="region", min=1, max=1000, sidetext="mm/s"),
    "sparse_infill_speed": _e("float", "Sparse infill speed", "120", category="Speed",
                              preset="process", scope="region", min=1, max=1000, sidetext="mm/s"),
    "enable_support": _e("bool", "Enable support", "0", category="Support", preset="process",
                         scope="object"),
    "skirt_loops": _e("int", "Skirt loops", "1", category="Others", preset="process", min=0,
                      max=100),
    "enable_prime_tower": _e("bool", "Enable prime tower", "0", category="Multimaterial",
                             preset="process"),
    "filename_format": _e("string", "Filename format",
                          "{input_filename_base}_{layer_height}mm_{filament_type[0]}_{print_time}.gcode",
                          category="Others", preset="process", mode="advanced"),
    "curr_bed_type": _e("enum", "Bed type", "Textured PEI Plate", category="Others", preset="none",
                        enum=["Cool Plate", "Engineering Plate", "Smooth PEI Plate",
                              "Textured PEI Plate"]),
    "wipe_tower_x": _e("floats", "Prime tower X", "165", category="Multimaterial", preset="none",
                       per_extruder=True, sidetext="mm"),
    "wipe_tower_y": _e("floats", "Prime tower Y", "250", category="Multimaterial", preset="none",
                       per_extruder=True, sidetext="mm"),
    # filament
    "filament_type": _e("strings", "Filament type", "PLA", category="Filament", preset="filament",
                        per_extruder=True),
    "filament_colour": _e("strings", "Filament colour", "#F2754E", category="Filament",
                          preset="filament", per_extruder=True),
    "filament_diameter": _e("floats", "Filament diameter", "1.75", category="Filament",
                            preset="filament", per_extruder=True, min=0.5, max=10.0, sidetext="mm"),
    "filament_density": _e("floats", "Filament density", "1.24", category="Filament",
                           preset="filament", per_extruder=True, min=0.0, sidetext="g/cm3"),
    "filament_cost": _e("floats", "Filament cost", "0", category="Filament", preset="filament",
                        per_extruder=True, min=0.0),
    "nozzle_temperature": _e("ints", "Nozzle temperature", "220", category="Filament",
                             preset="filament", per_extruder=True, min=0, max=500, sidetext="C",
                             variant="filament"),
}


def build_schema() -> dict[str, dict]:
    """A fresh deep-enough copy so callers may mutate the result."""
    return {k: {**v, "enum": [dict(x) for x in v["enum"]] if v["enum"] else None}
            for k, v in _SCHEMA.items()}


# Legacy key names mapped by normalize_config (04 section 6.2).
LEGACY_KEYS = {"fill_density": "sparse_infill_density", "perimeters": "wall_loops"}

# Keys that appear in preset JSON but are metadata, not config (04 section 2.5).
META_KEYS = frozenset({
    "name", "inherits", "include", "from", "instantiation", "setting_id", "filament_id",
    "version", "type", "compatible_printers", "compatible_printers_condition",
    "compatible_prints", "compatible_prints_condition", "renamed_from", "description",
})

# A minimal tab layout (04 section 6.5). Every key here exists in the schema.
TAB_LAYOUT = {
    "process": [
        {"page": "Quality", "icon": "quality", "groups": [
            {"title": "Layer height", "keys": ["layer_height", "initial_layer_print_height"],
             "wiki": {"layer_height": "quality_settings_layer_height"}, "custom": None}]},
        {"page": "Strength", "icon": "strength", "groups": [
            {"title": "Walls", "keys": ["wall_loops"], "wiki": {}, "custom": None},
            {"title": "Infill", "keys": ["sparse_infill_density", "sparse_infill_pattern"],
             "wiki": {}, "custom": None}]},
        {"page": "Speed", "icon": "speed", "groups": [
            {"title": "Print speed",
             "keys": ["outer_wall_speed", "inner_wall_speed", "sparse_infill_speed"],
             "wiki": {}, "custom": None}]},
        {"page": "Support", "icon": "support", "groups": [
            {"title": "Support", "keys": ["enable_support"], "wiki": {}, "custom": None}]},
    ],
    "filament": [
        {"page": "Filament", "icon": "filament", "groups": [
            {"title": "Basic information",
             "keys": ["filament_type", "filament_colour", "filament_diameter", "filament_density"],
             "wiki": {}, "custom": None},
            {"title": "Temperature", "keys": ["nozzle_temperature"], "wiki": {}, "custom": None}]},
    ],
    "printer": [
        {"page": "Basic information", "icon": "printer", "groups": [
            {"title": "Printable space",
             "keys": ["printable_area", "bed_exclude_area", "printable_height"],
             "wiki": {}, "custom": "bed_shape"},
            {"title": "Advanced", "keys": ["gcode_flavor"], "wiki": {}, "custom": None}]},
        {"page": "Extruder", "icon": "extruder", "groups": [
            {"title": "Size", "keys": ["nozzle_diameter"], "wiki": {}, "custom": "extruder_count"}]},
    ],
}
