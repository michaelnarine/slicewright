# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview data: moves to per-chunk texture arrays, view scalars, marker lists, range planning.

Pure numpy, no ``bpy``/``gpu`` (03 sections 7.2 to 7.5 and 7.8). The renderer in
``blender/preview`` turns these arrays into textures; everything here is unit-testable.

Chunk layout (03 7.2). Moves are split into chunks of up to ``CHUNK_MOVES``. Chunk *c* covers
moves ``[s, e]`` and its textures hold ``e - s + 2`` texels at width ``TEX_W``: texel 0
duplicates move ``s - 1`` (move 0 for the first chunk) and texel ``j >= 1`` is move
``s - 1 + j``, so a segment's start point is always in the same chunk. Padding texels are
zero, which decodes as type 0 (a no-op) and is rejected by the shader.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np

TEX_W = 8192
CHUNK_MOVES = 1 << 20
META_EXPONENT = 0x3F800000      # float 1.0: the payload lives in the 23 mantissa bits
META_MASK = 0x7FFFFF
ROLE_BITS, TYPE_BITS, FILAMENT_BITS, NOZZLE_BITS = 5, 4, 8, 3
TYPE_SHIFT = ROLE_BITS
FILAMENT_SHIFT = TYPE_SHIFT + TYPE_BITS
NOZZLE_SHIFT = FILAMENT_SHIFT + FILAMENT_BITS          # bits 17..19; 20..22 are flags (unused)
BYTES_PER_MOVE = 16 + 8 + 4                              # t_pos + t_meta + t_val on the GPU

# Move types drawn as markers, in draw order: the names are those of ``enums()["move_type"]``.
MARKER_TYPES = ("Retract", "Unretract", "Seam", "Tool_change", "Color_change", "Pause_print",
                "Custom_gcode", "Wipe")

# View modes. "role" and the slot modes colour from a palette; the rest map a scalar to the range.
VIEW_FEATURE = "feature"
VIEW_RANGE_FIELDS = {          # view -> moves key (flow and layer time are computed)
    "speed": "feedrate", "actual_speed": "actual_feedrate", "height": "height", "width": "width",
    "fan": "fan", "temperature": "temperature", "pressure_advance": "pressure_advance",
    "acceleration": "acceleration", "jerk": "jerk"}
VIEW_COMPUTED = ("flow", "layer_time", "layer_time_log")
VIEW_SLOT_FIELDS = {"filament": "filament", "nozzle": "nozzle", "color": "color_id"}
VIEW_MODES = (VIEW_FEATURE, *VIEW_RANGE_FIELDS, *VIEW_COMPUTED, *VIEW_SLOT_FIELDS)


