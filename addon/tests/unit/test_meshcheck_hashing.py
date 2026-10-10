# SPDX-License-Identifier: GPL-3.0-or-later
"""core.meshcheck and core.hashing (03 sections 4.3 and 4.4)."""
import numpy as np

from slicewright.core import hashing, meshcheck

CUBE_V = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)
# outward-wound cube over those vertices (index = x*4 + y*2 + z)
CUBE_T = np.array([(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
                   (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)], np.int32)


def test_closed_cube_is_manifold_and_has_unit_volume():
    assert len(meshcheck.non_manifold_edges(CUBE_T)) == 0
    assert meshcheck.signed_volume(CUBE_V, CUBE_T) == np.float64(1.0) or abs(
        meshcheck.signed_volume(CUBE_V, CUBE_T) - 1.0) < 1e-9


def test_reversed_winding_gives_negative_volume():
    assert meshcheck.signed_volume(CUBE_V, CUBE_T[:, ::-1]) < 0


def test_removing_a_triangle_opens_three_edges():
    edges = meshcheck.non_manifold_edges(CUBE_T[1:])
    assert len(edges) == 3 and (edges[:, 0] < edges[:, 1]).all()


def test_an_edge_shared_by_three_triangles_is_non_manifold():
    t = np.vstack([CUBE_T, [[0, 1, 4]]])
    assert len(meshcheck.non_manifold_edges(t)) > 0


def test_no_triangles_is_degenerate_and_a_cube_is_not():
    assert meshcheck.is_degenerate(CUBE_V, np.zeros((0, 3), np.int32))
    assert not meshcheck.is_degenerate(CUBE_V, CUBE_T)


def test_a_flat_sheet_has_zero_volume():
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], np.float32)
    assert meshcheck.is_degenerate(v, np.array([[0, 1, 2], [0, 2, 3]], np.int32))


def test_object_key_is_stable_and_sensitive_to_every_input():
    base = dict(vertices=CUBE_V, triangles=CUBE_T, face_support=np.zeros(12, np.uint8))
    k = hashing.object_key(**base)
    assert k == hashing.object_key(**base)
    assert k != hashing.object_key(**{**base, "vertices": CUBE_V + 0.001})
    assert k != hashing.object_key(**{**base, "triangles": CUBE_T[::-1].copy()})
    assert k != hashing.object_key(**{**base, "face_support": np.ones(12, np.uint8)})
    assert k != hashing.object_key(**{**base, "face_seam": np.zeros(12, np.uint8)})
    assert k != hashing.object_key(**base, overrides={"wall_loops": "3"})
    assert k != hashing.object_key(**base, extruder=2)
    assert hashing.object_key(**base, overrides={"a": 1, "b": 2}) == hashing.object_key(
        **base, overrides={"b": 2, "a": 1})


def test_scene_key_ignores_object_order_but_not_version_or_config():
    a = hashing.scene_key("1.0", {"x": "1"}, ["k1", "k2"])
    assert a == hashing.scene_key("1.0", {"x": "1"}, ["k2", "k1"])
    assert a != hashing.scene_key("1.1", {"x": "1"}, ["k1", "k2"])
    assert a != hashing.scene_key("1.0", {"x": "2"}, ["k1", "k2"])
    assert a != hashing.scene_key("1.0", {"x": "1"}, ["k1"])
