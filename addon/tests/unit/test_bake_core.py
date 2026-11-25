# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake geometry (03 section 7.11, ``core/bake.py``): selection, paths, curve and prism arrays. No Blender."""
from __future__ import annotations

import numpy as np
import pytest

from fake_engine.api import MOVE_TYPES, ROLES, enums
from slicewright.core import bake

T = bake.MoveTypes.from_enums(enums())
EXT, TRV, RET = MOVE_TYPES["Extrude"], MOVE_TYPES["Travel"], MOVE_TYPES["Retract"]
PERI, INFILL = ROLES["Perimeter"], ROLES["InternalInfill"]


def table(rows):
    """A move table from ``(type, role, x, y, z, layer, filament, object, width, height)`` rows (move 0 is a Noop start)."""
    cols = list(zip(*rows))
    n = len(rows)
    f32 = lambda c: np.asarray(c, np.float32)
    feed = np.linspace(20.0, 120.0, n).astype(np.float32)
    return {
        "type": np.asarray(cols[0], np.uint8), "role": np.asarray(cols[1], np.uint8),
        "position": np.stack((f32(cols[2]), f32(cols[3]), f32(cols[4])), axis=1),
        "layer_id": np.asarray(cols[5], np.uint32), "filament": np.asarray(cols[6], np.uint8),
        "object_id": np.asarray(cols[7], np.int32), "width": f32(cols[8]), "height": f32(cols[9]),
        "feedrate": feed, "mm3_per_mm": np.full(n, 0.08, np.float32)}


def row(kind, x, y, z=0.2, role=PERI, layer=0, fil=0, obj=0, w=0.45, h=0.2):
    return (kind, role, x, y, z, layer, fil, obj, w, h)


def square(layer=0, z=0.2, role=PERI, obj=0, fil=0):
    """A start (travel) then four extruded sides: a path of 4 segments."""
    pts = [(10, 10), (20, 10), (20, 20), (10, 20), (10, 10)]
    return ([row(TRV, *pts[0], z, role, layer, fil, obj, 0, 0)]
            + [row(EXT, x, y, z, role, layer, fil, obj) for x, y in pts[1:]])


