# SPDX-License-Identifier: GPL-3.0-or-later
"""core.paint (03 sections 5.1 and 5.2)."""
import numpy as np
import pytest

from slicewright.core import paint


def test_attribute_names_and_values():
    assert paint.ATTRIBUTES == ("slicewright_support", "slicewright_seam", "slicewright_filament")
    assert (paint.NONE, paint.ENFORCE, paint.BLOCK, paint.MAX_FILAMENT) == (0, 1, 2, 16)


def test_overhang_mask_uses_the_angle_from_straight_down():
    down = [0, 0, -1.0]
    tilted = [np.sin(np.radians(20)), 0, -np.cos(np.radians(20))]      # 20 degrees off straight down
    up, side = [0, 0, 1.0], [1.0, 0, 0]
    n = np.array([down, tilted, up, side])
    assert paint.overhang_mask(n, 30).tolist() == [True, True, False, False]
    assert paint.overhang_mask(n, 10).tolist() == [True, False, False, False]
    assert paint.overhang_mask(n, 89.9).tolist() == [True, True, False, False]


def test_world_normals_rotate_with_the_object():
    flip_x = np.diag([1.0, -1, -1, 1])                                 # 180 degrees about X
    out = paint.world_normals(np.array([[0, 0, 1.0]]), flip_x)
    assert out.tolist() == [[0.0, 0.0, -1.0]]


def test_world_normals_are_correct_under_non_uniform_scale():
    # A 45 degree slope stretched 2x in x: the normal must tilt toward z, not follow the surface vector.
    m = np.diag([2.0, 1, 1, 1])
    n = paint.world_normals(np.array([[1.0, 0, 1.0]]) / np.sqrt(2), m)[0]
    assert n.tolist() == pytest.approx([1 / np.sqrt(5), 0, 2 / np.sqrt(5)])
    assert np.linalg.norm(n) == pytest.approx(1.0)


def test_face_colors_priority_and_palette():
    support = np.array([0, 1, 2, 1, 0])
    seam = np.array([0, 0, 0, 2, 1])
    filament = np.array([0, 0, 3, 0, 17])
    c = paint.face_colors(support, seam, filament)
    assert c.shape == (5, 4) and c.dtype == np.float32
    assert c[0, 3] == 0                                              # unpainted: alpha 0
    assert c[1].tolist() == pytest.approx(paint.SUPPORT_ENFORCE_RGBA)
    assert c[2].tolist() == pytest.approx(paint.SUPPORT_BLOCK_RGBA)   # support beats filament
    assert c[3].tolist() == pytest.approx(paint.SUPPORT_ENFORCE_RGBA)  # support beats seam
    assert c[4].tolist() == pytest.approx(paint.SEAM_ENFORCE_RGBA)     # seam beats filament
    only_fil = paint.face_colors(filament=np.array([1, 2, 17]))
    assert only_fil[0, :3].tolist() == pytest.approx(paint.DEFAULT_SLOT_COLORS[0])
    assert only_fil[2, :3].tolist() == pytest.approx(paint.DEFAULT_SLOT_COLORS[0])   # 17 wraps to slot 1


def test_face_colors_with_nothing_given_is_empty():
    assert paint.face_colors().shape == (0, 4)


def test_seam_values_use_their_own_colours():
    c = paint.face_colors(seam=np.array([1, 2]))
    assert c[0].tolist() == pytest.approx(paint.SEAM_ENFORCE_RGBA)
    assert c[1].tolist() == pytest.approx(paint.SEAM_BLOCK_RGBA)


def test_triangle_overlay_keeps_only_painted_triangles():
    co = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], np.float32)
    tri = np.array([[0, 1, 2], [0, 2, 3]], np.int32)
    poly = np.array([0, 1], np.int32)
    rgba = np.array([[0, 0, 0, 0], [1, 0, 0, 0.5]], np.float32)       # only face 1 painted
    pos, col = paint.triangle_overlay(co, tri, poly, rgba)
    assert pos.shape == (3, 3) and col.shape == (3, 4)
    assert pos.tolist() == [[0, 0, 0], [1, 1, 0], [0, 1, 0]]
    assert (col == [1, 0, 0, 0.5]).all()
    pos, col = paint.triangle_overlay(co, tri, poly, np.zeros((2, 4), np.float32))
    assert len(pos) == 0 and len(col) == 0
