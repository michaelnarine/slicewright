# SPDX-License-Identifier: GPL-3.0-or-later
"""``fake_engine.from_gcode``: both tag dialects, G2/G3, relative and absolute E."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import fake_engine as sc
import numpy as np
import pytest
from fake_engine.gcode import from_gcode

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gcode"
SAMPLES = ["plain_square.gcode", "bambu_square.gcode"]
ENUMS = sc.enums()
EXTRUDE, TRAVEL = ENUMS["move_type"]["Extrude"], ENUMS["move_type"]["Travel"]
RETRACT, UNRETRACT = ENUMS["move_type"]["Retract"], ENUMS["move_type"]["Unretract"]
ROLE = ENUMS["role"]
# Per layer: three straight sides and a quarter arc around the loop, plus a 4,4 diagonal.
PATH_MM = (8 + 10 + 8) + math.pi + 8 + 2 + math.hypot(4, 4)
AREA = math.pi * (1.75 / 2) ** 2


def _make_samples_module():
    spec = importlib.util.spec_from_file_location("make_samples", FIXTURES / "make_samples.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_guarantees(r) -> None:
    """The 04 section 5.3 layer guarantees plus array consistency."""
    k = len(r.moves["type"])
    first, last = r.layers["first"].astype(np.int64), r.layers["last"].astype(np.int64)
    assert first[0] == 0 and last[-1] == k - 1
    assert (first[1:] == last[:-1] + 1).all()
    lid = r.moves["layer_id"].astype(np.int64)
    idx = np.arange(k)
    assert (first[lid] <= idx).all() and (idx <= last[lid]).all()
    assert r.moves["position"].shape == (k, 3) and r.moves["time"].shape == (k, 2)
    assert all(not a.flags.writeable for a in r.moves.values())


def _volume(r) -> float:
    m = r.moves
    ext = m["type"] == EXTRUDE
    seg = np.zeros(len(ext))
    seg[1:] = np.linalg.norm(np.diff(m["position"].astype(np.float64), axis=0), axis=1)
    return float((seg * m["mm3_per_mm"] * ext).sum())


@pytest.mark.parametrize("name", SAMPLES)
def test_samples_parse_into_a_valid_result(name):
    r = from_gcode(str(FIXTURES / name))
    check_guarantees(r)
    assert r.warnings == []
    assert np.allclose(r.layers["z"], [0.2, 0.4, 0.6])
    types = set(np.unique(r.moves["type"]).tolist())
    assert {EXTRUDE, TRAVEL, RETRACT, UNRETRACT} <= types
    roles = set(np.unique(r.moves["role"][r.moves["type"] == EXTRUDE]).tolist())
    assert roles == {ROLE["ExternalPerimeter"], ROLE["InternalInfill"]}
    assert r.stats["layer_count"] == 3 and r.stats["time_s"]["normal"] > 0


@pytest.mark.parametrize("name", SAMPLES)
def test_extruded_volume_is_conserved_for_relative_and_absolute_e(name):
    r = from_gcode(str(FIXTURES / name))
    expected = 3 * 0.42 * 0.2 * PATH_MM
    assert _volume(r) == pytest.approx(expected, rel=2e-3)
    assert r.stats["filament_per_extruder"][0]["cm3"] * 1000 == pytest.approx(expected, rel=2e-3)


@pytest.mark.parametrize("name,center,radius", [
    ("plain_square.gcode", (20.0, 30.0), 2.0),     # G2 about (x0, y0+10)
    ("bambu_square.gcode", (22.0, 28.0), 2.0),     # G3 about (x0+2, y0+8)
])
def test_arcs_are_tessellated_onto_the_circle(name, center, radius):
    path = FIXTURES / name
    r = from_gcode(str(path))
    lines = path.read_text().splitlines()
    arc_lines = [i + 1 for i, t in enumerate(lines) if t.startswith(("G2 ", "G3 "))]
    assert len(arc_lines) == 3
    sel = np.isin(r.moves["gcode_line"], arc_lines)
    pts = r.moves["position"][sel][:, :2].astype(np.float64)
    assert len(pts) >= 3 * 15            # a quarter turn at 5 degrees is 18 segments
    d = np.hypot(pts[:, 0] - center[0], pts[:, 1] - center[1])
    assert np.allclose(d, radius, atol=1e-3)
    assert (r.moves["type"][sel] == EXTRUDE).all()


def test_arc_direction_matters(tmp_path):
    cw = "G90\nM82\nG1 X10 Y0 F600\nG2 X0 Y10 I-10 J0 E5\n"
    ccw = "G90\nM82\nG1 X10 Y0 F600\nG3 X0 Y10 I-10 J0 E5\n"
    for text, quarter in ((cw, False), (ccw, True)):
        r = _parse(tmp_path, text)
        pts = r.moves["position"][r.moves["type"] == EXTRUDE][:, :2]
        # CCW from (10,0) to (0,10) about the origin is a quarter turn; CW is three quarters
        assert (len(pts) < 25) == quarter


def test_line_index_matches_the_file():
    path = FIXTURES / "plain_square.gcode"
    r = from_gcode(str(path))
    data = path.read_bytes()
    assert int(r.gcode_line_ends[-1]) == len(data)
    assert len(r.gcode_line_ends) == data.count(b"\n")
    assert r.moves["gcode_line"].min() >= 1 and r.moves["gcode_line"].max() <= len(r.gcode_line_ends)
    assert r.gcode_path == str(path)


def test_the_source_file_is_not_deleted_with_the_result(tmp_path):
    f = tmp_path / "a.gcode"
    f.write_text("G90\nG1 X1 Y1 E1 F600\n")
    import gc
    r = from_gcode(str(f))
    del r
    gc.collect()
    assert f.exists()


def test_samples_match_their_generator():
    mod = _make_samples_module()
    assert (FIXTURES / "plain_square.gcode").read_text() == mod.build(bambu=False)
    assert (FIXTURES / "bambu_square.gcode").read_text() == mod.build(bambu=True)


# --- smaller dialect and state cases ---------------------------------------------------------

def _parse(tmp_path, text, name="t.gcode"):
    f = tmp_path / name
    f.write_bytes(text.encode())
    return from_gcode(str(f))


def test_relative_and_absolute_e_agree(tmp_path):
    rel = _parse(tmp_path, "M83\nG1 X10 Y0 E1 F600\nG1 X10 Y10 E1\n", "rel.gcode")
    ab = _parse(tmp_path, "M82\nG1 X10 Y0 E1 F600\nG1 X10 Y10 E2\n", "abs.gcode")
    assert _volume(rel) == pytest.approx(_volume(ab))
    assert _volume(rel) == pytest.approx(2 * AREA, rel=1e-6)


def test_g92_resets_the_extruder_position(tmp_path):
    r = _parse(tmp_path, "M82\nG1 X10 E5 F600\nG92 E0\nG1 X20 E2\n")
    ext = r.moves["type"] == EXTRUDE
    assert ext.sum() == 2           # not a retract after the reset


def test_dialect_tags_set_role_width_and_height(tmp_path):
    plain = _parse(tmp_path, ";TYPE:Top surface\n;WIDTH:0.5\n;HEIGHT:0.3\nG1 X5 E1 F600\n")
    bambu = _parse(tmp_path, "; FEATURE: Top surface\n; LINE_WIDTH: 0.5\n; LAYER_HEIGHT: 0.3\n"
                   "G1 X5 E1 F600\n")
    for r in (plain, bambu):
        i = int(np.flatnonzero(r.moves["type"] == EXTRUDE)[0])
        assert r.moves["role"][i] == ROLE["TopSolidInfill"]
        assert r.moves["width"][i] == pytest.approx(0.5)
        assert r.moves["height"][i] == pytest.approx(0.3)


def test_both_layer_tags_start_layers(tmp_path):
    for tag in (";LAYER_CHANGE", "; CHANGE_LAYER"):
        r = _parse(tmp_path, f"{tag}\nG1 X1 E1 F600\n{tag}\nG1 Z1 X2 E2\n")
        assert len(r.layers["z"]) == 2


def test_files_without_layer_tags_split_layers_on_z(tmp_path):
    r = _parse(tmp_path, "G1 Z0.2 F600\nG1 X5 E1\nG1 Z0.4\nG1 X0 E2\nG1 Z0.6\nG1 X5 E3\n")
    check_guarantees(r)
    assert len(r.layers["z"]) == 3


def test_tool_changes_set_the_filament_index(tmp_path):
    r = _parse(tmp_path, "T0\nG1 X5 E1 F600\nT1\nG1 X10 E2\n")
    ext = r.moves["type"] == EXTRUDE
    assert r.moves["filament"][ext].tolist() == [0, 1]
    assert len(r.stats["filament_per_extruder"]) == 2


def test_object_labels_populate_objects(tmp_path):
    r = _parse(tmp_path, "; printing object Cube id:0 copy 0\nG1 X5 E1 F600\n"
                         "; stop printing object Cube id:0 copy 0\nG1 X9 E2\n")
    assert r.objects == ["Cube"]
    ext = r.moves["type"] == EXTRUDE
    assert r.moves["object_id"][ext].tolist() == [0, -1]


def test_wipe_moves_are_typed(tmp_path):
    r = _parse(tmp_path, "G1 X5 E1 F600\n; WIPE_START\nG1 X3 E-0.2\n; WIPE_END\n")
    assert ENUMS["move_type"]["Wipe"] in r.moves["type"].tolist()


def test_temperature_fan_and_feedrate_are_tracked(tmp_path):
    r = _parse(tmp_path, "M104 S215\nM106 S255\nG1 X10 E1 F1200\n")
    i = int(np.flatnonzero(r.moves["type"] == EXTRUDE)[0])
    assert r.moves["temperature"][i] == 215 and r.moves["fan"][i] == pytest.approx(100.0)
    assert r.moves["feedrate"][i] == pytest.approx(20.0)
    assert r.moves["time"][i, 0] == pytest.approx(0.5)


def test_crlf_and_missing_trailing_newline(tmp_path):
    r = _parse(tmp_path, "G1 X5 E1 F600\r\nG1 X9 E2")
    assert len(r.gcode_line_ends) == 2 and int(r.gcode_line_ends[-1]) == len("G1 X5 E1 F600\r\nG1 X9 E2")
    assert (r.moves["type"] == EXTRUDE).sum() == 2


def test_unparsable_motion_lines_become_warnings(tmp_path):
    r = _parse(tmp_path, "G1 X Y5 E1 F600\nG1 X5 E1\n")
    assert [w["code"] for w in r.warnings] == ["gcode_processor"]
    assert (r.moves["type"] == EXTRUDE).sum() == 1


def test_empty_file_gives_a_valid_one_move_result(tmp_path):
    r = _parse(tmp_path, "; nothing here\n")
    check_guarantees(r)
    assert len(r.moves["type"]) == 1


# --- round trip with the fake's own synthetic slicer -----------------------------------------

def test_the_fakes_own_gcode_parses_back_to_the_same_moves():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contract"))
    from contract_helpers import sliced
    _, made = sliced(sc)
    parsed = from_gcode(made.gcode_path)
    check_guarantees(parsed)
    for name in ("Extrude", "Travel", "Retract", "Unretract"):
        t = ENUMS["move_type"][name]
        assert (parsed.moves["type"] == t).sum() == (made.moves["type"] == t).sum(), name
    ext = made.moves["type"] == EXTRUDE
    assert np.array_equal(parsed.moves["role"][parsed.moves["type"] == EXTRUDE],
                          made.moves["role"][ext])
    assert len(parsed.layers["z"]) == len(made.layers["z"])
    assert np.allclose(parsed.layers["z"], made.layers["z"], atol=1e-3)
    assert _volume(parsed) == pytest.approx(_volume(made), rel=2e-3)


# --- robustness cases from review ----------------------------------------------------------

@pytest.mark.parametrize("cmd", ["T255", "T1000", "T9999"])
def test_large_t_commands_are_not_tool_changes(tmp_path, cmd):
    r = _parse(tmp_path, f"T0\nG1 X5 E1 F600\n{cmd}\nG1 X10 E2\n")
    ext = r.moves["type"] == EXTRUDE
    assert r.moves["filament"][ext].tolist() == [0, 0]
    assert len(r.stats["filament_per_extruder"]) == 1
    warns = [w for w in r.warnings if w["code"] == "gcode_processor"]
    assert len(warns) == 1 and cmd in warns[0]["message"]


def test_the_same_ignored_t_command_warns_once(tmp_path):
    r = _parse(tmp_path, "T1000\nG1 X5 E1 F600\nT1000\nG1 X9 E2\n")
    assert len(r.warnings) == 1


def test_tools_beyond_the_configured_filament_count_are_ignored(tmp_path):
    r = _parse(tmp_path, "; filament_diameter = 1.75,1.75\nT1\nG1 X5 E1 F600\nT2\nG1 X9 E2\n")
    ext = r.moves["type"] == EXTRUDE
    assert r.moves["filament"][ext].tolist() == [1, 1]
    assert len(r.stats["filament_per_extruder"]) == 2
    assert any("T2" in w["message"] for w in r.warnings)


def test_g91_makes_e_relative_even_under_m82(tmp_path):
    r = _parse(tmp_path, "M82\nG91\nG1 X5 E1 F600\nG1 X5 E1\nG90\n")
    assert (r.moves["type"] == EXTRUDE).sum() == 2           # absolute E would make the 2nd a no-op
    assert _volume(r) == pytest.approx(2 * AREA)


def test_g90_restores_absolute_e_under_m82(tmp_path):
    r = _parse(tmp_path, "M82\nG91\nG1 X5 E1 F600\nG90\nG1 X20 E2\n")
    assert _volume(r) == pytest.approx(AREA * 1 + AREA * 1)  # E 1 relative, then 1 -> 2 absolute


def test_arc_with_r_and_identical_endpoints_is_skipped_with_a_warning(tmp_path):
    r = _parse(tmp_path, "G90\nM82\nG1 X5 Y5 F600\nG2 X5 Y5 R3 E1\nG1 X9 Y5 E2\n")
    assert [w["code"] for w in r.warnings] == ["gcode_processor"]
    assert "ambiguous" in r.warnings[0]["message"]
    assert (r.moves["type"] == EXTRUDE).sum() == 1           # only the straight move after it
    assert _volume(r) == pytest.approx(AREA * 1)             # E bookkeeping continued at 1 -> 2


def test_arc_with_ij_and_identical_endpoints_is_a_full_circle(tmp_path):
    r = _parse(tmp_path, "G90\nM82\nG1 X5 Y0 F600\nG2 X5 Y0 I-5 J0 E3\n")
    pts = r.moves["position"][r.moves["type"] == EXTRUDE][:, :2].astype(float)
    assert len(pts) >= 70 and np.allclose(np.hypot(pts[:, 0], pts[:, 1]), 5.0, atol=1e-3)
    assert r.warnings == []


def test_words_without_spaces(tmp_path):
    r = _parse(tmp_path, "M82\nG1X10Y0E1F600\nG1X10Y10E2\nM104S215\nG1X0E3\n")
    assert (r.moves["type"] == EXTRUDE).sum() == 3
    assert r.moves["position"][-1, 0] == 0 and r.warnings == []
    assert r.moves["temperature"][-1] == 215


def test_m106_for_another_fan_does_not_change_the_part_fan(tmp_path):
    r = _parse(tmp_path, "M106 S255\nM106 P2 S128\nG1 X5 E1 F600\nM107 P2\nG1 X9 E2\n")
    fans = r.moves["fan"][r.moves["type"] == EXTRUDE]
    assert fans.tolist() == [pytest.approx(100.0)] * 2


@pytest.mark.parametrize("name", SAMPLES)
def test_each_outer_wall_run_starts_with_a_seam_move(name):
    m = from_gcode(str(FIXTURES / name)).moves
    seam = ENUMS["move_type"]["Seam"]
    idx = np.flatnonzero(m["type"] == seam)
    assert len(idx) == 3                                           # one per layer
    for i in idx:
        assert m["type"][i + 1] == EXTRUDE and m["role"][i + 1] == ROLE["ExternalPerimeter"]
        assert (m["position"][i] == m["position"][i - 1]).all() or m["type"][i - 1] != EXTRUDE
        assert m["time"][i].sum() == 0 and m["layer_id"][i] == m["layer_id"][i + 1]
