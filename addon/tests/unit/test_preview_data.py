# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview packing, view scalars, marker lists and range planning (03 section 7; plan M6)."""
from __future__ import annotations

import random

import numpy as np
import pytest

from fake_engine.api import MOVE_TYPES
from fake_engine.movetable import build_layers
from slicewright.core import preview_data as pd


def make_moves(n: int, per_layer: int = 7, seed: int = 3) -> dict:
    rng = np.random.default_rng(seed)
    types = rng.choice([MOVE_TYPES["Extrude"]] * 6 + [MOVE_TYPES["Travel"], MOVE_TYPES["Retract"],
                                                      MOVE_TYPES["Seam"], MOVE_TYPES["Wipe"]], n)
    types[0] = MOVE_TYPES["Noop"]
    m = {
        "position": rng.uniform(0, 100, (n, 3)).astype(np.float32),
        "type": types.astype(np.uint8), "role": rng.integers(0, 20, n).astype(np.uint8),
        "filament": rng.integers(0, 5, n).astype(np.uint8),
        "nozzle": rng.integers(0, 2, n).astype(np.uint8), "color_id": np.zeros(n, np.uint8),
        "width": rng.uniform(0.3, 0.6, n).astype(np.float32),
        "height": np.full(n, 0.2, np.float32),
        "mm3_per_mm": rng.uniform(0.01, 0.1, n).astype(np.float32),
        "feedrate": rng.uniform(20, 200, n).astype(np.float32),
        "time": rng.uniform(0.01, 0.1, (n, 2)).astype(np.float32),
        "layer_id": (np.arange(n) // per_layer).astype(np.uint32),
        "print_z": np.zeros(n, np.float32),
    }
    m["actual_feedrate"] = m["feedrate"]
    for k in ("fan", "temperature", "pressure_advance", "acceleration", "jerk"):
        m[k] = rng.uniform(0, 1, n).astype(np.float32)
    return m


def test_chunk_bounds_cover_all_moves_once():
    b = pd.chunk_bounds(23, 10)
    assert [(c.s, c.e) for c in b] == [(0, 9), (10, 19), (20, 22)]
    assert pd.chunk_bounds(0) == [] and pd.chunk_bounds(1)[0].n_moves == 1
    assert pd.chunk_bounds(pd.CHUNK_MOVES)[0].rows == 129       # 2**20 + 1 texels at width 8192


@pytest.mark.parametrize("chunk", [4, 7, 10])
def test_texel_j_is_move_s_minus_1_plus_j_with_move_0_duplicated(chunk):
    m = make_moves(23)
    for b in pd.chunk_bounds(23, chunk):
        pos = pd.pack_positions(m, b)
        assert pos.shape == (b.rows * pd.TEX_W, 4)
        for j in range(b.n_texels):
            move = max(b.s - 1 + j, 0)
            assert np.array_equal(pos[j, :3], m["position"][move]) and pos[j, 3] == m["width"][move]
        assert not pos[b.n_texels:].any()                        # padding is zero


def test_meta_round_trips_and_every_channel_is_a_finite_float_in_1_2():
    m = make_moves(500)
    b = pd.chunk_bounds(500, 500)[0]
    bits = pd.pack_meta(m, b)
    f = bits[:b.n_texels].view(np.float32)
    assert np.isfinite(f).all() and (f >= 1.0).all() and (f < 2.0).all()
    u = pd.unpack_meta(bits[:b.n_texels])
    idx = np.maximum(np.arange(b.n_texels) - 1, 0)
    assert np.array_equal(u["role"], m["role"][idx]) and np.array_equal(u["type"], m["type"][idx])
    assert np.array_equal(u["filament"], m["filament"][idx])
    assert np.array_equal(u["nozzle"], m["nozzle"][idx])
    assert np.array_equal(u["layer_id"], m["layer_id"][idx])
    pad = pd.unpack_meta(bits[b.n_texels:])
    assert not pad["type"].any()                                 # padding decodes as a no-op
    assert pd.TYPE_SHIFT + pd.TYPE_BITS == pd.FILAMENT_SHIFT and pd.NOZZLE_SHIFT + 3 <= 20


def test_filament_255_and_max_type_survive_packing():
    m = make_moves(10)
    m["filament"][3] = 255
    m["type"][3] = 15
    u = pd.unpack_meta(pd.pack_meta(m, pd.chunk_bounds(10, 10)[0]))
    assert u["filament"][4] == 255 and u["type"][4] == 15


def test_values_hold_height_and_the_active_scalar():
    m = make_moves(30)
    b = pd.chunk_bounds(30, 10)[1]
    v = pd.pack_values(m, m["feedrate"], b)
    assert v[1, 0] == 0.2 and v[1, 1] == m["feedrate"][b.s]
    assert not pd.pack_values(m, None, b)[:, 1].any()


def test_markers_are_texel_indices_grouped_by_kind():
    m = make_moves(300)
    b = pd.chunk_bounds(300, 100)[1]
    texels, slices = pd.marker_moves(m, b, MOVE_TYPES)
    assert texels.dtype == np.float32
    for kind, (start, count) in slices.items():
        moves = texels[start:start + count].astype(int) + b.s - 1
        assert (m["type"][moves] == MOVE_TYPES[kind]).all()
        assert (np.diff(moves) > 0).all() and count == (m["type"][b.s:b.e + 1] == MOVE_TYPES[kind]).sum()
    assert sum(c for _, c in slices.values()) == len(texels)
    assert "Tool_change" not in slices
    empty, none = pd.marker_moves(m, b, {"Seam": 250})           # a type that never occurs
    assert len(empty) == 0 and none == {}


def test_marker_range_counts_markers_inside_the_move_range():
    idx = np.array([2, 5, 9, 14])
    assert pd.marker_range(idx, 5, 9) == (1, 2)
    assert pd.marker_range(idx, 6, 8) == (2, 0)
    assert pd.marker_range(idx, 0, 100) == (0, 4)


def test_layer_time_view_maps_each_layer_sum_onto_its_moves():
    m = make_moves(40, per_layer=10)
    layers = build_layers(m)
    t = pd.layer_times(m, layers)
    assert len(t) == 4 and np.isclose(t.sum(), m["time"][:, 0].sum(), rtol=1e-6)
    s = pd.view_scalar(m, layers, "layer_time")
    assert np.allclose(s[:10], t[0]) and np.allclose(s[30:], t[3])
    log = pd.view_scalar(m, layers, "layer_time_log")
    assert np.allclose(log[:10], np.log10(t[0]), rtol=1e-5)


def test_view_scalars_for_each_mode():
    m = make_moves(40)
    layers = build_layers(m)
    assert pd.view_scalar(m, layers, "feature") is None
    assert np.array_equal(pd.view_scalar(m, layers, "speed"), m["feedrate"])
    assert np.allclose(pd.view_scalar(m, layers, "flow"), m["mm3_per_mm"] * m["feedrate"])
    assert np.array_equal(pd.view_scalar(m, layers, "filament"), m["filament"].astype(np.float32))
    for view in pd.VIEW_MODES:
        s = pd.view_scalar(m, layers, view)
        assert s is None or (s.dtype == np.float32 and s.shape == (40,))
    with pytest.raises(ValueError):
        pd.view_scalar(m, layers, "nope")


def test_value_range_is_the_trimmed_percentile_and_never_degenerate():
    v = np.arange(1001, dtype=np.float32)
    lo, hi = pd.value_range(v)
    assert lo == pytest.approx(5.0) and hi == pytest.approx(995.0)
    mask = np.arange(1001) >= 500
    assert pd.value_range(v, mask)[0] > 500
    assert pd.value_range(np.full(10, 3.0, np.float32)) == (3.0, 4.0)
    assert pd.value_range(np.zeros(0, np.float32)) == (0.0, 1.0)
    big = np.arange(5_000_000, dtype=np.float32)
    lo, hi = pd.value_range(big, max_samples=1 << 16)
    assert abs(lo - 0.005 * 5e6) < 5e4 and abs(hi - 0.995 * 5e6) < 5e4


LAYERS = {"first": np.array([0, 10, 25, 26], np.uint32), "last": np.array([9, 24, 25, 39], np.uint32)}


def test_visible_range_follows_03_7_4():
    assert pd.visible_range(LAYERS, 1, 1, None, 39) == (10, 24)
    assert pd.visible_range(LAYERS, 0, 1, 4, 39) == (0, 14)      # in-layer position of the top layer
    assert pd.visible_range(LAYERS, 0, 1, 500, 39) == (0, 24)    # clamped to the layer
    assert pd.visible_range(LAYERS, 0, 3, None, 30) == (0, 30)   # capped by what is uploaded
    assert pd.visible_range(LAYERS, 3, 3, None, 20) is None      # not uploaded yet
    assert pd.visible_range(LAYERS, 2, 1, None, 39) is None
    assert pd.visible_range(LAYERS, 0, 99, None, 39) == (0, 39)  # hi clamped
    assert pd.visible_range({"first": np.zeros(0), "last": np.zeros(0)}, 0, 0, 0, 5) is None


def test_plan_never_issues_an_empty_draw_and_covers_each_move_exactly_once():
    n = 103
    bounds = pd.chunk_bounds(n, 10)
    rng = random.Random(5)
    empties = 0
    for _ in range(2000):
        g_first = rng.randrange(-3, n + 3)
        g_last = rng.randrange(-3, n + 3)
        plan = pd.plan_ranges(bounds, g_first, g_last)
        assert all(r.count >= 1 for r in plan)
        covered = []
        for r in plan:
            s = bounds[r.chunk].s
            covered += [s - 1 + r.u_first + i for i in range(r.count)]       # move = s - 1 + j
        expect = list(range(max(g_first, 0), min(g_last, n - 1) + 1))
        assert covered == expect
        empties += not plan
    assert empties > 100                                                       # the empty case ran


def test_plan_skips_chunks_outside_the_range_and_uses_texel_offsets():
    b = pd.chunk_bounds(40, 10)
    plan = pd.plan_ranges(b, 12, 25)
    assert [(r.chunk, r.first_move, r.u_first, r.count) for r in plan] == [(1, 12, 3, 8), (2, 20, 1, 6)]


def test_int32_role_mask_and_vram_estimate():
    assert pd.as_int32(0xFFFFFFFF) == -1 and pd.as_int32(0x7FFFFFFF) == 2**31 - 1
    assert pd.as_int32(0x80000000) == -(2**31) and pd.as_int32(5) == 5
    assert pd.estimate_vram(10_000_000) == 280_000_000


def test_a_full_size_chunk_packs_within_the_tick_budget():
    import time
    m = make_moves(pd.CHUNK_MOVES + 10, per_layer=50_000)
    b = pd.chunk_bounds(len(m["type"]))[0]
    t0 = time.perf_counter()
    pd.pack_positions(m, b), pd.pack_meta(m, b), pd.pack_values(m, m["feedrate"], b)
    assert time.perf_counter() - t0 < 0.5          # generous: ~30 ms on a laptop, CI can be slow
