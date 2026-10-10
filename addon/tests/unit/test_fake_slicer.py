# SPDX-License-Identifier: GPL-3.0-or-later
"""The fake engine's synthetic slicer, validation and arranging, tested directly."""
from __future__ import annotations

import numpy as np
import pytest
from fake_engine import compose_config, enums, normalize_config
from fake_engine.api import temp_dir
from fake_engine.errors import ArrangeError
from fake_engine.slicer import ObjectData, arrange, synthesize, validate_objects

_TRIS = np.array([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                  (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)], np.int32)
EXTRUDE = enums()["move_type"]["Extrude"]


def cube(name="cube", cx=100.0, cy=100.0, size=10.0, z0=0.0, **kw) -> ObjectData:
    r = size / 2
    v = np.array([(cx - r, cy - r, z0), (cx + r, cy - r, z0), (cx + r, cy + r, z0),
                  (cx - r, cy + r, z0), (cx - r, cy - r, z0 + size), (cx + r, cy - r, z0 + size),
                  (cx + r, cy + r, z0 + size), (cx - r, cy + r, z0 + size)], np.float32)
    return ObjectData(name, v, _TRIS.copy(), **kw)


def config(**process) -> dict:
    flat = compose_config({"name": "P"}, {"name": "Q", **process}, [{"name": "F"}])
    return normalize_config(flat)["config"]


def test_synthesize_produces_consistent_arrays_and_a_file():
    r = synthesize([cube()], config(), [], temp_dir())
    k = len(r.moves["type"])
    assert all(len(a) == k for a in r.moves.values())
    assert len(r.layers["z"]) == 50                       # 10 mm at 0.2 mm
    assert np.allclose(r.layers["z"][[0, -1]], [0.2, 10.0], atol=1e-4)
    assert int(r.layers["last"][-1]) == k - 1
    with open(r.gcode_path, "rb") as f:
        assert len(f.read()) == int(r.gcode_line_ends[-1])


def test_gcode_file_is_removed_when_the_result_is_freed():
    import gc
    import os
    r = synthesize([cube()], config(), [], temp_dir())
    path = r.gcode_path
    assert os.path.exists(path)
    del r
    gc.collect()
    assert not os.path.exists(path)


def test_layer_height_changes_the_layer_count():
    assert len(synthesize([cube()], config(layer_height="0.1", initial_layer_print_height="0.1"),
                          [], temp_dir()).layers["z"]) == 100


def test_wall_loops_and_infill_density_come_from_the_config_and_overrides():
    plain = synthesize([cube()], config(), [], temp_dir())
    thick = synthesize([cube(overrides={"wall_loops": "4"})], config(), [], temp_dir())
    assert (thick.moves["type"] == EXTRUDE).sum() > (plain.moves["type"] == EXTRUDE).sum()
    empty = synthesize([cube()], config(sparse_infill_density="0%"), [], temp_dir())
    assert enums()["role"]["InternalInfill"] not in empty.moves["role"].tolist()


def test_the_top_layer_is_top_solid_infill_and_the_first_is_bottom_surface():
    r = synthesize([cube()], config(), [], temp_dir())
    roles = enums()["role"]
    top = r.layers["first"][-1], r.layers["last"][-1]
    in_top = r.moves["role"][top[0]:top[1] + 1]
    assert roles["TopSolidInfill"] in in_top.tolist()
    in_first = r.moves["role"][r.layers["first"][0]:r.layers["last"][0] + 1]
    assert roles["BottomSurface"] in in_first.tolist() and roles["Skirt"] in in_first.tolist()


def test_a_floating_object_skips_the_empty_layers():
    r = synthesize([cube(z0=5.0)], config(), [], temp_dir())
    assert float(r.layers["z"][0]) > 5.0
    assert (np.diff(r.layers["z"]) > 0).all()


def test_two_objects_share_layers_and_are_labelled():
    r = synthesize([cube("a", cx=60), cube("b", cx=150, size=6)], config(), [], temp_dir())
    assert r.objects == ["a", "b"]
    ids = set(r.moves["object_id"].tolist())
    assert {-1, 0, 1} <= ids


def test_no_objects_gives_a_single_noop_move():
    r = synthesize([], config(), [], temp_dir())
    assert len(r.moves["type"]) == 1 and len(r.layers["z"]) == 1


