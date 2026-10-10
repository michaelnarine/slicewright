# SPDX-License-Identifier: GPL-3.0-or-later
"""Scrub arithmetic and the view to shader-mode mapping."""
from __future__ import annotations

import numpy as np
import pytest

from slicewright.core import preview_data as pd
from slicewright.core import preview_nav as nav
from slicewright.core.preview_nav import Scrub

LAYERS = {"first": np.array([0, 10, 25, 26], np.uint32), "last": np.array([9, 24, 25, 39], np.uint32)}


def test_clamp_keeps_lo_le_hi_inside_the_table():
    assert nav.clamp(Scrub(5, 99, None), LAYERS) == Scrub(3, 3, None)
    assert nav.clamp(Scrub(-4, -2, -9), LAYERS) == Scrub(0, 0, 0)
    assert nav.clamp(Scrub(0, 1, 500), LAYERS) == Scrub(0, 1, 14)         # layer 1 has 15 moves
    empty = {"first": np.zeros(0, np.uint32), "last": np.zeros(0, np.uint32)}
    assert nav.clamp(Scrub(3, 4, 5), empty) == Scrub(0, 0, None) and nav.full(empty) == Scrub(0, 0, None)


def test_step_top_layer_resets_the_in_layer_position_and_clamps():
    s = nav.step_top_layer(Scrub(0, 1, 4), LAYERS, 1)
    assert s == Scrub(0, 2, None)
    assert nav.step_top_layer(Scrub(2, 2, None), LAYERS, -5) == Scrub(0, 0, None)    # lo follows hi down
    assert nav.step_top_layer(Scrub(0, 2, None), LAYERS, 50).hi == 3


def test_step_bottom_layer_cannot_pass_the_top():
    assert nav.step_bottom_layer(Scrub(0, 2, None), LAYERS, 9) == Scrub(2, 2, None)
    assert nav.step_bottom_layer(Scrub(1, 2, None), LAYERS, -9).lo == 0


def test_step_move_starts_from_the_end_and_returns_to_the_whole_layer():
    s = Scrub(0, 1, None)                                  # layer 1: moves 0..14
    s = nav.step_move(s, LAYERS, -1)
    assert s.pos == 13
    assert nav.step_move(s, LAYERS, 1).pos is None         # reached the last move
    assert nav.step_move(Scrub(0, 1, 3), LAYERS, -10).pos == 0
    assert nav.step_move(Scrub(0, 1, 3), LAYERS, 11).pos is None
    one = Scrub(2, 2, None)                                # a one-move layer
    assert nav.step_move(one, LAYERS, -1).pos == 0 or nav.step_move(one, LAYERS, -1).pos is None


def test_top_position_is_the_global_index_of_the_nozzle():
    assert nav.top_position(Scrub(0, 1, None), LAYERS) == 24
    assert nav.top_position(Scrub(0, 1, 4), LAYERS) == 14
    assert nav.top_position(Scrub(0, 1, 400), LAYERS) == 24


def test_role_mask_from_booleans():
    assert nav.role_mask([True] * 32) == 0xFFFFFFFF
    assert nav.role_mask([False] * 32) == 0
    assert nav.role_mask([i in (0, 2, 31) for i in range(32)]) == 1 | 4 | (1 << 31)


@pytest.mark.parametrize("view,mode", [("feature", 0), ("speed", 1), ("flow", 1), ("layer_time_log", 1),
                                       ("filament", 2), ("nozzle", 2), ("color", 2)])
def test_shader_mode_for_each_view(view, mode):
    assert pd.shader_mode_for(view) == mode


def test_every_view_has_a_shader_mode_and_unknown_raises():
    assert all(pd.shader_mode_for(v) in (0, 1, 2) for v in pd.VIEW_MODES)
    with pytest.raises(ValueError):
        pd.shader_mode_for("bogus")
