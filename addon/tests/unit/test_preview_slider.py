# SPDX-License-Identifier: GPL-3.0-or-later
"""The layer range slider and move slider: geometry, hit testing, value <-> pixel, clamping, steps, the thumbs' rules."""
from __future__ import annotations

import math

import pytest

from slicewright.core import preview_slider as ps
from slicewright.core.preview_nav import Scrub

N = 101                                   # layers 0..100
G = ps.make_geometry(1200, 800, 1.0)


def test_geometry_places_the_track_at_the_right_and_the_move_slider_along_the_bottom():
    assert G.show_layers and G.show_move
    assert G.track_x == 1200 - ps.MARGIN_RIGHT
    assert G.track_y0 < G.track_y1 <= 800 - ps.RESERVE_TOP
    assert G.move_y < G.track_y0 - G.thumb_r                       # the move row is below the layer track
    assert 0 < G.move_x0 < G.move_x1 < G.track_x


def test_geometry_scales_with_the_ui_scale_and_respects_insets():
    g2 = ps.make_geometry(1200, 800, 2.0)
    assert g2.thumb_r == 2 * G.thumb_r and g2.move_y == pytest.approx(2 * G.move_y)
    gi = ps.make_geometry(1200, 800, 1.0, ps.Insets(left=50, right=280, top=40, bottom=30))
    assert gi.track_x == 1200 - 280 - ps.MARGIN_RIGHT
    assert gi.track_y0 == 30 + ps.RESERVE_BOTTOM_LAYER and gi.track_y1 == 800 - 40 - ps.RESERVE_TOP
    assert gi.move_x0 >= 50


def test_a_tiny_viewport_draws_and_captures_nothing():
    g = ps.make_geometry(300, 200, 1.0)
    assert not g.show_layers
    assert ps.hit_test(g, g.track_x, 100, Scrub(0, 5), 10, 5) is None


def test_insets_from_overlapping_regions():
    view = (0, 0, 1000, 700)
    sidebar = (780, 0, 220, 700)                      # overlapping sidebar on the right
    toolbar = (0, 100, 40, 500)
    header = (0, 660, 1000, 40)
    hidden = (999, 0, 1, 1)
    assert ps.insets_from_rects(view, [sidebar, toolbar, header, hidden]) == ps.Insets(40, 220, 40, 0)
    footer = (0, 0, 1000, 30)
    assert ps.insets_from_rects(view, [footer]).bottom == 30
    # A region beside the viewport (no overlap) takes nothing.
    assert ps.insets_from_rects((0, 0, 780, 700), [(780, 0, 220, 700)]) == ps.Insets()


def test_layer_pixel_round_trip_and_clamping():
    for i in (0, 1, 37, 99, 100):
        assert ps.y_to_layer(G, ps.layer_to_y(G, i, N), N) == i
    assert ps.y_to_layer(G, G.track_y0 - 500, N) == 0
    assert ps.y_to_layer(G, G.track_y1 + 500, N) == N - 1
    assert ps.layer_to_y(G, -5, N) == G.track_y0 and ps.layer_to_y(G, 500, N) == G.track_y1
    assert ps.y_to_layer(G, 123, 1) == 0 and ps.layer_to_y(G, 0, 1) == G.track_y1       # one layer: the top
    assert ps.y_to_layer(G, 123, 0) == 0


def test_the_pixel_nearest_a_layer_maps_back_between_neighbours():
    step = (G.track_y1 - G.track_y0) / (N - 1)
    y = ps.layer_to_y(G, 40, N)
    assert ps.y_to_layer(G, y + step * 0.4, N) == 40 and ps.y_to_layer(G, y + step * 0.6, N) == 41


def test_move_pixel_round_trip_whole_layer_is_the_right_end():
    last = 499
    assert ps.move_to_x(G, None, last) == G.move_x1 and ps.move_to_x(G, last, last) == G.move_x1
    assert ps.move_to_x(G, 0, last) == G.move_x0
    for p in (0, 1, 250, 498):
        assert ps.x_to_move(G, ps.move_to_x(G, p, last), last) == p
    assert ps.x_to_move(G, G.move_x1 + 50, last) is None and ps.x_to_move(G, G.move_x0 - 50, last) == 0
    assert ps.x_to_move(G, 500, 0) is None and ps.move_to_x(G, 3, 0) == G.move_x1


def test_hit_test_thumbs_bar_track_and_nothing():
    s = Scrub(20, 70)
    ylo, yhi = ps.layer_to_y(G, 20, N), ps.layer_to_y(G, 70, N)
    x = G.track_x
    assert ps.hit_test(G, x, ylo, s, N, 9) == ps.LO
    assert ps.hit_test(G, x + 3, yhi - 3, s, N, 9) == ps.HI
    assert ps.hit_test(G, x, (ylo + yhi) / 2, s, N, 9) == ps.BAR
    assert ps.hit_test(G, x, ylo - 40, s, N, 9) == ps.TRACK
    assert ps.hit_test(G, x, yhi + 40, s, N, 9) == ps.TRACK
    assert ps.hit_test(G, x - 60, (ylo + yhi) / 2, s, N, 9) is None         # beside the slider: the viewport's own
    assert ps.hit_test(G, 100, 400, s, N, 9) is None


