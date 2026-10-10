# SPDX-License-Identifier: GPL-3.0-or-later
"""core.geometry hull/containment, core.checks and core.transform (03 sections 4.1 and 4.3)."""
import numpy as np
import pytest

from slicewright.core import checks
from slicewright.core.bed import parse_bed
from slicewright.core.geometry import convex_hull, convex_overlap, points_in_polygon
from slicewright.core.transform import (
    bed_transform_to_world, is_mirrored, oriented_triangles, to_world_mm)

BED = parse_bed("0x0,200x0,200x200,0x200", "0x0,30x0,30x30,0x30", 100)


def cube(lo, hi):
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], float)


def test_convex_hull_matches_a_brute_force_reference_on_random_clouds():
    rng = np.random.default_rng(1)
    for n in (5, 100, 5000):
        pts = rng.normal(size=(n, 2)) * 10
        hull = convex_hull(pts)
        assert len(hull) >= 3
        # every input point is inside (or on) the hull; every hull vertex is an input point
        assert points_in_polygon(pts, hull, eps=1e-9).all()
        assert {tuple(h) for h in hull} <= {tuple(p) for p in pts}
        # strictly counter-clockwise: positive signed area
        x, y = hull[:, 0], hull[:, 1]
        assert 0.5 * (x @ np.roll(y, -1) - y @ np.roll(x, -1)) > 0


def test_convex_hull_degenerate_inputs():
    assert len(convex_hull(np.array([[1.0, 1.0]] * 10))) == 1
    assert len(convex_hull(np.array([[0.0, 0], [1, 1], [2, 2]]))) == 2


def test_points_in_polygon_includes_the_boundary():
    sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    got = points_in_polygon(np.array([[5, 5], [0, 5], [10, 10], [10.5, 5], [-1, -1]]), sq)
    assert got.tolist() == [True, True, True, False, False]


def test_convex_overlap_ignores_touching_edges():
    a = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    assert not convex_overlap(a, a + [10, 0])
    assert convex_overlap(a, a + [9, 0])
    assert not convex_overlap(a, a + [20, 20])


def test_a_cube_on_the_bed_fits():
    assert checks.out_of_volume(cube((50, 50, 0), (80, 80, 20)), BED) == []


def test_each_violation_is_reported():
    assert checks.OUTSIDE_BED in checks.out_of_volume(cube((190, 50, 0), (210, 80, 20)), BED)
    assert checks.OUTSIDE_BED in checks.out_of_volume(cube((-5, 50, 0), (20, 80, 20)), BED)
    assert checks.TOO_TALL in checks.out_of_volume(cube((50, 50, 0), (80, 80, 100.5)), BED)
    assert checks.BELOW_BED in checks.out_of_volume(cube((50, 50, -0.5), (80, 80, 20)), BED)
    assert checks.IN_EXCLUDE in checks.out_of_volume(cube((20, 20, 0), (60, 60, 20)), BED)


def test_tolerances():
    assert checks.out_of_volume(cube((0, 0, -0.005), (0.0005 + 200, 50, 100.0005)), parse_bed(
        "0x0,200x0,200x200,0x200", "", 100)) == []     # within 0.01 / 1e-3 slack
    # touching the exclude area's edge is fine
    assert checks.out_of_volume(cube((30, 50, 0), (60, 80, 5)), BED) == []


def test_a_diagonal_part_inside_the_hull_of_a_rotated_bed_polygon():
    diamond = parse_bed("100x0,200x100,100x200,0x100", "", 50)
    assert checks.out_of_volume(cube((90, 90, 0), (110, 110, 5)), diamond) == []
    assert checks.OUTSIDE_BED in checks.out_of_volume(cube((10, 10, 0), (30, 30, 5)), diamond)


def test_empty_vertices_have_no_reasons():
    assert checks.out_of_volume(np.zeros((0, 3)), BED) == []


def test_throttle_runs_at_most_once_per_interval():
    t = [0.0]
    th = checks.Throttle(0.2, lambda: t[0])
    assert th.ready()
    th.mark()
    t[0] = 0.1
    assert not th.ready() and th.remaining() == pytest.approx(0.1)
    t[0] = 0.2
    assert th.ready() and th.remaining() == 0.0


def test_to_world_mm_applies_matrix_and_unit_scale():
    m = np.eye(4)
    m[:3, 3] = [1, 2, 3]
    m[0, 0] = 2
    v = to_world_mm(np.array([1.0, 1.0, 1.0]), m, 1000.0)
    assert v.dtype == np.float32 and v.tolist() == [[3000.0, 3000.0, 4000.0]]


def test_mirror_flips_winding_only_for_negative_determinant():
    tri = np.array([[0, 1, 2]], np.int32)
    flip = np.diag([-1.0, 1, 1, 1])
    assert is_mirrored(flip) and not is_mirrored(np.eye(4))
    assert oriented_triangles(tri, flip).tolist() == [[2, 1, 0]]
    assert oriented_triangles(tri, np.diag([-1.0, -1, 1, 1])).tolist() == [[0, 1, 2]]


def test_placement_to_world_is_conjugation_by_the_unit_scale():
    t = np.eye(4)
    t[:3, 3] = [10.0, 20.0, 0.0]               # mm
    m = np.eye(4)
    m[:3, 3] = [0.1, 0.0, 0.0]                  # BU
    new = bed_transform_to_world(t, m, 1000.0)
    assert new[:3, 3].tolist() == pytest.approx([0.11, 0.02, 0.0])
