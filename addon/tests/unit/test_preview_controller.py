# SPDX-License-Identifier: GPL-3.0-or-later
"""VRAM budget plans and the upload/view-change sequencing, with a renderer stand-in (no GPU)."""
from __future__ import annotations

import numpy as np
import pytest

from fake_engine import api
from fake_engine.gcode import from_gcode
from slicewright.blender.preview.controller import PreviewController
from slicewright.blender.preview.runtime import PreviewRuntime
from slicewright.core import preview_budget as budget
from slicewright.core import preview_data as pd
from slicewright.core.ticking import TickRunner

from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "gcode" / "plain_square.gcode"
MB = budget.MB


def test_default_budget_is_conservative_on_integrated_gpus():
    assert budget.default_budget_mb("INTEL", 64) == 384
    assert budget.default_budget_mb("APPLE", 8, apple=True) == 384
    assert budget.default_budget_mb("APPLE", 16, apple=True) == 1024
    assert budget.default_budget_mb("APPLE", None) == 384                 # unknown memory counts as small
    assert budget.default_budget_mb("NVIDIA", 8) == 1024 and budget.default_budget_mb("AMD", None) == 1024
    assert budget.system_ram_gb() is None or budget.system_ram_gb() > 0


def test_chunk_bytes_include_row_padding_and_28_bytes_per_texel():
    b = pd.chunk_bounds(pd.CHUNK_MOVES)[0]
    assert budget.chunk_bytes(b) == 129 * 8192 * 28 and budget.chunk_bytes(b, False) == 129 * 8192 * 24
    assert abs(budget.chunk_bytes(b) / (b.n_moves * 28) - 1) < 0.01


def test_plan_fits_then_drops_values_then_truncates():
    bounds = pd.chunk_bounds(5 * pd.CHUNK_MOVES)
    per = budget.chunk_bytes(bounds[0])
    full = budget.plan_budget(bounds, 5 * per)
    assert full.with_values and full.n_chunks == 5 and not full.truncated and not full.messages
    nov = budget.plan_budget(bounds, 5 * budget.chunk_bytes(bounds[0], False))
    assert not nov.with_values and nov.n_chunks == 5 and "Feature type" in nov.messages[0]
    cut = budget.plan_budget(bounds, 3 * budget.chunk_bytes(bounds[0], False) + 10)
    assert cut.truncated and cut.n_chunks == 3 and cut.last_move == bounds[2].e
    assert "first 3,145,728 of 5,242,880 moves" in cut.messages[1]
    none = budget.plan_budget(bounds, 10)
    assert none.n_chunks == 0 and none.last_move is None
    assert budget.plan_budget([], 100).n_chunks == 0


def test_ten_million_moves_need_about_280_mb_and_fit_the_default_gpu_budget():
    bounds = pd.chunk_bounds(10_000_000)
    plan = budget.plan_budget(bounds, 1024 * MB)
    assert plan.with_values and 280e6 <= plan.bytes_full <= 300e6
    small = budget.plan_budget(bounds, 384 * MB)
    assert small.with_values                              # 10M moves fit even the iGPU budget


class FakeRenderer:
    def __init__(self):
        self.chunks, self.uploaded_last, self.range, self.calls = [], -1, None, []

    def begin(self, n, layers, chunk):
        self.chunks.clear()
        return pd.chunk_bounds(n, chunk)

    def build_chunk(self, moves, b, scalar=None, register=True, with_values=True):
        self.calls.append(("build", b.index, scalar is not None, with_values))
        self.chunks.append(b)
        self.uploaded_last = b.e

    def rebuild_values(self, chunk, moves, scalar):
        self.calls.append(("rebuild", chunk.index))

    def set_range(self, lo, hi):
        self.range = (lo, hi)

    def release(self):
        self.calls.append(("release",))
        self.chunks.clear()


def make(chunk=16, budget_mb=1024):
    rt = PreviewRuntime(from_gcode(str(FIXTURE)))
    runner = TickRunner(budget_s=1e-9)                    # a tick advances exactly one step
    r = FakeRenderer()
    return PreviewController(rt, r, runner, budget_mb * MB, api.enums(), chunk), r, runner