def random_table(n, seed=1):
    rng = np.random.default_rng(seed)
    kinds = rng.choice([EXT] * 8 + [TRV, RET], n)
    kinds[0] = MOVE_TYPES["Noop"]
    rows = []
    for i in range(n):
        rows.append(row(int(kinds[i]), *rng.uniform(0, 100, 2), z=0.2 * (i // 40 + 1), role=int(rng.integers(1, 5)),
                        layer=i // 40, fil=int(rng.integers(0, 2)), obj=int(i % 120 >= 60),
                        w=float(rng.uniform(0.3, 0.6)), h=0.2))
    return table(rows)


# ----------------------------------------------------------------------------- selection

def test_a_closed_square_is_one_path_of_four_segments_and_move_zero_is_never_chosen():
    m = table([row(MOVE_TYPES["Noop"], 0, 0, 0)] + square())
    sel = bake.select(m, bake.Filter(0, len(m["type"]) - 1), T)
    assert sel.idx.tolist() == [2, 3, 4, 5] and sel.start.tolist() == [True, False, False, False]
    assert (sel.n_segments, sel.n_paths, sel.n_points) == (4, 1, 5)


def test_paths_break_at_a_gap_and_at_every_change_of_feature_layer_filament_or_object():
    base = [row(TRV, 0, 0), row(EXT, 1, 0), row(EXT, 2, 0)]
    cases = {
        "gap": base + [row(TRV, 5, 5), row(EXT, 6, 5)],
        "role": base + [row(EXT, 3, 0, role=INFILL)],
        "layer": base + [row(EXT, 3, 0, layer=1)],
        "filament": base + [row(EXT, 3, 0, fil=1)],
        "object": base + [row(EXT, 3, 0, obj=1)],
    }
    for name, rows in cases.items():
        sel = bake.select(table(rows), bake.Filter(0, len(rows) - 1), T)
        assert sel.n_paths == 2, name
        assert sel.start.tolist()[:3] == [True, False, True], name
    assert bake.select(table(base), bake.Filter(0, 2), T).n_paths == 1


def test_the_move_range_and_the_role_mask_filter_extrusions():
    rows = [row(TRV, 0, 0)] + [row(EXT, i, 0, role=PERI) for i in range(1, 4)] + [row(EXT, i, 1, role=INFILL) for i in range(4, 8)]
    m = table(rows)
    assert bake.select(m, bake.Filter(2, 5), T).idx.tolist() == [2, 3, 4, 5]
    only_infill = bake.select(m, bake.Filter(0, 7, role_mask=1 << INFILL), T)
    assert only_infill.idx.tolist() == [4, 5, 6, 7] and only_infill.n_paths == 1
    assert bake.select(m, bake.Filter(0, 7, role_mask=0), T).n_segments == 0
    assert bake.select(m, bake.Filter(5, 2), T).n_segments == 0           # an empty range
    assert bake.select(m, bake.Filter(-4, 99), T).n_segments == 7         # clamped to the table


def test_a_role_filter_that_removes_a_middle_feature_splits_the_path_around_it():
    rows = [row(TRV, 0, 0)] + [row(EXT, 1, 0, role=PERI), row(EXT, 2, 0, role=INFILL), row(EXT, 3, 0, role=PERI)]
    sel = bake.select(table(rows), bake.Filter(0, 3, role_mask=1 << PERI), T)
    assert sel.idx.tolist() == [1, 3] and sel.start.tolist() == [True, True]


def test_travel_is_chosen_only_when_asked_and_is_its_own_feature_type():
    rows = [row(TRV, 0, 0), row(EXT, 1, 0), row(TRV, 5, 5), row(TRV, 6, 5), row(EXT, 7, 5)]
    m = table(rows)
    assert bake.select(m, bake.Filter(0, 4), T).idx.tolist() == [1, 4]
    sel = bake.select(m, bake.Filter(0, 4, travel=True), T)
    assert sel.idx.tolist() == [1, 2, 3, 4] and sel.start.tolist() == [True, True, False, True]
    assert bake.roles_present(m, sel, T) == [PERI, bake.TRAVEL_KEY]
    assert bake.feature_keys(m, sel.idx, T).tolist() == [PERI, bake.TRAVEL_KEY, bake.TRAVEL_KEY, PERI]


def test_split_groups_keeps_whole_paths_per_object_in_order_of_first_appearance():
    rows = ([row(TRV, 0, 0)] + [row(EXT, i, 0, obj=1) for i in range(1, 3)] + [row(TRV, 5, 5, obj=0)]
            + [row(EXT, 6, 5, obj=0), row(EXT, 7, 5, obj=0)] + [row(EXT, 8, 5, obj=-1)])
    m = table(rows)
    sel = bake.select(m, bake.Filter(0, len(rows) - 1), T)
    groups = bake.split_groups(m, sel)
    assert [g for g, _ in groups] == [1, 0, -1]
    assert [s.idx.tolist() for _, s in groups] == [[1, 2], [4, 5], [6]]
    assert all(s.start[0] and s.n_paths == 1 for _, s in groups)
    assert sum(s.n_paths for _, s in groups) == sel.n_paths
    assert bake.split_groups(m, bake.Selection(np.zeros(0, np.int64), np.zeros(0, bool))) == []
    assert len(bake.split_groups(m, sel.subset(m["object_id"][sel.idx] == 1))) == 1


def test_part_ranges_and_path_chunks_cover_everything_once():
    assert bake.part_ranges(10, 4) == [(0, 4), (4, 8), (8, 10)] and bake.part_ranges(0, 4) == []
    with pytest.raises(ValueError):
        bake.part_ranges(3, 0)
    m = random_table(900)
    sel = bake.select(m, bake.Filter(0, 899, travel=True), T)
    for cap in (1, 7, 50, 10_000):
        chunks = bake.path_chunks(sel, cap)
        assert chunks[0][0] == 0 and chunks[-1][1] == sel.n_paths
        assert all(a1 == b0 for (_, a1), (b0, _) in zip(chunks, chunks[1:]))
        pts = np.diff(np.append(sel.path_first, sel.n_segments)) + 1
        for p0, p1 in chunks:
            assert p1 > p0 and (pts[p0:p1].sum() <= cap or p1 - p0 == 1)
    assert bake.path_chunks(bake.Selection(np.zeros(0, np.int64), np.zeros(0, bool)), 10) == []


def test_material_lut_maps_a_feature_type_to_its_slot():
    lut = bake.material_lut([2, 5, bake.TRAVEL_KEY])
    assert lut[2] == 0 and lut[5] == 1 and lut[bake.TRAVEL_KEY] == 2 and len(lut) == 33


# ----------------------------------------------------------------------------- curves

def build_curves(m, sel, scale=1.0, chunk=10_000):
    keys = bake.roles_present(m, sel, T)
    lut = bake.material_lut(keys)
    out = bake.alloc_curves(sel)
    for p0, p1 in bake.path_chunks(sel, chunk):
        bake.fill_curves(out, m, sel, p0, p1, T, scale, lut)
    return out


def test_curve_points_are_the_start_and_end_positions_of_the_segments_centred_on_the_extrusion():
    m = table([row(TRV, 1, 2, 0.4)] + [row(EXT, 4, 6, 0.4, w=0.5, h=0.2), row(EXT, 4, 9, 0.4, w=0.6, h=0.2)])
    sel = bake.select(m, bake.Filter(0, 2), T)
    out = bake.alloc_curves(sel)
    bake.fill_curves(out, m, sel, 0, 1, T, 1.0, bake.material_lut([PERI]))
    assert out.sizes.tolist() == [3]
    np.testing.assert_allclose(out.position, [[1, 2, 0.3], [4, 6, 0.3], [4, 9, 0.3]], atol=1e-6)
    np.testing.assert_allclose(out.radius, [0.25, 0.25, 0.3], atol=1e-6)            # the first point copies segment 1
    assert out.attributes["width"].tolist() == pytest.approx([0.5, 0.5, 0.6])
    assert out.attributes["feature_type"].tolist() == [PERI] * 3
    assert out.attributes["layer"].tolist() == [0, 0, 0] and out.material_index.tolist() == [0]


def test_bake_points_are_written_in_the_world_frame_shifted_by_the_bed_centre():
    """Moves are in the engine's bed frame; the baked geometry is in Blender's world frame (core.frame)."""
    m = table([row(TRV, 138, 130, 0.4), row(EXT, 148, 130, 0.4, w=0.5, h=0.2)])
    sel = bake.select(m, bake.Filter(0, 1), T)
    lut = bake.material_lut([PERI])
    off = (128.0, 128.0, 0.0)
    out = bake.alloc_curves(sel)
    bake.fill_curves(out, m, sel, 0, 1, T, 1.0, lut, off)
    np.testing.assert_allclose(out.position, [[10, 2, 0.3], [20, 2, 0.3]], atol=1e-5)
    plain = bake.alloc_curves(sel)
    bake.fill_curves(plain, m, sel, 0, 1, T, 1.0, lut)                                  # no offset: unchanged
    np.testing.assert_allclose(plain.position[:, 0], [138, 148], atol=1e-5)
    a, b = (bake.alloc_prisms(sel, 0, 1, 6) for _ in range(2))
    bake.fill_prisms(a, m, sel, 0, 1, 0, T, 1.0, lut, off)
    bake.fill_prisms(b, m, sel, 0, 1, 0, T, 1.0, lut)
    np.testing.assert_allclose(b.co - a.co, np.tile([128.0, 128.0, 0.0], (len(a.co), 1)), atol=1e-4)


def test_curve_attributes_hold_slicer_units_and_geometry_follows_the_scale():
    m = table([row(TRV, 0, 0), row(EXT, 10, 0, w=0.4)])
    sel = bake.select(m, bake.Filter(0, 1), T)
    out = bake.alloc_curves(sel)
    bake.fill_curves(out, m, sel, 0, 1, T, 0.001, bake.material_lut([PERI]))
    np.testing.assert_allclose(out.position[:, 0], [0.0, 0.01], atol=1e-9)
    np.testing.assert_allclose(out.radius, 0.0002, atol=1e-9)
    assert out.attributes["width"].tolist() == pytest.approx([0.4, 0.4])               # still mm
    assert out.attributes["speed"][1] == pytest.approx(m["feedrate"][1])
    assert out.attributes["flow"][1] == pytest.approx(m["feedrate"][1] * 0.08)
    assert out.attributes["filament"].dtype == np.int32 and out.attributes["speed"].dtype == np.float32


def test_travel_curves_are_thin_and_marked_with_the_travel_feature_type():
    m = table([row(TRV, 0, 0), row(EXT, 1, 0), row(TRV, 5, 5, 0.2, 0, 0, 0, 0, 0.0, 0.0), row(TRV, 6, 5, 0.2, 0, 0, 0, 0, 0.0, 0.0)])
    sel = bake.select(m, bake.Filter(0, 3, travel=True), T)
    out = build_curves(m, sel)
    assert out.sizes.tolist() == [2, 3]
    assert out.attributes["feature_type"].tolist() == [PERI, PERI, bake.TRAVEL_KEY, bake.TRAVEL_KEY, bake.TRAVEL_KEY]
    assert out.radius[2:].tolist() == pytest.approx([bake.TRAVEL_SIZE_MM / 2] * 3)
    assert out.material_index.tolist() == [0, 1]


@pytest.mark.parametrize("chunk", [1, 5, 40, 100_000])
def test_curves_are_the_same_whatever_the_chunking_and_agree_with_a_plain_loop(chunk):
    m = random_table(600, seed=4)
    sel = bake.select(m, bake.Filter(0, 599, travel=True), T)
    got, whole = build_curves(m, sel, 0.5, chunk), build_curves(m, sel, 0.5, 10**9)
    np.testing.assert_array_equal(got.position, whole.position)
    np.testing.assert_array_equal(got.radius, whole.radius)
    for name in bake.ATTRIBUTES:
        np.testing.assert_array_equal(got.attributes[name], whole.attributes[name])
    np.testing.assert_array_equal(got.material_index, whole.material_index)
    # the plain loop: each path's points are the moves first-1 .. last
    pts, cur = 0, 0
    ends = np.append(sel.path_first[1:], sel.n_segments)
    for first, end in zip(sel.path_first, ends):
        moves = [int(sel.idx[first]) - 1] + [int(i) for i in sel.idx[first:end]]
        assert moves == list(range(moves[0], moves[0] + len(moves)))
        centre = m["position"][moves].astype(np.float64)
        np.testing.assert_allclose(got.position[pts:pts + len(moves), :2], centre[:, :2] * 0.5, atol=1e-5)
        assert got.sizes[cur] == len(moves)
        pts, cur = pts + len(moves), cur + 1
    assert pts == got.n_points == sel.n_points


# ----------------------------------------------------------------------------- prisms

def build_prisms(m, sel, sides=6, scale=1.0, part=None, step=None, a=0, b=None):
    b = sel.n_segments if b is None else b
    keys = bake.roles_present(m, sel, T)
    lut = bake.material_lut(keys)
    out = bake.alloc_prisms(sel, a, b, sides)
    step = step or max(b - a, 1)
    for s in range(a, b, step):
        bake.fill_prisms(out, m, sel, s, min(s + step, b), a, T, scale, lut)
    bake.fill_caps(out)
    return out


def faces_of(out):
    ls = np.append(out.loop_start, len(out.corner_vert))
    return [(out.corner_vert[ls[i]:ls[i + 1]], out.corner_edge[ls[i]:ls[i + 1]]) for i in range(len(out.loop_start))]


def volume(out):
    """Signed volume by fan triangulation of every face (positive for outward-facing faces of a closed mesh)."""
    co = out.co.astype(np.float64)
    total = 0.0
    for verts, _ in faces_of(out):
        p = co[verts]
        for i in range(1, len(p) - 1):
            total += np.dot(p[0], np.cross(p[i], p[i + 1])) / 6.0
    return total


def one_segment(w=0.5, h=0.2, length=10.0, z=1.0):
    m = table([row(TRV, 0, 0, z), row(EXT, length, 0, z, w=w, h=h)])
    return m, bake.select(m, bake.Filter(0, 1), T)


@pytest.mark.parametrize("sides", [3, 4, 6, 8])
def test_a_lone_segment_is_a_closed_outward_facing_prism_of_the_right_volume(sides):
    m, sel = one_segment()
    out = build_prisms(m, sel, sides)
    assert (out.n_vertices, out.n_faces, out.n_caps) == (2 * sides, sides + 2, 2)
    rs, rn = bake.ring(sides)
    assert volume(out) > 0
    # an exact reference: the section's area times the length (no turn, so no lengthening)
    section = 0.5 * np.sum(rs * np.roll(rn, -1) - np.roll(rs, -1) * rn) * (0.5 / 2) * (0.2 / 2)
    assert volume(out) == pytest.approx(section * 10.0, rel=1e-5)
    # every edge is shared by exactly two faces: the prism is watertight
    uses = np.bincount(out.corner_edge, minlength=len(out.edge_verts))
    assert uses.tolist() == [2] * (3 * sides)


def test_the_hexagon_is_three_quarters_of_the_box_and_sits_centred_below_the_nozzle_height():
    m, sel = one_segment(w=0.5, h=0.2, length=10.0, z=1.0)
    out = build_prisms(m, sel, 6)
    assert volume(out) == pytest.approx(0.75 * 0.5 * 0.2 * 10.0, rel=1e-5)
    lo, hi = out.co.min(axis=0), out.co.max(axis=0)
    np.testing.assert_allclose(lo, [0.0, -0.25, 0.8], atol=1e-6)               # z - h .. z
    np.testing.assert_allclose(hi, [10.0, 0.25, 1.0], atol=1e-6)
    box = build_prisms(m, sel, 4)
    assert volume(box) == pytest.approx(0.5 * 0.2 * 10.0, rel=1e-5)


def test_loops_and_edges_agree_in_every_face():
    m = random_table(300, seed=9)
    sel = bake.select(m, bake.Filter(0, 299, travel=True), T)
    out = build_prisms(m, sel, 6)
    ev = out.edge_verts
    for verts, edges in faces_of(out):
        nxt = np.roll(verts, -1)
        for v0, v1, e in zip(verts, nxt, edges):
            assert {int(v0), int(v1)} == {int(ev[e, 0]), int(ev[e, 1])}
    assert out.corner_vert.max() < out.n_vertices and out.corner_edge.max() < len(ev)
    assert len(out.corner_vert) == 4 * out.n * 6 + 6 * out.n_caps
    assert out.n_caps == 2 * sel.n_paths and out.n_faces == 6 * out.n + out.n_caps


def test_a_right_angle_corner_is_lengthened_by_half_a_width_and_a_straight_joint_is_not():
    m = table([row(TRV, 0, 0), row(EXT, 10, 0, w=0.4), row(EXT, 10, 10, w=0.4), row(EXT, 10, 20, w=0.4)])
    sel = bake.select(m, bake.Filter(0, 3), T)
    out = build_prisms(m, sel, 4)
    xs = [out.co[i * 8:(i + 1) * 8, 0] for i in range(3)]
    ys = [out.co[i * 8:(i + 1) * 8, 1] for i in range(3)]
    assert xs[0].max() == pytest.approx(10.2, abs=1e-5)                       # the first segment runs 0.2 past the corner
    assert ys[1].min() == pytest.approx(-0.2, abs=1e-5) and ys[1].max() == pytest.approx(10.0, abs=1e-5)
    assert ys[2].min() == pytest.approx(10.0, abs=1e-5) and ys[2].max() == pytest.approx(20.0, abs=1e-5)   # a straight joint
    assert xs[0].min() == pytest.approx(0.0, abs=1e-5)                          # the path's own start has no extension


def test_a_sharp_reversal_is_capped_at_half_a_width_and_stays_finite():
    m = table([row(TRV, 0, 0), row(EXT, 10, 0, w=0.4), row(EXT, 0.0, 0.0, w=0.4), row(EXT, 0.0, 0.0, w=0.4)])   # a U-turn, then a point
    sel = bake.select(m, bake.Filter(0, 3), T)
    out = build_prisms(m, sel, 6)
    assert np.isfinite(out.co).all() and np.abs(out.co).max() < 11.0


def test_a_vertical_move_and_a_zero_length_move_still_make_a_prism():
    m = table([row(TRV, 0, 0, 1.0), row(EXT, 0, 0, 2.0), row(EXT, 0, 0, 2.0)])
    sel = bake.select(m, bake.Filter(0, 2), T)
    out = build_prisms(m, sel, 6)
    assert np.isfinite(out.co).all()
    assert out.co[:, 2].max() > out.co[:, 2].min()


@pytest.mark.parametrize("step", [1, 3, 16, 10_000])
def test_a_prism_part_is_the_same_whatever_the_step_size(step):
    m = random_table(500, seed=2)
    sel = bake.select(m, bake.Filter(0, 499, travel=True), T)
    whole, got = build_prisms(m, sel, 6), build_prisms(m, sel, 6, step=step)
    for name in ("co", "corner_vert", "corner_edge", "edge_verts", "loop_start", "material_index"):
        np.testing.assert_array_equal(getattr(got, name), getattr(whole, name), err_msg=name)
    for name in bake.ATTRIBUTES:
        np.testing.assert_array_equal(got.seg[name], whole.seg[name])


def test_parts_cut_through_paths_still_cover_every_segment_and_cap_each_path_once():
    m = random_table(700, seed=5)
    sel = bake.select(m, bake.Filter(0, 699), T)
    whole = build_prisms(m, sel, 6)
    parts = [build_prisms(m, sel, 6, a=a, b=b) for a, b in bake.part_ranges(sel.n_segments, 37)]
    assert sum(p.n for p in parts) == sel.n_segments
    assert sum(len(p.cap_start) for p in parts) == sel.n_paths == len(whole.cap_start)
    assert sum(len(p.cap_end) for p in parts) == sel.n_paths == len(whole.cap_end)
    np.testing.assert_array_equal(np.concatenate([p.co for p in parts]), whole.co)        # same geometry, indices part-local
    for p in parts:
        assert p.corner_vert.max() < p.n_vertices and p.corner_edge.max() < len(p.edge_verts)


def test_face_attributes_repeat_the_segments_values_on_every_quad_and_cap():
    rows = [row(TRV, 0, 0), row(EXT, 5, 0, w=0.4, role=PERI), row(EXT, 9, 0, w=0.5, role=PERI)]
    m = table(rows)
    sel = bake.select(m, bake.Filter(0, 2), T)
    out = build_prisms(m, sel, 6)
    attrs = bake.face_attributes(out)
    assert set(attrs) == {*bake.ATTRIBUTES, "material_index"}
    assert all(len(v) == out.n_faces for v in attrs.values())
    assert attrs["width"][:6].tolist() == pytest.approx([0.4] * 6) and attrs["width"][6:12].tolist() == pytest.approx([0.5] * 6)
    assert attrs["width"][12:].tolist() == pytest.approx([0.4, 0.5])                    # start cap of seg 0, end cap of seg 1
    assert attrs["feature_type"].dtype == np.int32 and attrs["flow"].dtype == np.float32


def test_prism_scale_and_a_huge_selection_keep_indices_in_int32():
    m, sel = one_segment()
    out = build_prisms(m, sel, 6, scale=0.001)
    assert out.co.max() == pytest.approx(0.01) and out.corner_vert.dtype == np.int32 and out.co.dtype == np.float32


def test_ring_rejects_odd_sizes():
    with pytest.raises(ValueError):
        bake.ring(2)
    with pytest.raises(ValueError):
        bake.ring(17)
