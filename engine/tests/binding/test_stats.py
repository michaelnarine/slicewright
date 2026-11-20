# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 6: stats (04 section 5.4) and the unified issue list (section 5.5)."""
import numpy as np
import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402
import heavy  # noqa: E402
from test_add_object import config  # noqa: E402
from test_validating_errors import box  # noqa: E402


def two_filament_result():
    j = sc.SliceJob()
    j.set_config(config(2))
    cx, cy = cube_case.bed_centre(cube_case.profiles()["machine"])
    j.add_object("a", *box(cx - 30, cy, size=15.0))
    j.add_object("b", *box(cx + 30, cy, size=15.0), extruder=2)
    return j.run()


@pytest.fixture(scope="module")
def results():
    return {"cube": cube_case.build_job(sc).run(), "two": two_filament_result(), "heavy": heavy.heavy_job(sc, subdivisions=3).run()}


def test_time_breakdown_sums_to_the_total_within_one_percent(results):
    for name, r in results.items():
        s = r.stats
        for column, total in ((0, s["time_s"]["normal"]), (1, s["time_s"]["silent"])):
            parts = sum(v[column] for v in s["time_by_role_s"].values()) + sum(v[column] for v in s["time_by_move_type_s"].values())
            if total == 0:
                assert parts == 0
            else:
                assert abs(parts - total) <= 0.01 * total, (name, column, parts, total)


def test_role_and_type_names_are_the_enum_names(results):
    e = sc.enums()
    for r in results.values():
        assert set(r.stats["time_by_role_s"]) <= set(e["role"])
        assert set(r.stats["time_by_move_type_s"]) <= set(e["move_type"]) - {"Extrude"}
        assert set(r.stats["used_filament_per_role"]) <= set(e["role"])


def test_the_move_arrays_and_the_stats_agree(results):
    r = results["cube"]
    m = r.moves
    types = sc.enums()["move_type"]
    travel = m["type"] == types["Travel"]
    summed = float(m["time"][travel][:, 0].sum())
    assert abs(r.stats["time_by_move_type_s"]["Travel"][0] - summed) < 1e-3 * max(1.0, summed)
    assert r.stats["total_travel_mm"] > 0


def test_filament_numbers_are_consistent(results):
    for name in ("cube", "two", "heavy"):
        s = results[name].stats
        for f in s["filament_per_extruder"]:
            assert set(f) == {"mm", "cm3", "g", "cost"}
            if f["mm"] > 0:
                assert f["cm3"] > 0 and f["g"] > 0
                # 1.75 mm filament, density 1.24 g/cm3 in the spike profile: g = cm3 * density
                assert abs(f["g"] / f["cm3"] - 1.24) < 0.2
        total_g = sum(f["g"] for f in s["filament_per_extruder"])
        assert abs(total_g - float(s["display"]["total_weight_g"])) <= 0.01 + 0.005 * total_g
        # the per-role table (metres, grams) covers the extruded material
        role_g = sum(v["g"] for v in s["used_filament_per_role"].values())
        assert role_g > 0 and role_g <= total_g * 1.05


def test_two_filaments_have_two_entries_and_count_changes(results):
    s = results["two"].stats
    assert len(s["filament_per_extruder"]) == 2 and all(f["mm"] > 0 for f in s["filament_per_extruder"])
    assert s["total_tool_changes"] >= 1 or s["total_filament_changes"] >= 1
    assert len(s["flush_per_filament_g"]) == 2
    single = results["cube"].stats
    assert len(single["filament_per_extruder"]) == 1 and single["total_tool_changes"] == 0


def test_stats_shape_and_layer_count(results):
    for r in results.values():
        s = r.stats
        assert s["layer_count"] == len(r.layers["z"])
        assert s["time_s"]["normal"] > 0 and s["prepare_time_s"] >= 0
        assert all(isinstance(v, str) for v in s["display"].values())
        assert s["threads"] >= 1


def test_warnings_are_issues_and_deduplicated():
    p = cube_case.profiles()
    flat = sc.normalize_config(sc.compose_config(p["machine"], p["process"], [p["filament"]]))["config"]
    flat["sparse_infill_pattern"] = "no_such_pattern"  # substituted by Orca's loader: config_substitution
    j = sc.SliceJob()
    j.set_config(flat)
    j.add_object("cube", *box(*cube_case.bed_centre(p["machine"])))
    r = j.run()
    keys = [(w["level"], w["code"], w["message"], w["opt_key"], w["object_name"]) for w in r.warnings]
    assert len(keys) == len(set(keys))
    assert any(w["code"] == "config_substitution" and w["opt_key"] == "sparse_infill_pattern" for w in r.warnings)
    for w in r.warnings:
        assert set(w) == {"level", "code", "message", "opt_key", "object_name"}
        assert w["level"] in ("error", "warning", "info")


def test_a_step_warning_names_the_print_object_it_is_about():
    """Orca's per-step warnings carry the PrintObject's id (PrintStateBase::warning_object_id), not the ModelObject's:
    the engine maps it back to the add_object name. A slab on a thin stem with supports off makes Orca warn
    ("It seems object tee has floating cantilever. Please re-orient the object or enable support generation.") about that
    object only."""
    from test_paint import tee

    p = cube_case.profiles()
    cx, cy = cube_case.bed_centre(p["machine"])
    flat = sc.normalize_config(sc.compose_config(p["machine"], p["process"], [p["filament"]]))["config"]
    j = sc.SliceJob()
    j.set_config(flat)
    j.add_object("plain", *box(cx - 70, cy, size=15.0))
    j.add_object("tee", *tee(cx + 30, cy))
    r = j.run()
    needs_support = [w for w in r.warnings if w["code"] == "slicing" and "enable support generation" in w["message"]]
    assert needs_support, [(w["code"], w["message"]) for w in r.warnings]
    assert {w["object_name"] for w in needs_support} == {"tee"}
