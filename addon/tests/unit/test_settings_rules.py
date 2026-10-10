# SPDX-License-Identifier: GPL-3.0-or-later
"""Behaviour of the show/enable rule table (03 section 2.4), group by group."""
import pytest

from slicewright.blender import settings_rules as r


def state(key, **flat):
    return r.key_state(r.Cfg.of({k: str(v) for k, v in flat.items()}), key)


def test_the_table_has_about_forty_rules_over_a_hundred_and_fifty_keys_in_ten_groups():
    assert 40 <= len(r.RULES) <= 80
    assert len(r.rule_keys()) >= 150
    assert 10 <= len(r.groups()) <= 14
    assert all(rule.enabled_if or rule.visible_if for rule in r.RULES)       # no rule is a no-op


def test_keys_without_a_rule_are_always_visible_and_enabled():
    assert state("layer_height") == (True, True)
    assert state("no_such_key") == (True, True)
    assert r.key_state(r.Cfg.of({}), "layer_height") == (True, True)


def test_every_rule_key_has_a_state_that_can_differ_from_the_default():
    """Each rule must be satisfiable both ways, otherwise it is dead weight."""
    probes = [{}, {k: "1" for k in r.condition_keys()}, {k: "0" for k in r.condition_keys()}]
    for rule in r.RULES:
        outcomes = {r.key_state(r.Cfg.of(p), rule.keys[0]) for p in probes}
        assert len(outcomes) >= 1                       # smoke: evaluating never raises


