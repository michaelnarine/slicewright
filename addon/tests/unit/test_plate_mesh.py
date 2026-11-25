# SPDX-License-Identifier: GPL-3.0-or-later
"""The build-plate slab: ear-clipping triangulation and slab geometry (core, no Blender)."""
import numpy as np
import pytest

from slicewright.core import plate_mesh as pm


def tri_area(poly, tris):
    p = np.asarray(poly, float)
    a, b, c = p[tris[:, 0]], p[tris[:, 1]], p[tris[:, 2]]
    return 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))


SQUARE = [(0, 0), (256, 0), (256, 256), (0, 256)]
L_SHAPE = [(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)]
COMB = [(0, 0), (30, 0), (30, 20), (25, 20), (25, 5), (20, 5), (20, 20), (15, 20), (15, 5), (10, 5), (10, 20),
        (5, 20), (5, 5), (0, 5)] 
CIRCLE = [(100 * np.cos(t), 100 * np.sin(t)) for t in np.linspace(0, 2 * np.pi, 64, endpoint=False)]


@pytest.mark.parametrize("poly", [SQUARE, L_SHAPE, COMB, CIRCLE, [(0, 0), (10, 0), (0, 10)]])
@pytest.mark.parametrize("reverse", [False, True])
def test_triangles_are_counter_clockwise_and_cover_the_polygon(poly, reverse):
    poly = poly[::-1] if reverse else poly
    tris = pm.triangulate(poly)
    areas = tri_area(poly, tris)
    assert len(tris) == len(poly) - 2
    assert (areas > 0).all()
    assert areas.sum() == pytest.approx(abs(pm.signed_area(np.array(poly))))


def test_a_closing_point_duplicates_and_collinear_points_are_dropped():
    poly = [(0, 0), (5, 0), (10, 0), (10, 10), (10, 10), (0, 10), (0, 0)]
    tris = pm.triangulate(poly)
    assert tri_area(poly, tris).sum() == pytest.approx(100.0)
    assert (tri_area(poly, tris) > 0).all()


def test_no_triangle_has_a_vertex_of_the_polygon_strictly_inside():
    p = np.array(COMB, float)

    def side(a, b, q):
        return (b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0])

    for t in pm.triangulate(COMB):
        a, b, c = p[t]
        for j, q in enumerate(p):
            if j not in t:
                assert not (side(a, b, q) > 1e-9 and side(b, c, q) > 1e-9 and side(c, a, q) > 1e-9)


@pytest.mark.parametrize("poly", [[], [(0, 0)], [(0, 0), (1, 1)], [(0, 0), (1, 1), (2, 2)], [(0, 0), (0, 0), (0, 0)]])
def test_degenerate_polygons_give_no_triangles(poly):
    assert pm.triangulate(poly).shape == (0, 3)
    assert all(len(v) == 0 for v in pm.slab(poly).values())


def test_slab_faces_heights_and_orientation():
    s = pm.slab(SQUARE, thickness=4.0, eps=0.15)
    assert s["top"].shape == (6, 3) and s["bottom"].shape == (6, 3) and s["sides"].shape == (24, 3)
    assert s["top"].dtype == np.float32
    assert np.allclose(s["top"][:, 2], -0.15) and np.allclose(s["bottom"][:, 2], -4.15)
    assert s["sides"][:, 2].max() == pytest.approx(-0.15) and s["sides"][:, 2].min() == pytest.approx(-4.15)
    assert max(float(s["top"][:, 2].max()), float(s["sides"][:, 2].max())) < 0       # nothing reaches Z = 0

    def normals(v):
        t = v.reshape(-1, 3, 3).astype(float)
        return np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    assert (normals(s["top"])[:, 2] > 0).all() and (normals(s["bottom"])[:, 2] < 0).all()
    centre = np.array([128.0, 128.0, -2.0])
    for tri, n in zip(s["sides"].reshape(-1, 3, 3), normals(s["sides"])):
        assert np.dot(n, tri.mean(axis=0) - centre) > 0                                # outward


def test_slab_follows_a_clockwise_non_convex_polygon():
    s = pm.slab(L_SHAPE[::-1])
    t = s["top"].reshape(-1, 3, 3)
    area = sum(0.5 * abs((x[1, 0]-x[0, 0])*(x[2, 1]-x[0, 1]) - (x[1, 1]-x[0, 1])*(x[2, 0]-x[0, 0])) for x in t)
    assert area == pytest.approx(300.0)
    assert (s["sides"].reshape(-1, 3, 3)[:, :, :2].min() >= 0)


def test_build_plate_is_in_the_world_frame_centred_on_the_bed():
    from slicewright.core.bed import build_plate, parse_bed
    bed = parse_bed("0x0,256x0,256x256,0x256", "", 250.0)
    plate = build_plate(bed)
    assert plate["top"].dtype == np.float32
    assert plate["top"][:, :2].min() == pytest.approx(-128.0) and plate["top"][:, :2].max() == pytest.approx(128.0)
    raw = build_plate(bed, offset=(0, 0))                       # the raw bed frame for comparison
    assert raw["top"][:, :2].min() == pytest.approx(0.0) and raw["top"][:, :2].max() == pytest.approx(256.0)
    assert np.allclose(plate["top"][:, 2], raw["top"][:, 2])    # only X and Y move


def test_build_plate_follows_a_non_convex_printable_area():
    from slicewright.core.bed import build_plate, parse_bed
    bed = parse_bed("0x0,20x0,20x10,10x10,10x20,0x20", "", 100.0)
    top = build_plate(bed, offset=(0, 0))["top"].reshape(-1, 3, 3)
    area = sum(0.5 * abs((t[1, 0]-t[0, 0])*(t[2, 1]-t[0, 1]) - (t[1, 1]-t[0, 1])*(t[2, 0]-t[0, 0])) for t in top)
    assert area == pytest.approx(300.0)