def test_hit_test_move_slider():
    s = Scrub(0, 10, 4)
    mx = ps.move_to_x(G, 4, 9)
    assert ps.hit_test(G, mx, G.move_y, s, N, 9) == ps.MOVE_THUMB
    assert ps.hit_test(G, G.move_x0 + 2, G.move_y + 5, s, N, 9) == ps.MOVE_TRACK
    assert ps.hit_test(G, (G.move_x0 + G.move_x1) / 2, G.move_y + 60, s, N, 9) is None
    assert ps.hit_test(G, G.move_x1 + 100, G.move_y, s, N, 9) is None


def test_overlapping_thumbs_can_both_be_picked_by_the_side_of_the_press():
    s = Scrub(30, 30)
    y = ps.layer_to_y(G, 30, N)
    assert ps.hit_test(G, G.track_x, y + 2, s, N, 9) == ps.HI
    assert ps.hit_test(G, G.track_x, y - 2, s, N, 9) == ps.LO
    assert ps.hit_test(G, G.track_x, y, s, N, 9) == ps.HI


def test_thumbs_stop_at_each_other_by_default():
    s = Scrub(20, 70, 3)
    assert ps.set_thumb(s, ps.HI, 10, N) == Scrub(20, 20, None)
    assert ps.set_thumb(s, ps.LO, 90, N) == Scrub(70, 70, 3)               # hi unchanged: the in-layer position stays
    assert ps.set_thumb(s, ps.HI, 85, N) == Scrub(20, 85, None)            # a new top layer drops the position
    assert ps.set_thumb(s, ps.LO, -9, N).lo == 0 and ps.set_thumb(s, ps.HI, 9999, N).hi == N - 1


def test_follow_mode_pushes_the_other_thumb_along():
    s = Scrub(20, 70)
    assert ps.set_thumb(s, ps.LO, 90, N, follow=True) == Scrub(90, 90, None)
    assert ps.set_thumb(s, ps.HI, 5, N, follow=True) == Scrub(5, 5, None)
    assert ps.set_thumb(s, ps.HI, 50, N, follow=True) == Scrub(20, 50, None)       # no crossing: nothing is pushed


def test_set_thumb_always_keeps_lo_le_hi_and_in_range():
    for lo in (0, 3, 99):
        for hi in (0, 50, 100, 500):
            for which in (ps.LO, ps.HI):
                for v in (-4, 0, 7, 100, 700):
                    for follow in (False, True):
                        r = ps.set_thumb(Scrub(lo, hi), which, v, N, follow)
                        assert 0 <= r.lo <= r.hi <= N - 1
    assert ps.set_thumb(Scrub(0, 0), ps.HI, 5, 0) == Scrub(0, 0, None)


def test_shift_range_keeps_the_span_and_stops_at_the_ends():
    s = Scrub(20, 30, 2)
    assert ps.shift_range(s, 5, N) == Scrub(25, 35, None)      # a new top layer drops the position
    assert ps.shift_range(s, 0, N) == s
    assert ps.shift_range(s, -50, N) == Scrub(0, 10, None)
    assert ps.shift_range(s, 500, N) == Scrub(90, 100, None)
    assert ps.shift_range(Scrub(0, 200), 5, N) == Scrub(0, 100, None)                  # a span wider than the table


def test_steps_wheel_and_shift():
    assert ps.step_amount(False) == 1 and ps.step_amount(True) == 10
    assert ps.step_amount(False, "move") == 1 and ps.step_amount(True, "move") == 100
    s = Scrub(20, 70)
    assert ps.step_layers(s, ps.HI, 1, N).hi == 71 and ps.step_layers(s, ps.HI, -10, N).hi == 60
    assert ps.step_layers(s, ps.LO, 10, N).lo == 30 and ps.step_layers(s, ps.LO, -99, N).lo == 0
    assert ps.step_layers(Scrub(5, 5), ps.HI, -1, N) == Scrub(5, 5)                 # stops at the bottom thumb
    assert ps.step_layers(Scrub(5, 5), ps.HI, -1, N, follow=True) == Scrub(4, 4)    # pushes it down
    assert ps.step_layers(Scrub(100, 100), ps.HI, 1, N).hi == 100


def test_wheel_steps_the_thumb_nearest_the_pointer():
    s = Scrub(20, 70)
    assert ps.nearest_thumb(G, ps.layer_to_y(G, 25, N), s, N) == ps.LO
    assert ps.nearest_thumb(G, ps.layer_to_y(G, 60, N), s, N) == ps.HI
    assert ps.nearest_thumb(G, 0, Scrub(10, 10), N) == ps.HI                         # a tie picks the top thumb


