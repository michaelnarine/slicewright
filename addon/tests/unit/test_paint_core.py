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
