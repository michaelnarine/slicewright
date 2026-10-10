# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 11: output_filename (Orca's filename_format) and the bed-frame wipe_tower (04 sections 5.1, 5.5)."""
import re

import numpy as np
import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402
from test_validating_errors import box  # noqa: E402


def flat(machine_extra=None, process_extra=None, filaments=1, project=None):
    p = cube_case.profiles()
    fil = [p["filament"]] + [{**p["filament"], "name": f"F{i}", "filament_colour": [f"#11223{i}"]} for i in range(1, filaments)]
    proj = {"flush_volumes_matrix": ",".join("0" if i == j else "100" for i in range(filaments) for j in range(filaments))} if filaments > 1 else {}
    proj.update(project or {})
    return sc.normalize_config(sc.compose_config({**p["machine"], **(machine_extra or {})}, {**p["process"], **(process_extra or {})}, fil, proj or None))["config"]


def run(cfg, objects=1):
    j = sc.SliceJob()
    j.set_config(cfg)
    cx, cy = cube_case.bed_centre(cube_case.profiles()["machine"])
    for i in range(objects):
        j.add_object(f"o{i}", *box(cx + 30 * (i - (objects - 1) / 2), cy, size=15.0), extruder=1 + i % 2 if objects > 1 else 0)
    return j.run()


# --- output_filename --------------------------------------------------------------------------------------

def test_the_default_format_gives_a_gcode_name_with_the_basename():
    name = run(flat()).output_filename("Cube")
    assert name.startswith("Cube") and name.endswith(".gcode")


def test_the_format_is_evaluated_against_the_config_and_the_statistics():
    r = run(flat(process_extra={"filename_format": "{input_filename_base}_{layer_height}mm_{filament_type[0]}_{print_time}_{total_weight}g.gcode"}))
    name = r.output_filename("Benchy")
    m = re.fullmatch(r"Benchy_0\.2mm_PLA_(\S+)_([\d.]+)g\.gcode", name)
    assert m, name
    # the print time is Orca's short form (for example 27m42s) and the weight is the one in the statistics
    assert re.fullmatch(r"(\d+d)?(\d+h)?(\d+m)?\d*s?", m.group(1)), m.group(1)
    assert abs(float(m.group(2)) - sum(f["g"] for f in r.stats["filament_per_extruder"])) < 0.01


def test_the_extension_is_added_when_the_format_has_none_and_the_basename_may_repeat():
    r = run(flat(process_extra={"filename_format": "{input_filename_base}-{input_filename_base}"}))
    assert r.output_filename("A b") == "A b-A b.gcode"
    assert r.output_filename("x") == "x-x.gcode"  # several calls, same result


def test_input_filename_placeholder_carries_the_extension():
    r = run(flat(process_extra={"filename_format": "{input_filename}"}))
    assert r.output_filename("model") == "model.gcode"


def test_a_broken_format_is_reported_when_the_name_is_asked_for():
    r = run(flat(process_extra={"filename_format": "{no_such_placeholder_xyz}.gcode"}))
    with pytest.raises(sc.ConfigError):
        r.output_filename("x")


def test_output_filename_needs_a_str():
    r = run(flat())
    with pytest.raises(TypeError):
        r.output_filename(None)


# --- wipe_tower ---------------------------------------------------------------------------------------------

def tower_result(**extra):
    cfg = flat(filaments=2, process_extra={"enable_prime_tower": "1", "prime_tower_width": "30"},
               project={"wipe_tower_x": "120", "wipe_tower_y": "60", **extra})
    return run(cfg, objects=2)


def test_no_tower_for_one_filament():
    assert run(flat()).wipe_tower is None


def test_the_tower_is_in_the_bed_frame():
    r = tower_result()
    t = r.wipe_tower
    assert set(t) == {"x", "y", "width", "depth", "height", "rotation_deg"}
    assert t["width"] > 20 and t["depth"] > 5 and t["height"] > 5
    # the tower origin is wipe_tower_x/y; its box lies on or next to it, in bed coordinates (not tower-local)
    assert 100 < t["x"] < 160 and 40 < t["y"] < 120, t


def test_every_wipe_tower_extrusion_lies_in_the_reported_box():
    r = tower_result()
    t = r.wipe_tower
    m = r.moves
    ext = (m["type"] == sc.enums()["move_type"]["Extrude"]) & (m["role"] == sc.enums()["role"]["WipeTower"])
    assert ext.any()
    xy = m["position"][ext][:, :2].astype(np.float64)
    a = np.deg2rad(t["rotation_deg"])
    u, v = np.array([np.cos(a), np.sin(a)]), np.array([-np.sin(a), np.cos(a)])
    rel = xy - np.array([t["x"], t["y"]])
    s, d = rel @ u, rel @ v
    tol = 0.6
    assert s.min() >= -tol and s.max() <= t["width"] + tol and d.min() >= -tol and d.max() <= t["depth"] + tol, (s.min(), s.max(), d.min(), d.max(), t)


def test_the_rotation_moves_the_box_but_not_its_size():
    plain = tower_result().wipe_tower
    rotated = tower_result(wipe_tower_rotation_angle="30").wipe_tower
    assert abs(rotated["rotation_deg"] - 30.0) < 1e-9
    assert abs(rotated["width"] - plain["width"]) < 1e-6 and abs(rotated["depth"] - plain["depth"]) < 1e-6
    r = tower_result(wipe_tower_rotation_angle="30")
    m = r.moves
    t = r.wipe_tower
    ext = (m["type"] == sc.enums()["move_type"]["Extrude"]) & (m["role"] == sc.enums()["role"]["WipeTower"])
    a = np.deg2rad(30.0)
    rel = m["position"][ext][:, :2].astype(np.float64) - np.array([t["x"], t["y"]])
    s, d = rel @ np.array([np.cos(a), np.sin(a)]), rel @ np.array([-np.sin(a), np.cos(a)])
    assert s.min() >= -0.6 and s.max() <= t["width"] + 0.6 and d.min() >= -0.6 and d.max() <= t["depth"] + 0.6