@dataclass(frozen=True)
class ChunkBounds:
    index: int
    s: int          # first move
    e: int          # last move, inclusive

    @property
    def n_moves(self) -> int:
        return self.e - self.s + 1

    @property
    def n_texels(self) -> int:
        return self.n_moves + 1

    @property
    def rows(self) -> int:
        return -(-self.n_texels // TEX_W)


def chunk_bounds(n_moves: int, chunk: int = CHUNK_MOVES) -> list[ChunkBounds]:
    if chunk < 1:
        raise ValueError("chunk must be positive")
    starts = range(0, n_moves, chunk)
    return [ChunkBounds(i, s, min(s + chunk, n_moves) - 1) for i, s in enumerate(starts)]


def _texels(column: np.ndarray, b: ChunkBounds) -> np.ndarray:
    """Per-texel view of ``column`` for chunk ``b``: texel j is move ``s - 1 + j`` (move 0 twice
    for the first chunk). A slice (no copy) except for the first chunk."""
    if b.s == 0:
        return np.concatenate((column[:1], column[:b.e + 1]))
    return column[b.s - 1:b.e + 1]


def pack_positions(moves: Mapping[str, np.ndarray], b: ChunkBounds) -> np.ndarray:
    """``t_pos`` RGBA32F as a (rows * TEX_W, 4) float32 array: x, y, z, width."""
    out = np.zeros((b.rows * TEX_W, 4), np.float32)
    t = b.n_texels
    out[:t, :3] = _texels(moves["position"], b)
    out[:t, 3] = _texels(moves["width"], b)
    return out


def pack_meta(moves: Mapping[str, np.ndarray], b: ChunkBounds) -> np.ndarray:
    """``t_meta`` RG32F as float *bit patterns* in a (rows * TEX_W, 2) uint32 array.

    ``.r`` = role (5 bits) | type (4) << 5 | filament (8) << 9 | nozzle (3) << 17 (flags above);
    ``.g`` = layer id. Each channel carries 23 payload bits under exponent ``0x3F800000``, so it
    is a finite float on every backend; the shader masks with ``0x7FFFFF`` (03 7.2)."""
    t = b.n_texels
    out = np.zeros((b.rows * TEX_W, 2), np.uint32)
    r = out[:t, 0]
    r[:] = _texels(moves["role"], b) & 31
    r |= (_texels(moves["type"], b).astype(np.uint32) & 15) << TYPE_SHIFT
    r |= _texels(moves["filament"], b).astype(np.uint32) << FILAMENT_SHIFT
    r |= (_texels(moves["nozzle"], b).astype(np.uint32) & 7) << NOZZLE_SHIFT
    out[:t, 1] = _texels(moves["layer_id"], b)
    out[:t] |= np.uint32(META_EXPONENT)
    return out


def unpack_meta(bits: np.ndarray) -> dict[str, np.ndarray]:
    """Inverse of :func:`pack_meta` (for tests and the inspector)."""
    r = bits[..., 0] & np.uint32(META_MASK)
    return {"role": r & 31, "type": (r >> TYPE_SHIFT) & 15, "filament": (r >> FILAMENT_SHIFT) & 255,
            "nozzle": (r >> NOZZLE_SHIFT) & 7, "layer_id": bits[..., 1] & np.uint32(META_MASK)}


def pack_values(moves: Mapping[str, np.ndarray], scalar: np.ndarray | None,
                b: ChunkBounds) -> np.ndarray:
    """``t_val`` RG16F source as (rows * TEX_W, 2) float32: height, active view scalar."""
    out = np.zeros((b.rows * TEX_W, 2), np.float32)
    t = b.n_texels
    out[:t, 0] = _texels(moves["height"], b)
    if scalar is not None:
        out[:t, 1] = _texels(scalar, b)
    return out


def marker_moves(moves: Mapping[str, np.ndarray], b: ChunkBounds,
                 type_ids: Mapping[str, int]) -> tuple[np.ndarray, dict[str, tuple[int, int]]]:
    """Marker list of chunk ``b``: texel indices of marker moves, grouped by kind.

    Returns ``(texels, slices)``: ``texels`` is float32 (floats are exact below 2**24, and avoid
    integer textures), ordered kind by kind then by move; ``slices[kind] = (start, count)`` into
    it. Texel of move ``m`` is ``m - s + 1``. Kinds with no marker are absent."""
    types = moves["type"][b.s:b.e + 1]
    parts, slices, start = [], {}, 0
    for kind in MARKER_TYPES:
        if kind not in type_ids:
            continue
        idx = np.flatnonzero(types == type_ids[kind])
        if len(idx):
            parts.append(idx + 1)          # (m - s) + 1
            slices[kind] = (start, len(idx))
            start += len(idx)
    texels = np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32)
    return texels, slices


def marker_range(moves_of_kind: np.ndarray, first: int, last: int) -> tuple[int, int]:
    """(start, count) of the markers whose move index (sorted) lies in ``[first, last]``."""
    a = int(np.searchsorted(moves_of_kind, first, "left"))
    return a, int(np.searchsorted(moves_of_kind, last, "right")) - a


# ----------------------------------------------------------------------------- view scalars

def layer_times(moves: Mapping[str, np.ndarray], layers: Mapping[str, np.ndarray]) -> np.ndarray:
    """Per-layer duration in seconds (normal mode)."""
    if len(layers["first"]) == 0:
        return np.zeros(0, np.float64)
    return np.add.reduceat(moves["time"][:, 0].astype(np.float64), layers["first"].astype(np.int64))


