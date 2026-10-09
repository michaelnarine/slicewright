# SPDX-License-Identifier: GPL-3.0-or-later
"""Chunked vertex-pulling renderer for toolpaths (03 sections 7.1 to 7.4).

Each chunk of up to ~1M moves owns three small textures (``t_pos`` RGBA32F, ``t_meta`` RG32F
float bits, ``t_val`` RG16F) built once from ``core.preview_data``. Drawing issues one
``draw_instanced`` per chunk range, so scrubbing, layer ranges, role masks and view changes only
change push constants and instance counts: **no texture is created while drawing** (a counter
proves it). Texture and buffer creation is isolated in ``build_chunk`` / ``rebuild_values``.

Needs a GPU context (GUI Blender); the data side is in ``core.preview_data``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

from . import shaders, templates
from ...core import preview_data as pd
from ...core import preview_palette as pp


@dataclass
class DrawParams:
    lo: int = 0                         # first visible layer
    hi: int | None = None               # last visible layer (None: the last one)
    p: int | None = None                # in-layer move position of the top layer (None: all)
    role_mask: int = 0xFFFFFFFF         # bit r set: role id r is drawn
    grey_below: int = 0                 # layers below this are drawn grey
    view_mode: int = shaders.VIEW_ROLE
    show_travel: bool = False
    markers: tuple = ()                 # kind names from preview_data.MARKER_TYPES
    lines_lod: bool = False             # no tubes: shaded-free lines (above the segment threshold)
    viewport: tuple = (1.0, 1.0)        # region size in pixels, for screen-space markers
    marker_px: float = 4.0
    nozzle: tuple | None = None         # world position of the scrub marker, or None


@dataclass
class Chunk:
    bounds: pd.ChunkBounds
    t_pos: object
    t_meta: object
    t_val: object
    t_idx: object = None                # R32F marker texels, or None without markers
    slices: dict = field(default_factory=dict)
    kind_moves: dict = field(default_factory=dict)
    timing: dict = field(default_factory=dict)


class Renderer:
    def __init__(self, enums: Mapping[str, Mapping[str, int]]) -> None:
        self.type_ids = dict(enums["move_type"])
        self.role_ids = dict(enums["role"])
        self.layers: Mapping[str, np.ndarray] | None = None
        self.bounds: list[pd.ChunkBounds] = []
        self.chunks: list[Chunk] = []
        self.uploaded_last = -1
        self.range = (0.0, 1.0)
        self.stats = {"textures_created": 0, "draw_calls": 0, "instances": 0}
        self._shaders: dict = {}
        self._batches: dict = {}
        self._ubo = None
        self._dummy = None

    # ------------------------------------------------------------------ setup

    def begin(self, n_moves: int, layers: Mapping[str, np.ndarray],
              chunk: int = pd.CHUNK_MOVES) -> list[pd.ChunkBounds]:
        """Forget any chunks and plan the layout for ``n_moves`` moves."""
        self.release()
        self.layers = layers
        self.bounds = pd.chunk_bounds(n_moves, chunk)
        return self.bounds

    def release(self) -> None:
        """Drop every GPU object (textures free when their last reference goes)."""
        self.chunks.clear()
        self.uploaded_last = -1
        self._shaders.clear()
        self._batches.clear()
        self._ubo = None
        self._dummy = None

    def ensure_gpu(self) -> None:
        if self._shaders:
            return
        import gpu
        self._shaders = {"tubes": shaders.create_path_shader(self.type_ids, False),
                         "lines": shaders.create_path_shader(self.type_ids, True),
                         "markers": shaders.create_marker_shader(self.type_ids),
                         "nozzle": shaders.create_nozzle_shader()}
        self._batches = {"strip": templates.make_template("strip"),
                         "lines": templates.make_template("lines")}
        pal = self._palette()
        self._ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', pal.size, pal.reshape(-1)))

    def _palette(self) -> np.ndarray:
        return pp.palette_array(self.role_ids, self.type_ids, *self.range)

    def set_range(self, lo: float, hi: float) -> None:
        """Palette min/max of the active range view (updates the UBO, no textures)."""
        self.range = (lo, hi)
        if self._ubo is not None:
            import gpu
            pal = self._palette()
            self._ubo.update(gpu.types.Buffer('FLOAT', pal.size, pal.reshape(-1)))

    # ------------------------------------------------------------------ building

    def _tex(self, rows: int, fmt: str, arr: np.ndarray):
        import gpu
        flat = np.ascontiguousarray(arr).view(np.float32).reshape(-1)
        tex = gpu.types.GPUTexture((pd.TEX_W, rows), format=fmt,
                                   data=gpu.types.Buffer('FLOAT', flat.size, flat))
        self.stats["textures_created"] += 1
        return tex

    def build_chunk(self, moves: Mapping[str, np.ndarray], b: pd.ChunkBounds,
                    scalar: np.ndarray | None = None, register: bool = True,
                    with_values: bool = True) -> Chunk:
        """Pack chunk ``b`` and create its textures. Chunks must be registered in order.

        ``with_values=False`` (over the VRAM budget) binds one shared dummy ``t_val`` instead of a
        per-chunk texture: only the feature-type view may then be drawn."""
        t0 = time.perf_counter()
        self.ensure_gpu()
        pos = pd.pack_positions(moves, b)
        meta = pd.pack_meta(moves, b)
        val = pd.pack_values(moves, scalar, b) if with_values else None
        t1 = time.perf_counter()
        texels, slices = pd.marker_moves(moves, b, self.type_ids)
        kind_moves = {k: texels[s:s + n].astype(np.int64) + (b.s - 1) for k, (s, n) in slices.items()}
        t_idx = None
        if len(texels):
            rows = -(-len(texels) // pd.TEX_W)
            padded = np.zeros(rows * pd.TEX_W, np.float32)
            padded[:len(texels)] = texels
            t_idx = self._tex(rows, 'R32F', padded)
        chunk = Chunk(b, self._tex(b.rows, 'RGBA32F', pos), self._tex(b.rows, 'RG32F', meta),
                      self._tex(b.rows, 'RG16F', val) if with_values else self._dummy_values(),
                      t_idx, slices, kind_moves)
        chunk.timing = {"pack_ms": (t1 - t0) * 1e3, "total_ms": (time.perf_counter() - t0) * 1e3}
        if register:
            if b.index != len(self.chunks):
                raise ValueError(f"chunk {b.index} registered out of order ({len(self.chunks)} built)")
            self.chunks.append(chunk)
            self.uploaded_last = b.e
        return chunk

    def _dummy_values(self):
        if self._dummy is None:
            self._dummy = self._tex(1, 'RG16F', np.zeros((pd.TEX_W, 2), np.float32))
        return self._dummy

    def rebuild_values(self, chunk: Chunk, moves: Mapping[str, np.ndarray],
                       scalar: np.ndarray | None) -> None:
        """New ``t_val`` for a view change: the only texture a view switch recreates."""
        chunk.t_val = self._tex(chunk.bounds.rows, 'RG16F', pd.pack_values(moves, scalar, chunk.bounds))

    # ------------------------------------------------------------------ drawing

    def plan(self, params: DrawParams) -> list[pd.DrawRange]:
        if self.layers is None or not self.chunks:
            return []
        hi = len(self.layers["first"]) - 1 if params.hi is None else params.hi
        rng = pd.visible_range(self.layers, params.lo, hi, params.p, self.uploaded_last)
        return [] if rng is None else pd.plan_ranges(self.bounds, *rng)

    def draw(self, view_proj, eye, params: DrawParams) -> int:
        """Draw the visible range; returns the number of ``draw_instanced`` calls issued."""
        return self.draw_plan(self.plan(params), view_proj, eye, params)

    def draw_plan(self, plan: list[pd.DrawRange], view_proj, eye, params: DrawParams) -> int:
        """Draw an explicit plan (tests use this to compare chunkings)."""
        if not plan:
            return 0
        import gpu
        self.ensure_gpu()
        depth_test, depth_mask = gpu.state.depth_test_get(), gpu.state.depth_mask_get()
        coverage = params.view_mode == shaders.VIEW_COVERAGE     # debug: additive, no depth
        gpu.state.depth_test_set('NONE' if coverage else 'LESS_EQUAL')
        gpu.state.depth_mask_set(not coverage)
        calls = 0
        try:
            calls += self._draw_paths(plan, view_proj, eye, params, travel=False)
            if params.show_travel:
                calls += self._draw_paths(plan, view_proj, eye, params, travel=True)
            if params.markers:
                calls += self._draw_markers(plan, view_proj, params)
            if params.nozzle is not None:
                calls += self._draw_nozzle(view_proj, params)
        finally:
            gpu.state.depth_test_set(depth_test)
            gpu.state.depth_mask_set(depth_mask)
        self.stats["draw_calls"] += calls
        return calls

    def _draw_paths(self, plan, view_proj, eye, params: DrawParams, travel: bool) -> int:
        lines = params.lines_lod or travel
        sh = self._shaders["lines" if lines else "tubes"]
        batch = self._batches["lines" if lines else "strip"][0]
        sh.uniform_block("pal", self._ubo)
        sh.uniform_float("u_vp", view_proj)
        sh.uniform_float("u_eye", tuple(eye))
        sh.uniform_int("u_grey_below", params.grey_below)
        sh.uniform_int("u_role_mask", pd.as_int32(params.role_mask))
        sh.uniform_int("u_view_mode", params.view_mode)
        sh.uniform_int("u_pass", shaders.PASS_TRAVEL if travel else shaders.PASS_EXTRUDE)
        for dr in plan:
            ch = self.chunks[dr.chunk]
            sh.uniform_int("u_first", dr.u_first)
            sh.uniform_sampler("t_pos", ch.t_pos)
            sh.uniform_sampler("t_meta", ch.t_meta)
            sh.uniform_sampler("t_val", ch.t_val)
            batch.draw_instanced(sh, instance_start=0, instance_count=dr.count)
            self.stats["instances"] += dr.count
        return len(plan)

    def _draw_markers(self, plan, view_proj, params: DrawParams) -> int:
        sh = self._shaders["markers"]
        batch = self._batches["strip"][0]
        sh.uniform_block("pal", self._ubo)
        sh.uniform_float("u_vp", view_proj)
        sh.uniform_float("u_viewport", tuple(params.viewport))
        sh.uniform_float("u_marker_px", params.marker_px)
        calls = 0
        for dr in plan:
            ch = self.chunks[dr.chunk]
            if ch.t_idx is None:
                continue
            last = dr.first_move + dr.count - 1
            for _kind, first_texel, count in pd.plan_markers(ch.kind_moves, ch.slices,
                                                             params.markers, dr.first_move, last):
                sh.uniform_int("u_first", first_texel)
                sh.uniform_sampler("t_pos", ch.t_pos)
                sh.uniform_sampler("t_meta", ch.t_meta)
                sh.uniform_sampler("t_idx", ch.t_idx)
                batch.draw_instanced(sh, instance_start=0, instance_count=count)
                calls += 1
        return calls

    def _draw_nozzle(self, view_proj, params: DrawParams) -> int:
        sh = self._shaders["nozzle"]
        sh.uniform_float("u_vp", view_proj)
        sh.uniform_float("u_pos", tuple(params.nozzle))
        sh.uniform_float("u_viewport", tuple(params.viewport))
        sh.uniform_float("u_marker_px", params.marker_px * 2.5)
        self._batches["strip"][0].draw_instanced(sh, instance_start=0, instance_count=1)
        return 1