def test_upload_is_one_chunk_per_tick_and_reports_progress():
    ctl, r, runner = make()
    n_chunks = len(ctl.bounds)
    assert n_chunks > 2
    ctl.start()
    builds = 0
    while not runner.idle:
        runner.tick()
        assert len(r.chunks) - builds <= 1                # never more than one chunk per tick
        builds = len(r.chunks)
    assert ctl.done and ctl.progress == 1.0 and [c[1] for c in r.calls] == list(range(n_chunks))
    assert r.uploaded_last == ctl.rt.n_moves - 1 and ctl.uploaded_moves == ctl.rt.n_moves


def test_a_partial_upload_leaves_a_drawable_prefix():
    ctl, r, runner = make()
    ctl.start()
    runner.tick()
    runner.tick()
    assert 0 < len(r.chunks) < len(ctl.bounds) and not ctl.done
    assert r.uploaded_last == ctl.bounds[len(r.chunks) - 1].e


def test_view_change_rebuilds_only_the_values_not_the_chunks():
    ctl, r, runner = make()
    ctl.start()
    runner.run_until_idle()
    r.calls.clear()
    assert ctl.set_view("speed")
    runner.run_until_idle()
    kinds = {c[0] for c in r.calls}
    assert kinds == {"rebuild"} and len(r.calls) == len(ctl.bounds) and ctl.done
    assert r.range[0] < r.range[1]
    r.calls.clear()
    assert ctl.set_view("feature")                       # the feature view needs no new textures
    runner.run_until_idle()
    assert r.calls == [] and ctl.done and r.range == (0.0, 1.0)


def test_fixed_range_is_used_and_same_view_is_a_no_op():
    ctl, r, runner = make()
    ctl.start()
    runner.run_until_idle()
    ctl.set_view("temperature", (150.0, 250.0))
    runner.run_until_idle()
    assert r.range == (150.0, 250.0)
    r.calls.clear()
    assert ctl.set_view("temperature", (150.0, 250.0)) and not r.calls and runner.idle


def test_a_view_change_during_upload_is_applied_to_every_chunk():
    ctl, r, runner = make()
    ctl.start()
    runner.tick()
    runner.tick()
    ctl.set_view("flow")
    runner.run_until_idle()
    assert ctl.done and not ctl.stale
    assert {c[1] for c in r.calls if c[0] == "build"} == set(range(len(ctl.bounds)))
    built_with_scalar = [c for c in r.calls if c[0] == "build" and c[2]]
    rebuilt = {c[1] for c in r.calls if c[0] == "rebuild"}
    early = {c[1] for c in r.calls if c[0] == "build" and not c[2]}
    assert early <= rebuilt and len(built_with_scalar) + len(early) == len(ctl.bounds)


def test_over_budget_forbids_views_and_truncates():
    ctl, r, runner = make(chunk=16, budget_mb=0)
    assert not ctl.plan.with_values and ctl.plan.n_chunks == 0 and ctl.messages
    assert ctl.view_allowed("feature") and not ctl.view_allowed("speed")
    assert ctl.set_view("speed") is False
    ctl.start()
    runner.run_until_idle()
    assert r.chunks == [] and ctl.done
    tiny = PreviewRuntime(from_gcode(str(FIXTURE)))
    per = budget.chunk_bytes(pd.chunk_bounds(16, 16)[0], False)
    c2 = PreviewController(tiny, FakeRenderer(), TickRunner(), 2 * per, api.enums(), 16)
    c2.start()
    c2.runner.run_until_idle()
    assert c2.plan.truncated and len(c2.renderer.chunks) == 2 and all(c[3] is False for c in c2.renderer.calls)
    assert c2.renderer.uploaded_last == c2.bounds[1].e


def test_cancel_and_release_stop_work_and_free_the_renderer():
    ctl, r, runner = make()
    ctl.start()
    runner.tick()
    ctl.cancel()
    assert runner.idle and len(r.chunks) < len(ctl.bounds)
    ctl.release()
    assert ("release",) in r.calls and ctl.rt.controller is None


def test_a_failing_build_is_reported_not_raised():
    ctl, r, runner = make()

    def boom(*a, **k):
        raise RuntimeError("gpu out of memory")
    r.build_chunk = boom
    ctl.start()
    runner.run_until_idle()
    assert isinstance(ctl.error, RuntimeError) and not ctl.done