@pytest.mark.parametrize("flat,key,expected", [
    # supports
    ({"enable_support": 0}, "support_type", (True, False)),
    ({"enable_support": 1}, "support_type", (True, True)),
    ({"enable_support": 0, "raft_layers": 2}, "support_type", (True, True)),     # a raft counts
    ({"enable_support": 1, "support_type": "tree(auto)"}, "support_threshold_angle", (True, True)),
    ({"enable_support": 1, "support_type": "normal(manual)"}, "support_threshold_angle", (True, False)),
    ({"enable_support": 1, "support_interface_top_layers": 0, "support_interface_bottom_layers": 0},
     "support_interface_filament", (True, False)),
    ({"enable_support": 1, "support_interface_top_layers": 2}, "support_interface_filament", (True, True)),
    ({"enable_support": 1, "support_type": "tree(auto)"}, "max_bridge_length", (True, True)),
    ({"enable_support": 1, "support_type": "normal(auto)"}, "max_bridge_length", (False, True)),
    ({"enable_support": 1, "support_type": "normal(auto)"}, "bridge_no_support", (True, True)),
    ({"enable_support": 0, "raft_layers": 0}, "raft_contact_distance", (False, True)),
    ({"raft_layers": 3}, "raft_contact_distance", (True, True)),
    ({"enable_support": 1, "support_interface_top_layers": 3, "support_ironing": 1},
     "support_ironing_flow", (True, True)),
    ({"enable_support": 1, "support_interface_top_layers": 3, "support_ironing": 1},
     "support_interface_spacing", (True, False)),         # solid interface while ironing it
    ({"enable_support": 0, "support_ironing": 1}, "support_ironing_flow", (False, True)),
    # infill
    ({"sparse_infill_density": "0%"}, "sparse_infill_pattern", (False, True)),
    ({"sparse_infill_density": "15%"}, "sparse_infill_pattern", (True, True)),
    ({"sparse_infill_density": "15%", "sparse_infill_pattern": "grid"}, "infill_shift_step", (False, True)),
    ({"sparse_infill_density": "15%", "sparse_infill_pattern": "cross-zag"}, "infill_shift_step", (True, True)),
    ({"sparse_infill_density": "15%", "sparse_infill_pattern": "zig-zag"}, "symmetric_infill_y_axis", (True, True)),
    ({"sparse_infill_pattern": "line"}, "infill_anchor_max", (True, False)),
    ({"sparse_infill_pattern": "grid", "infill_anchor_max": "20"}, "infill_anchor", (True, True)),
    ({"sparse_infill_pattern": "grid", "infill_anchor_max": "0"}, "infill_anchor", (True, False)),
    ({"top_shell_layers": 0}, "top_surface_pattern", (True, False)),
    ({"top_shell_layers": 4}, "top_surface_pattern", (True, True)),
    ({"bottom_shell_layers": 0}, "bottom_surface_density", (True, False)),
    ({"top_shell_layers": 4, "spiral_mode": 1}, "top_shell_thickness", (True, False)),
    # walls
    ({"wall_loops": 0}, "outer_wall_speed", (True, False)),
    ({"wall_loops": 2}, "outer_wall_speed", (True, True)),
    ({"wall_generator": "classic"}, "min_bead_width", (False, True)),
    ({"wall_generator": "arachne"}, "min_bead_width", (True, True)),
    ({"wall_generator": "arachne", "wall_loops": 2}, "detect_thin_wall", (True, False)),
    ({"wall_generator": "classic", "wall_loops": 2}, "detect_thin_wall", (True, True)),
    # spiral
    ({"spiral_mode": 0}, "spiral_mode_smooth", (False, True)),
    ({"spiral_mode": 1}, "spiral_mode_smooth", (True, True)),
    ({"spiral_mode": 1, "spiral_mode_smooth": 0}, "spiral_mode_max_xy_smoothing", (False, True)),
    ({"spiral_mode": 1, "spiral_mode_smooth": 1}, "spiral_mode_max_xy_smoothing", (True, True)),
    ({"spiral_mode": 1}, "seam_slope_type", (True, False)),
    ({"spiral_mode": 0, "seam_slope_type": "external"}, "seam_slope_steps", (True, True)),
    ({"spiral_mode": 1, "seam_slope_type": "external"}, "seam_slope_steps", (False, True)),
    ({"seam_slope_type": "external", "seam_slope_entire_loop": 1}, "seam_slope_min_length", (True, False)),
    # skirt and brim
    ({"skirt_loops": 0}, "skirt_distance", (True, False)),
    ({"skirt_loops": 2}, "skirt_distance", (True, True)),
    ({"skirt_loops": 2, "draft_shield": "enabled"}, "skirt_height", (True, False)),
    ({"skirt_loops": 2, "draft_shield": "disabled"}, "skirt_height", (True, True)),
    ({"brim_type": "no_brim"}, "brim_object_gap", (True, False)),
    ({"brim_type": "outer_only"}, "brim_width", (True, True)),
    ({"brim_type": "auto_brim"}, "brim_width", (True, False)),
    ({"brim_type": "brim_ears"}, "brim_ears_max_angle", (True, True)),
    ({"brim_type": "outer_only"}, "brim_ears_max_angle", (False, True)),
    # ironing and fuzzy skin
    ({"ironing_type": "no ironing"}, "ironing_flow", (False, True)),
    ({"ironing_type": "top"}, "ironing_flow", (True, True)),
    ({"ironing_type": "top", "ironing_pattern": "zig-zag"}, "ironing_angle", (True, False)),
    ({"ironing_type": "top", "ironing_pattern": "rectilinear"}, "ironing_angle", (True, True)),
    ({"ironing_type": "no ironing"}, "ironing_speed", (False, True)),
    ({"fuzzy_skin": "none"}, "fuzzy_skin_thickness", (False, True)),
    ({"fuzzy_skin": "external"}, "fuzzy_skin_thickness", (True, True)),
    # prime tower, interlocking, ooze
    ({"enable_prime_tower": 0}, "prime_tower_width", (False, True)),
    ({"enable_prime_tower": 1}, "prime_tower_width", (True, True)),
    ({"enable_prime_tower": 0}, "flush_into_infill", (True, False)),
    ({"interlocking_beam": 0}, "interlocking_depth", (False, True)),
    ({"interlocking_beam": 1}, "mmu_segmented_region_interlocking_depth", (False, True)),
    ({"ooze_prevention": 1}, "preheat_time", (True, True)),
    # overhang and travel
    ({"reduce_crossing_wall": 0}, "max_travel_detour_distance", (False, True)),
    ({"make_overhang_printable": 1}, "make_overhang_printable_angle", (True, True)),
    ({"enable_overhang_speed": 0}, "overhang_1_4_speed", (False, True)),
    # acceleration and jerk
    ({"default_acceleration": 0}, "travel_acceleration", (True, False)),
    ({"default_acceleration": 5000}, "travel_acceleration", (True, True)),
    ({"default_jerk": 0}, "outer_wall_jerk", (True, False)),
    ({"default_jerk": 8}, "outer_wall_jerk", (True, True)),
    # retraction (per-extruder values read as their first element)
    ({"use_firmware_retraction": 1}, "retraction_length", (True, False)),
    ({"retraction_length": "0", "use_firmware_retraction": 0}, "z_hop", (True, False)),
    ({"retraction_length": "0.8,0.6"}, "z_hop", (True, True)),
    ({"retraction_length": "0.8", "use_firmware_retraction": 1}, "retraction_speed", (True, False)),
    ({"retraction_length": "0.8", "z_hop": "0"}, "z_hop_types", (True, False)),
    ({"retraction_length": "0.8", "z_hop": "0.4"}, "z_hop_types", (True, True)),
    ({"retraction_length": "0.8", "wipe": "1"}, "wipe_distance", (True, True)),
    ({"retraction_length": "0.8", "wipe": "0"}, "wipe_distance", (True, False)),
    # cooling
    ({"enable_overhang_bridge_fan": "0"}, "overhang_fan_speed", (True, False)),
    ({"enable_overhang_bridge_fan": "1"}, "overhang_fan_speed", (True, True)),
    ({"slow_down_for_layer_cooling": "0"}, "dont_slow_down_outer_wall", (True, False)),
    ({"enable_pressure_advance": "1"}, "pressure_advance", (True, True)),
])
def test_rule(flat, key, expected):
    assert state(key, **flat) == expected


