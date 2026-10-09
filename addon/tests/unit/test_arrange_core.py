# SPDX-License-Identifier: GPL-3.0-or-later
"""core.arrange (03 section 4.6)."""
import numpy as np
import pytest

from slicewright.core import arrange, meshcheck


def test_prism_of_a_box_is_a_closed_outward_box():
    box = np.array([[x, y, z] for x in (10, 30) for y in (5, 25) for z in (0, 8)], float)
    verts, tris = arrange.hull_prism(box)
    assert verts.dtype == np.float32 and tris.dtype == np.int32 and verts.shape == (8, 3)
    assert len(meshcheck.non_manifold_edges(tris)) == 0
    assert meshcheck.signed_volume(verts, tris) == pytest.approx(20 * 20 * 8)


def test_prism_footprint_is_the_convex_hull_not_the_bounding_box():
    tri = np.array([[0, 0, 0], [40, 0, 0], [0, 40, 0], [0, 0, 10], [40, 0, 10], [0, 40, 10],
                    [5, 5, 3]], float)
    verts, tris = arrange.hull_prism(tri)
    assert meshcheck.signed_volume(verts, tris) == pytest.approx(800.0 * 10)       # right triangle, 800 mm2
    assert len(meshcheck.non_manifold_edges(tris)) == 0


def test_prism_of_many_hull_points_stays_closed_and_positive():
    t = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    pts = np.column_stack([20 * np.cos(t), 20 * np.sin(t), np.zeros(40)])
    pts = np.concatenate([pts, pts + [0, 0, 5]])
    verts, tris = arrange.hull_prism(pts)
    assert len(meshcheck.non_manifold_edges(tris)) == 0
    assert meshcheck.signed_volume(verts, tris) > 0
    assert len(tris) == 4 * 40 - 4


def test_a_flat_sheet_gets_a_sliver_and_a_line_gets_nothing():
    sheet = np.array([[0, 0, 2], [10, 0, 2], [10, 10, 2], [0, 10, 2]], float)
    verts, tris = arrange.hull_prism(sheet)
    assert verts[:, 2].max() > verts[:, 2].min()
    assert arrange.hull_prism(np.array([[0, 0, 0], [1, 1, 5], [2, 2, 0]], float)) is None
    assert arrange.hull_prism(np.zeros((0, 3))) is None


def test_placement_matrices_use_the_unit_conjugation():
    t = np.eye(4)
    t[:3, 3] = [10, 20, 0]
    m = np.eye(4)
    m[:3, 3] = [1, 2, 3]                                   # BU, 1 BU = 10 mm
    out = arrange.placement_matrices([{"index": 4, "transform": t}], {4: m}, 10.0)
    assert out[4][:3, 3].tolist() == pytest.approx([2.0, 4.0, 3.0])
