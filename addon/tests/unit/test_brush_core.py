# SPDX-License-Identifier: GPL-3.0-or-later
"""core.brush: targets, face adjacency and smart fill (03 section 5.2)."""
import numpy as np
import pytest

from slicewright.core import brush
from slicewright.core import paint


def test_brush_targets():
    assert brush.brush_target("SUPPORT_ENFORCE") == (paint.ATTR_SUPPORT, 1)
    assert brush.brush_target("SUPPORT_BLOCK") == (paint.ATTR_SUPPORT, 2)
    assert brush.brush_target("SEAM_ENFORCE") == (paint.ATTR_SEAM, 1)
    assert brush.brush_target("SEAM_BLOCK") == (paint.ATTR_SEAM, 2)
    assert brush.brush_target("FILAMENT", 5) == (paint.ATTR_FILAMENT, 5)
    assert brush.brush_target("SUPPORT_BLOCK", 9) == (paint.ATTR_SUPPORT, 2)     # the slot only matters for filament
    assert {k[0] for k in brush.KINDS} == {"SUPPORT_ENFORCE", "SUPPORT_BLOCK", "SEAM_ENFORCE", "SEAM_BLOCK", "FILAMENT"}


def strip(n):
    """A strip of n quads in a row: face i touches faces i-1 and i+1 through edge i (left) and i+1 (right)."""
    edge, face = [], []
    for f in range(n):
        edge += [f, 1000 + f, f + 1, 2000 + f]          # left edge, bottom, right edge, top
        face += [f] * 4
    return np.array(edge), np.array(face)


def test_adjacency_of_a_strip():
    e, f = strip(4)
    adj = brush.face_adjacency(e, f, 4)
    assert [sorted(a.tolist()) for a in adj] == [[1], [0, 2], [1, 3], [2]]


def test_edges_used_once_or_three_times_link_nothing():
    # faces 0, 1, 2 all share edge 7 (three users); face 3 is alone
    e = np.array([7, 1, 7, 2, 7, 3, 9, 10])
    f = np.array([0, 0, 1, 1, 2, 2, 3, 3])
    assert all(len(a) == 0 for a in brush.face_adjacency(e, f, 4))


def test_a_face_does_not_neighbour_itself_across_a_doubled_edge():
    adj = brush.face_adjacency(np.array([5, 5]), np.array([0, 0]), 1)
    assert len(adj[0]) == 0


def flat_normals(angles_deg):
    a = np.radians(angles_deg)
    return np.stack([np.sin(a), np.zeros_like(a), np.cos(a)], axis=1)


def test_grow_region_stops_at_a_crease():
    e, f = strip(6)
    adj = brush.face_adjacency(e, f, 6)
    n = flat_normals(np.array([0, 5, 10, 60, 65, 70]))       # a 50 degree crease between faces 2 and 3
    assert brush.grow_region(0, range(6), n, adj, 20) == {0, 1, 2}
    assert brush.grow_region(5, range(6), n, adj, 20) == {3, 4, 5}
    assert brush.grow_region(0, range(6), n, adj, 60) == set(range(6))


def test_grow_region_compares_neighbours_so_gentle_curves_fill():
    e, f = strip(8)
    adj = brush.face_adjacency(e, f, 8)
    n = flat_normals(np.arange(8) * 15.0)                     # 105 degrees in total, 15 per step
    assert brush.grow_region(0, range(8), n, adj, 20) == set(range(8))


def test_grow_region_respects_the_candidate_set_and_a_missing_seed():
    e, f = strip(5)
    adj = brush.face_adjacency(e, f, 5)
    n = flat_normals(np.zeros(5))
    assert brush.grow_region(2, {1, 2, 3}, n, adj, 20) == {1, 2, 3}
    assert brush.grow_region(0, {1, 2, 3}, n, adj, 20) == set()
    assert brush.grow_region(2, {2, 4}, n, adj, 20) == {2}