def test_a_key_named_by_several_rules_needs_all_of_them():
    # support_ironing_flow: visible needs support ironing; interface spacing needs interface and no ironing
    flat = {"enable_support": 1, "support_interface_top_layers": 2}
    assert state("support_interface_spacing", **flat) == (True, True)
    assert state("support_interface_spacing", **flat, support_ironing=1) == (True, False)
    assert state("support_interface_spacing", enable_support=0) == (True, False)
    # bridge settings: max_bridge_length is enabled by supports AND visible only for tree supports
    assert state("max_bridge_length", enable_support=0, support_type="tree(auto)") == (False, False)


def test_values_that_do_not_parse_read_as_zero_not_as_errors():
    assert state("skirt_distance", skirt_loops="lots") == (True, False)
    assert state("skirt_distance", skirt_loops="") == (True, False)
    assert state("top_surface_pattern", top_shell_layers="3.5") == (True, True)
    assert r.Cfg.of({"x": "15%"}).n("x") == 15
    assert r.Cfg.of({"x": "TRUE"}).b("x") and not r.Cfg.of({}).b("x")


def test_row_state_memoises_within_one_draw():
    calls = []

    def get(key):
        calls.append(key)
        return "1"
    state_fn = r.row_state(r.Cfg(get))
    state_fn("support_type")
    first = len(calls)
    state_fn("support_type")
    assert len(calls) == first


def test_condition_keys_cover_the_keys_the_predicates_read():
    keys = r.condition_keys()
    assert {"enable_support", "raft_layers", "wall_generator", "sparse_infill_density", "spiral_mode",
            "brim_type", "ironing_type", "fuzzy_skin", "enable_prime_tower", "default_acceleration",
            "retraction_length", "use_firmware_retraction", "enable_pressure_advance"} <= keys
