# SPDX-License-Identifier: GPL-3.0-or-later
"""Uploads a result to the renderer one chunk per tick and applies view changes (03 section 7.8).

Everything runs as a generator task on the shared :class:`~slicewright.core.ticking.TickRunner`
(no threads): each ``yield`` ends one step, so a step is one chunk build (<= 40 ms) or one view
scalar computation. ``renderer`` is injected (anything with ``begin``, ``build_chunk``,
``rebuild_values``, ``set_range``, ``release`` and ``bounds``), so the sequencing is testable
without a GPU.
"""
from __future__ import annotations

import logging

import numpy as np

from ...core import preview_budget as budget
from ...core import preview_data as pd

log = logging.getLogger("slicewright.preview")


class PreviewController:
    def __init__(self, rt, renderer, runner, budget_bytes: int, enums, chunk: int = pd.CHUNK_MOVES,
                 name: str = "preview") -> None:
        self.rt, self.renderer, self.runner, self.name = rt, renderer, runner, name
        self.enums = enums
        self.chunk = chunk
        self.bounds = renderer.begin(rt.n_moves, rt.layers, chunk)
        self.plan = budget.plan_budget(self.bounds, budget_bytes)
        self.view = pd.VIEW_FEATURE
        self.fixed_range: tuple | None = None
        self.progress = 0.0
        self.error: BaseException | None = None
        self.built: list[int] = []                 # view token each uploaded chunk was built with
        self._token = 0                            # bumped on every view change
        self._scalar: np.ndarray | None = None
        self._range = (0.0, 1.0)
        self._task = None
        self._extrude = enums["move_type"]["Extrude"]
        rt.controller = self

    # ------------------------------------------------------------------ state

    @property
    def uploaded_moves(self) -> int:
        return self.renderer.uploaded_last + 1

    @property
    def done(self) -> bool:
        return self._task is None and len(self.built) == self.plan.n_chunks and not self.stale

    @property
    def stale(self) -> bool:
        return any(t != self._token for t in self.built)

    @property
    def value_range(self) -> tuple:
        """Min and max of the active range view (for the legend)."""
        return self._range

    @property
    def messages(self) -> list:
        return list(self.plan.messages)

    def view_allowed(self, view: str) -> bool:
        return view == pd.VIEW_FEATURE or self.plan.with_values

    # ------------------------------------------------------------------ running

    def start(self) -> None:
        self._schedule()

    def cancel(self) -> None:
        if self._task is not None:
            self.runner.cancel(self._task)
            self._task = None

    def release(self) -> None:
        self.cancel()
        self.renderer.release()
        self.built.clear()
        if self.rt.controller is self:
            self.rt.controller = None

    def set_view(self, view: str, fixed_range: tuple | None = None) -> bool:
        """Switch the view mode (and range); returns False if the budget forbids it.
        Textures are rebuilt on the next ticks: only each chunk's ``t_val``."""
        if not self.view_allowed(view):
            return False
        if view == self.view and fixed_range == self.fixed_range:
            return True
        self.view, self.fixed_range = view, fixed_range
        self._token += 1
        self._scalar = None
        self._schedule()
        return True

    def _schedule(self) -> None:
        if self._task is None or self._task.finished:
            self._task = self.runner.submit(self.name, self._work, on_done=self._finished,
                                            on_error=self._failed, on_cancel=self._cancelled, replace=True)

    def _finished(self, _result) -> None:
        self._task = None
        if self.stale or len(self.built) < self.plan.n_chunks:      # a view change raced the end
            self._schedule()
        else:
            self._scalar = None                                      # free the staging scalar
            self.progress = 1.0

    def _failed(self, exc: BaseException) -> None:
        self._task, self.error = None, exc
        log.error("preview upload failed: %s", exc)

    def _cancelled(self) -> None:
        self._task = None

    def _ensure_scalar(self) -> None:
        r = self.rt
        self._scalar = pd.view_scalar(r.moves, r.layers, self.view)
        self._range = (0.0, 1.0)
        if pd.shader_mode_for(self.view) == pd.RANGE_MODE:
            self._range = self.fixed_range or pd.value_range(self._scalar, r.moves["type"] == self._extrude)
        self.renderer.set_range(*self._range)

    def _work(self):
        r, plan, total = self.rt, self.plan, max(self.plan.n_chunks, 1)
        while True:
            token = self._token
            feature = self.view == pd.VIEW_FEATURE
            if feature:
                self.renderer.set_range(0.0, 1.0)
            elif self._scalar is None:
                self._ensure_scalar()
                yield (self.progress, "Computing view")
            for i, b in enumerate(self.bounds[:plan.n_chunks]):
                if token != self._token:
                    break                                           # the view changed: start over
                if i >= len(self.built):
                    self.renderer.build_chunk(r.moves, b, self._scalar, with_values=plan.with_values)
                elif self.built[i] != token and plan.with_values and not feature:
                    self.renderer.rebuild_values(self.renderer.chunks[i], r.moves, self._scalar)
                else:
                    if i < len(self.built):
                        self.built[i] = token                       # feature view ignores t_val
                    continue
                if i < len(self.built):
                    self.built[i] = token
                else:
                    self.built.append(token)
                self.progress = (i + 1) / total
                yield (self.progress, f"Chunk {i + 1} of {total}")
            if token == self._token:
                return None