def test_step_in_layer_matches_preview_nav():
    assert ps.step_in_layer(Scrub(0, 3), -1, 9) == Scrub(0, 3, 8)       # from the whole layer: the first step starts at its last move
    assert ps.step_in_layer(Scrub(0, 3, 8), 1, 9) == Scrub(0, 3, None)
    assert ps.step_in_layer(Scrub(0, 3, 2), -50, 9) == Scrub(0, 3, 0)
    assert ps.step_in_layer(Scrub(0, 3, 2), 1, 0) == Scrub(0, 3, None)


def test_pressing_the_track_jumps_the_nearer_thumb_and_keeps_dragging_it():
    s = Scrub(20, 70)
    y = lambda i: ps.layer_to_y(G, i, N)
    d, v = ps.begin_drag(G, ps.TRACK, G.track_x, y(90), s, N, 9)
    assert d.kind == ps.HI and v == Scrub(20, 90, None)
    d, v = ps.begin_drag(G, ps.TRACK, G.track_x, y(5), s, N, 9)
    assert d.kind == ps.LO and v == Scrub(5, 70)
    # Pressing the bare track between the thumbs (not possible on the bar) goes through the BAR path instead.
    d, v = ps.begin_drag(G, ps.BAR, G.track_x, y(45), s, N, 9)
    assert d.kind == ps.BAR and d.grab == 25 and v == s
    assert ps.begin_drag(G, None, 0, 0, s, N, 9) == (None, s)


def test_dragging_a_thumb_follows_the_pointer_and_clamps_at_the_other_thumb():
    s = Scrub(20, 70)
    y = lambda i: ps.layer_to_y(G, i, N)
    d, _ = ps.begin_drag(G, ps.HI, G.track_x, y(70), s, N, 9)
    assert ps.drag_to(G, d, 0, y(80), s, N, 9) == Scrub(20, 80)
    assert ps.drag_to(G, d, 0, y(10), s, N, 9) == Scrub(20, 20)                     # stops at the bottom thumb
    assert ps.drag_to(G, d, 0, y(10), s, N, 9, follow=True) == Scrub(10, 10)
    assert ps.drag_to(G, d, 0, 10_000, s, N, 9).hi == N - 1


def test_dragging_the_bar_moves_the_range_keeping_its_span():
    s = Scrub(20, 50)
    y = lambda i: ps.layer_to_y(G, i, N)
    d, _ = ps.begin_drag(G, ps.BAR, G.track_x, y(35), s, N, 9)
    assert ps.drag_to(G, d, 0, y(45), s, N, 9) == Scrub(30, 60)
    assert ps.drag_to(G, d, 0, y(0), s, N, 9) == Scrub(0, 30)
    assert ps.drag_to(G, d, 0, y(100), s, N, 9) == Scrub(70, 100)
    assert ps.drag_to(G, d, 0, y(35), s, N, 9) == s                                  # back where it started: unchanged


def test_dragging_the_move_thumb_sets_the_position_and_the_right_end_is_the_whole_layer():
    s = Scrub(0, 10)
    d, v = ps.begin_drag(G, ps.MOVE_TRACK, (G.move_x0 + G.move_x1) / 2, G.move_y, s, N, 99)
    assert d.kind == ps.MOVE_THUMB and 45 <= v.pos <= 55
    assert ps.drag_to(G, d, G.move_x0 - 30, G.move_y, v, N, 99).pos == 0
    assert ps.drag_to(G, d, G.move_x1 + 30, G.move_y, v, N, 99).pos is None
    assert ps.drag_to(G, d, G.move_x0, G.move_y, v, N, 99) == Scrub(0, 10, 0)        # the layer range is untouched


def test_cancelled_drag_restores_the_start_value():
    s = Scrub(20, 70, 4)
    d, _ = ps.begin_drag(G, ps.HI, G.track_x, ps.layer_to_y(G, 70, N), s, N, 9)
    assert d.start == s


def test_text():
    assert ps.thumb_text(0, 120, 0.2) == ("1", "0.20 mm") and ps.thumb_text(59, 120, 12.0) == ("60", "12.00 mm")
    assert ps.total_text(1) == "1 layer" and ps.total_text(120) == "120 layers"
    assert ps.move_text(None, 9) == "10 / 10" and ps.move_text(3, 9) == "4 / 10"


def test_shapes():
    pts = ps.circle_points(10, 20, 5, 12)
    assert len(pts) == 12 and all(math.isclose(math.hypot(x - 10, y - 20), 5) for x, y in pts)
    rr = ps.rounded_rect_points(0, 0, 40, 10, 100)           # the radius is capped to half the short side
    xs, ys = [p[0] for p in rr], [p[1] for p in rr]
    assert min(xs) == pytest.approx(0) and max(xs) == pytest.approx(40) and min(ys) == pytest.approx(0)
    assert max(ys) == pytest.approx(10)
