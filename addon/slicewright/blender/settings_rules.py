# SPDX-License-Identifier: GPL-3.0-or-later
"""Show/enable rules for settings rows: our own declarative table (03 section 2.4).

Written from observed behaviour of the settings UI, not from Orca's code (provenance rule): each rule
says which keys become *visible* or *enabled* under a condition on the settings of the same preset
role. Keys without a rule are always visible and enabled; a key named by several rules needs all of them
to pass. Conditions read values through a :class:`Cfg`, so a caller can back it by a PropertyGroup, a
flat dict or an override buffer. ``test_settings_rules`` asserts every key named here exists in the
engine schema. No ``bpy`` here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

TRUE_TEXT = ("1", "true")


class Cfg:
    """Typed reads over a ``key -> FlatConfig text`` lookup. Missing or odd values read as 0 / "" / False."""

    def __init__(self, get: Callable[[str], str | None]) -> None:
        self._get = get

    @classmethod
    def of(cls, flat: dict[str, str]) -> "Cfg":
        return cls(flat.get)

    def s(self, key: str) -> str:
        value = self._get(key)
        return "" if value is None else str(value).strip()

    def b(self, key: str) -> bool:
        first = self.s(key).split(",")[0].strip().lower()       # per-extruder flags: the first element
        return first in TRUE_TEXT

    def n(self, key: str) -> float:
        text = self.s(key).split(",")[0].strip().rstrip("%")
        try:
            return float(text)
        except ValueError:
            return 0.0


Pred = Callable[[Cfg], bool]


@dataclass(frozen=True)
class Rule:
    group: str
    keys: tuple[str, ...]
    enabled_if: Pred | None = None
    visible_if: Pred | None = None


def _r(group: str, keys: str, *, enabled: Pred | None = None, visible: Pred | None = None) -> Rule:
    return Rule(group, tuple(keys.split()), enabled, visible)


# -- conditions shared by several rules -----------------------------------------------------------

def _have_support(c: Cfg) -> bool:
    return c.b("enable_support") or c.n("raft_layers") > 0


def _have_interface(c: Cfg) -> bool:
    return c.n("support_interface_top_layers") > 0 or c.n("support_interface_bottom_layers") > 0


def _is_tree(c: Cfg) -> bool:
    return c.b("enable_support") and c.s("support_type").startswith("tree")


def _can_iron_support(c: Cfg) -> bool:
    return c.n("raft_layers") > 0 or (_have_support(c) and c.n("support_interface_top_layers") > 0)


def _support_ironing(c: Cfg) -> bool:
    return _can_iron_support(c) and c.b("support_ironing")


def _have_infill(c: Cfg) -> bool:
    return c.n("sparse_infill_density") > 0


def _has_ironing(c: Cfg) -> bool:
    return c.s("ironing_type") not in ("", "no ironing")


def _retraction(c: Cfg) -> bool:
    return c.n("retraction_length") > 0 or c.b("use_firmware_retraction")


def _seam_slope(c: Cfg) -> bool:
    return not c.b("spiral_mode") and c.s("seam_slope_type") not in ("", "none")


def _wipe(c: Cfg) -> bool:
    return _retraction(c) and c.b("wipe")


# -- the table: about ten groups ------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    # walls
    _r("walls", "extra_perimeters_on_overhangs ensure_vertical_shell_thickness detect_overhang_wall "
                "seam_position staggered_inner_seams wall_sequence outer_wall_line_width inner_wall_speed "
                "outer_wall_speed small_perimeter_speed small_perimeter_threshold",
       enabled=lambda c: c.n("wall_loops") > 0),
    _r("walls", "detect_thin_wall",
       enabled=lambda c: c.n("wall_loops") > 0 and c.s("wall_generator") != "arachne"),
    _r("walls", "wall_transition_length wall_transition_filter_deviation wall_transition_angle "
                "min_feature_size min_length_factor min_bead_width wall_distribution_count "
                "initial_layer_min_bead_width wall_maximum_resolution wall_maximum_deviation",
       visible=lambda c: c.s("wall_generator") == "arachne"),
    # infill and shells
    _r("infill", "sparse_infill_pattern infill_combination fill_multiline infill_direction "
                 "minimum_sparse_infill_area", visible=_have_infill),
    _r("infill", "infill_shift_step",
       visible=lambda c: _have_infill(c) and c.s("sparse_infill_pattern") in ("cross-zag", "locked-zag")),
    _r("infill", "symmetric_infill_y_axis",
       visible=lambda c: _have_infill(c) and c.s("sparse_infill_pattern") in (
           "zig-zag", "cross-zag", "locked-zag")),
    _r("infill", "infill_anchor_max", enabled=lambda c: c.s("sparse_infill_pattern") != "line"),
    _r("infill", "infill_anchor",
       enabled=lambda c: c.s("sparse_infill_pattern") != "line" and c.n("infill_anchor_max") > 0),
    _r("infill", "top_surface_pattern top_surface_density top_layer_direction top_surface_speed",
       enabled=lambda c: c.n("top_shell_layers") > 0),
    _r("infill", "bottom_surface_pattern bottom_surface_density bottom_layer_direction",
       enabled=lambda c: c.n("bottom_shell_layers") > 0),
    _r("infill", "top_shell_thickness",
       enabled=lambda c: not c.b("spiral_mode") and c.n("top_shell_layers") > 0),
    _r("infill", "bottom_shell_thickness",
       enabled=lambda c: not c.b("spiral_mode") and c.n("bottom_shell_layers") > 0),
    # spiral vase
    _r("spiral", "spiral_mode_smooth spiral_starting_flow_ratio spiral_finishing_flow_ratio",
       visible=lambda c: c.b("spiral_mode")),
    _r("spiral", "spiral_mode_max_xy_smoothing",
       visible=lambda c: c.b("spiral_mode") and c.b("spiral_mode_smooth")),
    _r("spiral", "seam_slope_type", enabled=lambda c: not c.b("spiral_mode")),
    _r("spiral", "seam_slope_conditional seam_slope_start_height seam_slope_entire_loop "
                 "seam_slope_steps seam_slope_inner_walls", visible=_seam_slope),
    _r("spiral", "seam_slope_min_length", visible=_seam_slope,
       enabled=lambda c: not c.b("seam_slope_entire_loop")),
    # skirt and brim
    _r("skirt_brim", "skirt_type min_skirt_length skirt_distance skirt_start_angle skirt_speed draft_shield",
       enabled=lambda c: c.n("skirt_loops") > 0),
    _r("skirt_brim", "skirt_height",
       enabled=lambda c: c.n("skirt_loops") > 0 and c.s("draft_shield") != "enabled"),
    _r("skirt_brim", "brim_object_gap", enabled=lambda c: c.s("brim_type") != "no_brim"),
    _r("skirt_brim", "brim_width",
       enabled=lambda c: c.s("brim_type") not in ("no_brim", "auto_brim", "brim_ears")),
    _r("skirt_brim", "brim_ears_max_angle brim_ears_detection_length",
       visible=lambda c: c.s("brim_type") == "brim_ears"),
    # supports and raft
    _r("support", "support_type support_style support_on_build_plate_only support_critical_regions_only "
                  "support_base_pattern support_base_pattern_spacing support_expansion support_angle "
                  "support_top_z_distance support_bottom_z_distance support_object_xy_distance "
                  "support_object_first_layer_gap independent_support_layer_height "
                  "support_interface_top_layers support_interface_bottom_layers support_interface_pattern "
                  "bridge_no_support max_bridge_length support_interface_not_for_body",
       enabled=_have_support),
    _r("support", "support_threshold_angle",
       enabled=lambda c: _have_support(c) and c.s("support_type").endswith("(auto)")),
    _r("support", "support_interface_filament support_interface_loop_pattern support_bottom_interface_spacing",
       enabled=lambda c: _have_support(c) and _have_interface(c)),
    _r("support", "support_interface_spacing",
       enabled=lambda c: _have_support(c) and _have_interface(c) and not _support_ironing(c)),
    _r("support", "support_ironing", enabled=_can_iron_support),
    _r("support", "support_ironing_pattern support_ironing_flow support_ironing_spacing",
       visible=_support_ironing),
    _r("support", "max_bridge_length", visible=_is_tree),
    _r("support", "bridge_no_support", visible=lambda c: not _is_tree(c)),
    _r("support", "small_support_perimeter_speed small_support_perimeter_threshold",
       enabled=lambda c: c.b("enable_support")),
    _r("support", "raft_contact_distance", visible=lambda c: c.n("raft_layers") > 0),
    _r("support", "raft_first_layer_density", enabled=_have_support),
    # ironing and fuzzy skin
    _r("ironing", "ironing_pattern ironing_flow ironing_spacing ironing_inset", visible=_has_ironing),
    _r("ironing", "ironing_angle ironing_angle_fixed", visible=_has_ironing,
       enabled=lambda c: c.s("ironing_pattern") == "rectilinear"),
    _r("ironing", "ironing_speed", visible=lambda c: _has_ironing(c) or _support_ironing(c)),
    _r("ironing", "fuzzy_skin_mode fuzzy_skin_noise_type fuzzy_skin_point_distance fuzzy_skin_thickness "
                  "fuzzy_skin_first_layer", visible=lambda c: c.s("fuzzy_skin") not in ("", "none")),
    # prime tower and flushing
    _r("prime_tower", "prime_tower_width prime_tower_brim_width prime_tower_skip_points "
                      "wipe_tower_wall_type prime_volume", visible=lambda c: c.b("enable_prime_tower")),
    _r("prime_tower", "flush_into_infill flush_into_support flush_into_objects",
       enabled=lambda c: c.b("enable_prime_tower")),
    _r("multimaterial", "standby_temperature_delta preheat_time", visible=lambda c: c.b("ooze_prevention")),
    _r("multimaterial", "mmu_segmented_region_interlocking_depth",
       visible=lambda c: not c.b("interlocking_beam")),
    _r("multimaterial", "interlocking_beam_width interlocking_orientation interlocking_beam_layer_count "
                      "interlocking_depth interlocking_boundary_avoidance",
       visible=lambda c: c.b("interlocking_beam")),
    # travel and overhang handling
    _r("overhang", "max_travel_detour_distance", visible=lambda c: c.b("reduce_crossing_wall")),
    _r("overhang", "make_overhang_printable_angle make_overhang_printable_hole_size",
       visible=lambda c: c.b("make_overhang_printable")),
    _r("overhang", "overhang_1_4_speed overhang_2_4_speed overhang_3_4_speed overhang_4_4_speed "
                   "slowdown_for_curled_perimeters", visible=lambda c: c.b("enable_overhang_speed")),
    # acceleration and jerk
    _r("motion", "outer_wall_acceleration inner_wall_acceleration initial_layer_acceleration "
                 "initial_layer_travel_acceleration top_surface_acceleration travel_acceleration "
                 "bridge_acceleration sparse_infill_acceleration",
       enabled=lambda c: c.n("default_acceleration") > 0),
    _r("motion", "outer_wall_jerk inner_wall_jerk top_surface_jerk infill_jerk initial_layer_jerk travel_jerk",
       enabled=lambda c: c.n("default_jerk") > 0),
    # printer retraction
    _r("retraction", "retraction_length", enabled=lambda c: not c.b("use_firmware_retraction")),
    _r("retraction", "retraction_minimum_travel z_hop wipe", enabled=_retraction),
    _r("retraction", "retraction_speed deretraction_speed retract_restart_extra",
       enabled=lambda c: _retraction(c) and not c.b("use_firmware_retraction")),
    _r("retraction", "z_hop_types", enabled=lambda c: _retraction(c) and c.n("z_hop") > 0),
    _r("retraction", "wipe_distance", enabled=_wipe),
    # filament cooling and pressure advance
    _r("cooling", "overhang_fan_speed overhang_fan_threshold",
       enabled=lambda c: c.b("enable_overhang_bridge_fan")),
    _r("cooling", "dont_slow_down_outer_wall", enabled=lambda c: c.b("slow_down_for_layer_cooling")),
    _r("cooling", "pressure_advance", enabled=lambda c: c.b("enable_pressure_advance")),
)


# The enum values the conditions above compare with, so a test can check they are real values of the
# engine's enums (a misspelt value would silently never match). Spellings follow the serialised
# values of the public profile format; verify against the real schema when the wheel is available.
ENUM_VALUES_USED: dict[str, tuple[str, ...]] = {
    "wall_generator": ("arachne",),
    "support_type": ("tree(auto)", "normal(auto)"),
    "sparse_infill_pattern": ("line", "zig-zag", "cross-zag", "locked-zag"),
    "draft_shield": ("enabled",),
    "brim_type": ("no_brim", "auto_brim", "brim_ears"),
    "ironing_type": ("no ironing",),
    "ironing_pattern": ("rectilinear",),
    "fuzzy_skin": ("none",),
    "seam_slope_type": ("none",),
}


def rule_keys() -> set[str]:
    """Every key some rule names."""
    return {k for rule in RULES for k in rule.keys}


def groups() -> set[str]:
    return {rule.group for rule in RULES}


def condition_keys() -> set[str]:
    """Every key the conditions read, found by running each predicate against recording configs.

    Predicates short-circuit, so they run once per probe value; the values are chosen so that the usual
    ``and`` chains read on (a flag set, a count above zero, an enum value of interest).
    """
    seen: set[str] = set()
    for probe in (None, "1", "0", "arachne", "tree(auto)", "cross-zag", "rectilinear", "brim_ears",
                  "top", "enabled", "none"):
        def get(key: str, probe=probe) -> str | None:
            seen.add(key)
            return probe
        cfg = Cfg(get)
        for rule in RULES:
            for pred in (rule.enabled_if, rule.visible_if):
                if pred is not None:
                    pred(cfg)
    return seen


def key_state(cfg: Cfg, key: str) -> tuple[bool, bool]:
    """``(visible, enabled)`` of one key under ``cfg``."""
    visible = enabled = True
    for rule in _BY_KEY.get(key, ()):
        if rule.visible_if is not None and visible and not rule.visible_if(cfg):
            visible = False
        if rule.enabled_if is not None and enabled and not rule.enabled_if(cfg):
            enabled = False
    return visible, enabled


def row_state(cfg: Cfg) -> Callable[[str], tuple[bool, bool]]:
    """A ``key -> (visible, enabled)`` function, memoised per call of this function (one draw)."""
    seen: dict[str, tuple[bool, bool]] = {}

    def state(key: str) -> tuple[bool, bool]:
        if key not in seen:
            seen[key] = key_state(cfg, key)
        return seen[key]
    return state


_BY_KEY: dict[str, list[Rule]] = {}
for _rule in RULES:
    for _key in _rule.keys:
        _BY_KEY.setdefault(_key, []).append(_rule)