def view_scalar(moves: Mapping[str, np.ndarray], layers: Mapping[str, np.ndarray],
                view: str) -> np.ndarray | None:
    """float32 scalar per move for ``view`` (None for the feature-type view)."""
    if view == VIEW_FEATURE:
        return None
    if view in VIEW_RANGE_FIELDS:
        return moves[VIEW_RANGE_FIELDS[view]].astype(np.float32, copy=False)
    if view in VIEW_SLOT_FIELDS:
        return moves[VIEW_SLOT_FIELDS[view]].astype(np.float32)
    if view == "flow":
        return (moves["mm3_per_mm"] * moves["feedrate"]).astype(np.float32)
    if view in ("layer_time", "layer_time_log"):
        per_layer = layer_times(moves, layers)
        if view == "layer_time_log":
            per_layer = np.log10(np.maximum(per_layer, 1e-3))
        return per_layer.astype(np.float32)[moves["layer_id"]]
    raise ValueError(f"unknown view mode {view!r}")


def value_range(scalar: np.ndarray, mask: np.ndarray | None = None, lo_pct: float = 0.5,
                hi_pct: float = 99.5, max_samples: int = 1 << 20) -> tuple[float, float]:
    """Palette range: the 0.5 to 99.5 percentile of ``scalar`` over ``mask`` (extrusions).

    Strided subsampling above ``max_samples`` keeps a view change fast on 10M moves."""
    v = scalar if mask is None else scalar[mask]
    if len(v) > max_samples:
        v = v[::-(-len(v) // max_samples)]
    if len(v) == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(v, [lo_pct, hi_pct])
    return (float(lo), float(hi)) if hi > lo else (float(lo), float(lo) + 1.0)


# ----------------------------------------------------------------------------- ranges (03 7.4)

def visible_range(layers: Mapping[str, np.ndarray], lo: int, hi: int, p: int | None,
                  uploaded_last: int) -> tuple[int, int] | None:
    """Global move range ``(g_first, g_last)`` for layers ``[lo, hi]`` and in-layer position ``p``
    of the top layer (None: the whole layer); None when nothing is visible."""
    n = len(layers["first"])
    if n == 0:
        return None
    lo, hi = max(0, min(lo, n - 1)), max(0, min(hi, n - 1))
    if hi < lo:
        return None
    g_first = int(layers["first"][lo])
    g_last = int(layers["last"][hi]) if p is None else min(int(layers["first"][hi]) + p,
                                                           int(layers["last"][hi]))
    g_last = min(g_last, uploaded_last)
    return (g_first, g_last) if g_last >= g_first else None


@dataclass(frozen=True)
class DrawRange:
    chunk: int
    first_move: int     # first visible move in the chunk
    u_first: int        # texel of that move: ``first_move - s + 1``
    count: int          # instances; always >= 1 (instance_count=0 would draw one instance)


def plan_ranges(bounds: list[ChunkBounds], g_first: int, g_last: int) -> list[DrawRange]:
    """One entry per chunk the range touches. Empty ranges are skipped, never issued."""
    out = []
    for b in bounds:
        first, last = max(g_first, b.s), min(g_last, b.e)
        if last >= first:
            out.append(DrawRange(b.index, first, first - b.s + 1, last - first + 1))
    return out


def as_int32(mask: int) -> int:
    """A 32-bit mask as a signed int for the INT push constant (UINT cannot be set from Python)."""
    mask &= 0xFFFFFFFF
    return mask - (1 << 32) if mask >= (1 << 31) else mask


def estimate_vram(n_moves: int, with_val: bool = True) -> int:
    """Bytes of textures for ``n_moves`` moves (28 B/move, row padding ignored)."""
    return n_moves * (BYTES_PER_MOVE if with_val else BYTES_PER_MOVE - 4)


def plan_markers(kind_moves: Mapping[str, np.ndarray], slices: Mapping[str, tuple[int, int]],
                 kinds: Iterable[str], first: int, last: int) -> list[tuple[str, int, int]]:
    """Marker draws for one chunk and the visible move range ``[first, last]``.

    ``kind_moves[kind]`` are the sorted move indices of that kind in the chunk and
    ``slices[kind] = (start, count)`` locates them in the chunk's marker texture (from
    :func:`marker_moves`). Returns ``(kind, first_marker_texel, count)``, never with count 0."""
    out = []
    for kind in kinds:
        if kind not in slices:
            continue
        a, n = marker_range(kind_moves[kind], first, last)
        if n:
            out.append((kind, slices[kind][0] + a, n))
    return out