def test_extruder_selects_the_filament_index():
    two = compose_config({"name": "P"}, {"name": "Q"}, [{"name": "A"}, {"name": "B"}])
    cfg = normalize_config(two)["config"]
    r = synthesize([cube(extruder=2)], cfg, [], temp_dir())
    own = (r.moves["type"] == EXTRUDE) & (r.moves["object_id"] == 0)
    assert set(r.moves["filament"][own].tolist()) == {1}
    assert len(r.stats["filament_per_extruder"]) == 2


def test_stats_time_adds_up():
    r = synthesize([cube()], config(), [], temp_dir())
    parts = sum(v[0] for v in r.stats["time_by_role_s"].values())
    parts += sum(v[0] for v in r.stats["time_by_move_type_s"].values())
    assert parts == pytest.approx(r.stats["time_s"]["normal"], rel=1e-4)
    assert r.stats["time_s"]["silent"] >= r.stats["time_s"]["normal"]


def test_output_filename_uses_the_format():
    r = synthesize([cube()], config(), [], temp_dir())
    name = r.output_filename("Part")
    assert name.startswith("Part_0.2mm_PLA_") and name.endswith(".gcode")
    assert r.output_filename("my.part").startswith("my.part_0.2mm_PLA_")


def test_missing_thumbnail_sizes_are_warned_about():
    cfg = config()
    cfg["thumbnails"] = "48x48/PNG, 300x300/PNG"
    img = np.zeros((48, 48, 4), np.uint8)
    r = synthesize([cube()], cfg, [img], temp_dir())
    msgs = [w["message"] for w in r.warnings if w["code"] == "thumbnail_missing"]
    assert len(msgs) == 1 and "300x300" in msgs[0]


def test_validation_flags_paint_open_edges_and_moves():
    bad_paint = cube("p", face_extruder=np.full(12, 3, np.uint8))
    open_mesh = cube("o")
    open_mesh.triangles = open_mesh.triangles[:-1]
    moved = cube("m", moved_to_bed=True)
    issues = validate_objects([bad_paint, open_mesh, moved], config())
    assert [i["code"] for i in issues] == ["paint_out_of_range", "mesh_open_edges", "moved_to_bed"]
    assert [i["level"] for i in issues] == ["error", "warning", "info"]
    assert validate_objects([cube()], config()) == []


def test_objects_outside_the_bed_or_too_tall_are_validation_errors():
    far = validate_objects([cube("far", cx=400)], config())
    tall = validate_objects([cube("tall", size=300, cx=128, cy=128)], config(), )
    assert [(i["code"], i["level"]) for i in far] == [("object_outside_bed", "error")]
    assert {i["code"] for i in tall} == {"object_outside_bed", "object_too_tall"}   # 300 mm wide too
    cfg = config()
    cfg["bed_exclude_area"] = "90x90,110x90,110x110,90x110"
    assert [i["code"] for i in validate_objects([cube("hit", cx=100, cy=100)], cfg)] == ["object_outside_bed"]


def test_arrange_packs_without_overlap_and_centres_on_the_bed():
    objs = [cube(f"o{i}", cx=128, cy=128, size=40) for i in range(4)]
    placements = arrange(objs, config(), 5.0)
    assert [p["index"] for p in placements] == [0, 1, 2, 3]
    xs, ys = [], []
    for p, o in zip(placements, objs):
        lo = o.vertices.min(axis=0) + p["transform"][:3, 3]
        xs.append((lo[0], lo[0] + 40))
        ys.append((lo[1], lo[1] + 40))
    for i in range(4):
        for j in range(i + 1, 4):
            overlap = xs[i][0] < xs[j][1] and xs[j][0] < xs[i][1] and ys[i][0] < ys[j][1] and ys[j][0] < ys[i][1]
            assert not overlap
    assert (min(a for a, _ in xs) + max(b for _, b in xs)) / 2 == pytest.approx(128.0)


def test_arrange_reports_what_does_not_fit():
    with pytest.raises(ArrangeError) as err:
        arrange([cube(f"big{i}", size=200) for i in range(3)], config(), None)
    assert err.value.object_names == ["big1", "big2"]
