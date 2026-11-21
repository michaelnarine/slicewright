# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake geometry: toolpath moves to hair-curve and prism arrays (03 section 7.11). Pure numpy, no ``bpy``.

Move *i* of ``result.moves`` is the end point of the segment from move *i - 1* to move *i* (04 5.2). A bake draws a chosen
set of those segments and groups them into **paths**: maximal runs of consecutive moves with the same feature (role, or
travel), filament, layer and object. A path of *n* segments has *n + 1* points, the positions of moves ``first - 1`` to
``last``, so its points are contiguous in the move table. Each point takes the values of the segment that ends there;
the first point copies the first segment.

Two builders turn a :class:`Selection` into arrays that ``blender/bake`` copies into Blender data:

* **curves**: one curve per path, one point per path point (:func:`fill_curves`);
* **prisms**: one swept polygonal prism per segment, ``sides`` vertices a ring and two rings a segment, with a cap at the two
  ends of a path (:func:`fill_prisms`). Where a path turns, each prism is lengthened by ``half width * tan(turn / 2)`` (at
  most half a width) so the outside of the corner has no wedge-shaped gap. Neighbouring prisms are not welded.

Both centre the extrusion at ``z - height / 2``: the move's z is the nozzle height, the top of the layer. Both work on
index ranges, so the Blender side can do one range per tick. Distances are scaled by ``scale`` (Blender units per mm); the
attributes stay in the slicer's units (mm, mm/s, mm3/s).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

TRAVEL_KEY = 32                 # the feature type of travel moves: one past the largest role id (< 32, 04 section 3)
ALL_ROLES = 0xFFFFFFFF
TRAVEL_SIZE_MM = 0.1            # width and height drawn for travel moves, which carry none
MIN_SIDES, MAX_SIDES = 3, 16
DEFAULT_SIDES = 6
ATTRIBUTES = ("feature_type", "layer", "width", "height", "speed", "flow", "filament")
INT_ATTRIBUTES = ("feature_type", "layer", "filament")


@dataclass(frozen=True)
class MoveTypes:
    """The engine's move-type ids (``enums()["move_type"]``) the bake needs."""
    extrude: int
    travel: int

    @classmethod
    def from_enums(cls, enums: Mapping[str, Mapping[str, int]]) -> "MoveTypes":
        types = enums["move_type"]
        return cls(int(types["Extrude"]), int(types["Travel"]))


@dataclass(frozen=True)
class Filter:
    """Which segments to bake: moves ``first..last`` (inclusive), extrusions of the roles whose bit is set in
    ``role_mask`` and, if ``travel``, travel moves."""
    first: int
    last: int
    role_mask: int = ALL_ROLES
    travel: bool = False


@dataclass
class Selection:
    """The chosen segments, in move order. ``idx[k]`` is the move that ends segment *k*; ``start[k]`` is True where a path
    begins (so ``start[0]`` is always True)."""
    idx: np.ndarray
    start: np.ndarray
    _path_first: np.ndarray | None = field(default=None, repr=False, compare=False)

    @property
    def n_segments(self) -> int:
        return len(self.idx)

    @property
    def path_first(self) -> np.ndarray:
        """Segment index of the first segment of each path (int64)."""
        if self._path_first is None:
            self._path_first = np.flatnonzero(self.start)
        return self._path_first

    @property
    def n_paths(self) -> int:
        return len(self.path_first)

    @property
    def n_points(self) -> int:
        return self.n_segments + self.n_paths

    def subset(self, mask: np.ndarray) -> "Selection":
        """The segments where ``mask`` is True. ``mask`` must keep or drop whole paths (:func:`split_groups` does)."""
        return Selection(self.idx[mask], self.start[mask])


def feature_keys(moves: Mapping[str, np.ndarray], idx: np.ndarray, types: MoveTypes) -> np.ndarray:
    """Feature type of the moves ``idx``: the role id, or :data:`TRAVEL_KEY` for travel (int32)."""
    key = moves["role"][idx].astype(np.int32)
    key[moves["type"][idx] == types.travel] = TRAVEL_KEY
    return key


def select(moves: Mapping[str, np.ndarray], flt: Filter, types: MoveTypes) -> Selection:
    """The segments ``flt`` asks for, split into paths. Move 0 has no segment and is never chosen."""
    n = len(moves["type"])
    first, last = max(int(flt.first), 1), min(int(flt.last), n - 1)
    if last < first:
        return Selection(np.zeros(0, np.int64), np.zeros(0, bool))
    kind = moves["type"][first:last + 1]
    role_ok = np.array([(flt.role_mask >> r) & 1 if r < 32 else 0 for r in range(256)], bool)
    ok = (kind == types.extrude) & role_ok[moves["role"][first:last + 1]]
    if flt.travel:
        ok |= kind == types.travel
    idx = np.flatnonzero(ok) + first
    start = np.ones(len(idx), bool)
    if len(idx) > 1:
        key = feature_keys(moves, idx, types)
        same = idx[1:] == idx[:-1] + 1
        same &= key[1:] == key[:-1]
        for name in ("filament", "layer_id", "object_id"):
            col = moves[name][idx]
            same &= col[1:] == col[:-1]
        start[1:] = ~same
    return Selection(idx, start)


def split_groups(moves: Mapping[str, np.ndarray], sel: Selection) -> list[tuple[int, Selection]]:
    """One selection per source object (``object_id``, -1 for the skirt, the wipe tower and custom G-code), in order of
    first appearance. Paths never span objects (:func:`select` breaks them there)."""
    if sel.n_segments == 0:
        return []
    obj = moves["object_id"][sel.idx]
    ids = np.unique(obj)
    if len(ids) == 1:
        return [(int(ids[0]), sel)]
    first_seen = {int(i): int(np.argmax(obj == i)) for i in ids}
    return [(i, sel.subset(obj == i)) for i in sorted(first_seen, key=first_seen.get)]


def part_ranges(n: int, size: int) -> list[tuple[int, int]]:
    """``[a, b)`` ranges of at most ``size`` covering ``0..n`` (empty for ``n == 0``)."""
    if size < 1:
        raise ValueError("size must be positive")
    return [(a, min(a + size, n)) for a in range(0, n, size)]


def roles_present(moves: Mapping[str, np.ndarray], sel: Selection, types: MoveTypes) -> list[int]:
    """The sorted feature types (role ids, :data:`TRAVEL_KEY`) among the selected segments."""
    if sel.n_segments == 0:
        return []
    return [int(k) for k in np.unique(feature_keys(moves, sel.idx, types))]


def material_lut(keys: list[int]) -> np.ndarray:
    """int32 lookup from a feature type (0..32) to its slot among ``keys`` (0 for a type that is not in ``keys``)."""
    lut = np.zeros(TRAVEL_KEY + 1, np.int32)
    for slot, key in enumerate(keys):
        lut[key] = slot
    return lut


# ----------------------------------------------------------------------------- per-point values

def _values(moves: Mapping[str, np.ndarray], src: np.ndarray, types: MoveTypes) -> dict[str, np.ndarray]:
    """The attribute values (:data:`ATTRIBUTES`) of the moves ``src``, plus the feature key."""
    key = feature_keys(moves, src, types)
    speed = moves["feedrate"][src]
    return {
        "feature_type": key,
        "layer": moves["layer_id"][src].astype(np.int32),
        "width": moves["width"][src],
        "height": moves["height"][src],
        "speed": speed,
        "flow": (moves["mm3_per_mm"][src] * speed).astype(np.float32),
        "filament": moves["filament"][src].astype(np.int32),
    }


def _size_mm(values: Mapping[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Drawn width and height in mm: the moves' own, :data:`TRAVEL_SIZE_MM` for travel (which has none)."""
    travel = values["feature_type"] == TRAVEL_KEY
    return (np.where(travel, TRAVEL_SIZE_MM, values["width"]).astype(np.float64),
            np.where(travel, TRAVEL_SIZE_MM, values["height"]).astype(np.float64))


# ----------------------------------------------------------------------------- curves

@dataclass
class CurveArrays:
    """The arrays of a whole curves bake, allocated once and filled path range by path range."""
    sizes: np.ndarray                   # int32 (curves,): points of each curve
    material_index: np.ndarray          # int32 (curves,)
    position: np.ndarray                # float32 (points, 3), Blender units
    radius: np.ndarray                  # float32 (points,), Blender units
    attributes: dict[str, np.ndarray]   # :data:`ATTRIBUTES`, (points,)

    @property
    def n_points(self) -> int:
        return len(self.radius)


def alloc_curves(sel: Selection) -> CurveArrays:
    n, p = sel.n_segments, sel.n_paths
    ends = np.append(sel.path_first[1:], n)
    sizes = (ends - sel.path_first + 1).astype(np.int32)
    attrs = {name: np.zeros(n + p, np.int32 if name in INT_ATTRIBUTES else np.float32) for name in ATTRIBUTES}
    return CurveArrays(sizes, np.zeros(p, np.int32), np.zeros((n + p, 3), np.float32), np.zeros(n + p, np.float32), attrs)


def path_chunks(sel: Selection, max_points: int) -> list[tuple[int, int]]:
    """Path ranges ``[p0, p1)`` of at most ``max_points`` points; a path longer than that is a range of its own."""
    p = sel.n_paths
    if p == 0:
        return []
    ends = np.append(sel.path_first[1:], sel.n_segments)
    pts = np.cumsum(ends - sel.path_first + 1)           # points up to and including path q
    out, p0, base = [], 0, 0
    while p0 < p:
        p1 = int(np.searchsorted(pts, base + max_points, side="right"))
        p1 = min(max(p1, p0 + 1), p)
        out.append((p0, p1))
        base, p0 = int(pts[p1 - 1]), p1
    return out


def fill_curves(out: CurveArrays, moves: Mapping[str, np.ndarray], sel: Selection, p0: int, p1: int,
                types: MoveTypes, scale: float, lut: np.ndarray) -> None:
    """Fill the points and curves of paths ``p0..p1`` of ``out``. ``scale`` is Blender units per millimetre."""
    first = sel.path_first
    a = int(first[p0])
    b = int(first[p1]) if p1 < sel.n_paths else sel.n_segments
    counts = np.diff(np.append(first[p0:p1], b))
    n_pts = b - a + (p1 - p0)
    o = a + p0                                                   # this range's first point
    pt_counts = counts + 1
    before = np.cumsum(pt_counts) - pt_counts
    rel = np.arange(n_pts) - np.repeat(before, pt_counts)        # point number within its path
    move = np.repeat(sel.idx[first[p0:p1]] - 1, pt_counts) + rel  # the move whose position is the point
    src = move + (rel == 0)                                      # the move whose values it takes
    values = _values(moves, src, types)
    width, height = _size_mm(values)
    pos = moves["position"][move].astype(np.float64)
    pos[:, 2] -= height / 2.0
    s = slice(o, o + n_pts)
    out.position[s] = pos * scale
    out.radius[s] = width * (scale / 2.0)
    for name in ATTRIBUTES:
        out.attributes[name][s] = values[name]
    out.material_index[p0:p1] = lut[values["feature_type"][before]]


# ----------------------------------------------------------------------------- prisms

def ring(sides: int) -> tuple[np.ndarray, np.ndarray]:
    """Unit cross-section of ``sides`` vertices: coordinates along the side vector and the up vector, scaled so the
    section spans exactly [-1, 1] in both (a flat-topped hexagon for 6, a box for 4)."""
    if not MIN_SIDES <= sides <= MAX_SIDES:
        raise ValueError(f"sides must be {MIN_SIDES}..{MAX_SIDES}")
    theta = 2 * np.pi * np.arange(sides) / sides + (np.pi / 4 if sides == 4 else 0.0)
    c, s = np.cos(theta), np.sin(theta)
    return c / np.abs(c).max(), s / np.abs(s).max()


@dataclass
class PrismArrays:
    """One mesh part: ``n`` segments (all quads first, then the caps), arrays ready for Blender's ``foreach_set``."""
    sides: int
    n: int
    cap_start: np.ndarray               # part-local segments whose start gets a cap
    cap_end: np.ndarray                 # part-local segments whose end gets a cap
    co: np.ndarray                      # float32 (V, 3)
    corner_vert: np.ndarray             # int32 (L,)
    corner_edge: np.ndarray             # int32 (L,)
    edge_verts: np.ndarray              # int32 (E, 2)
    loop_start: np.ndarray              # int32 (F,)
    seg: dict[str, np.ndarray]          # per-segment attribute values, (n,)
    material_index: np.ndarray          # int32 (n,)

    @property
    def n_vertices(self) -> int:
        return len(self.co)

    @property
    def n_faces(self) -> int:
        return len(self.loop_start)

    @property
    def n_caps(self) -> int:
        return len(self.cap_start) + len(self.cap_end)


def alloc_prisms(sel: Selection, a: int, b: int, sides: int) -> PrismArrays:
    """Arrays for the segments ``a..b`` of ``sel`` (one mesh part)."""
    n = b - a
    cs = np.flatnonzero(sel.start[a:b])
    nxt = np.append(sel.start[a + 1:b], True if b == sel.n_segments else bool(sel.start[b]))
    ce = np.flatnonzero(nxt)
    nq, caps = n * sides, len(cs) + len(ce)
    return PrismArrays(
        sides, n, cs, ce,
        co=np.zeros((n * 2 * sides, 3), np.float32),
        corner_vert=np.zeros(4 * nq + sides * caps, np.int32),
        corner_edge=np.zeros(4 * nq + sides * caps, np.int32),
        edge_verts=np.zeros((n * 3 * sides, 2), np.int32),
        loop_start=np.concatenate((4 * np.arange(nq, dtype=np.int32),
                                   4 * nq + sides * np.arange(caps, dtype=np.int32))),
        seg={name: np.zeros(n, np.int32 if name in INT_ATTRIBUTES else np.float32) for name in ATTRIBUTES},
        material_index=np.zeros(n, np.int32))


def _unit(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    length = np.linalg.norm(v, axis=1)
    safe = length > 1e-9
    u = np.where(safe[:, None], v / np.where(safe, length, 1.0)[:, None], (1.0, 0.0, 0.0))
    return u, length


def _turn_extension(u: np.ndarray, start: np.ndarray, half_w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How far to lengthen each prism at its start and end so the outside of a corner is covered: ``half width *
    tan(turn / 2)``, at most half a width. Zero at the ends of a path (those get caps)."""
    m = len(u)
    e_start, e_end = np.zeros(m), np.zeros(m)
    if m > 1:
        cos = np.clip(np.einsum("ij,ij->i", u[1:], u[:-1]), -1.0, 1.0)
        tan_half = np.sqrt((1.0 - cos) / np.maximum(1.0 + cos, 1e-9))
        joined = ~start[1:]
        e_start[1:] = np.where(joined, np.minimum(half_w[1:] * tan_half, half_w[1:]), 0.0)
        e_end[:-1] = np.where(joined, np.minimum(half_w[:-1] * tan_half, half_w[:-1]), 0.0)
    return e_start, e_end


def _templates(sides: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-quad loop vertices and edges, relative to a segment's first vertex and first edge. Quad ``j`` runs
    A_j, B_j, B_(j+1), A_(j+1) (outward normals); vertex ``A_j`` is ``j``, ``B_j`` is ``sides + j``; edges are the A ring
    (``0..s-1``), the B ring (``s..2s-1``) and the lengthwise edges (``2s..3s-1``)."""
    j = np.arange(sides)
    j1 = (j + 1) % sides
    verts = np.stack((j, sides + j, sides + j1, j1), axis=1)
    edges = np.stack((2 * sides + j, sides + j, 2 * sides + j1, j), axis=1)
    return verts.astype(np.int32), edges.astype(np.int32)


def fill_prisms(out: PrismArrays, moves: Mapping[str, np.ndarray], sel: Selection, a: int, b: int, a0: int,
                types: MoveTypes, scale: float, lut: np.ndarray) -> None:
    """Fill the vertices, loops, edges and attribute values of segments ``a..b`` of ``sel`` into ``out``, which was
    allocated for the part starting at segment ``a0``. The caps are written by :func:`fill_caps`."""
    sides = out.sides
    lo, hi = max(a - 1, 0), min(b + 1, sel.n_segments)           # neighbours give the turn at the range's ends
    idx = sel.idx[lo:hi]
    pos = moves["position"]
    p_a, p_b = pos[idx - 1].astype(np.float64), pos[idx].astype(np.float64)
    u, _ = _unit(p_b - p_a)
    values = _values(moves, idx, types)
    width, height = _size_mm(values)
    e_start, e_end = _turn_extension(u, sel.start[lo:hi], width / 2.0)
    p_a = p_a - u * e_start[:, None]
    p_b = p_b + u * e_end[:, None]
    p_a[:, 2] -= height / 2.0
    p_b[:, 2] -= height / 2.0
    side = np.cross(u, (0.0, 0.0, 1.0))
    norm = np.linalg.norm(side, axis=1)
    side = np.where((norm > 1e-9)[:, None], side / np.where(norm > 1e-9, norm, 1.0)[:, None], (1.0, 0.0, 0.0))
    up = np.cross(side, u)
    rs, rn = ring(sides)
    off = ((rs[None, :, None] * (width / 2.0)[:, None, None]) * side[:, None, :]
           + (rn[None, :, None] * (height / 2.0)[:, None, None]) * up[:, None, :])        # (m, sides, 3)
    k0, k1 = a - lo, b - lo
    m = k1 - k0
    verts = np.concatenate((p_a[k0:k1, None, :] + off[k0:k1], p_b[k0:k1, None, :] + off[k0:k1]), axis=1)
    o = a - a0
    out.co[o * 2 * sides:(o + m) * 2 * sides] = (verts * scale).reshape(-1, 3)
    tv, te = _templates(sides)
    seg = o + np.arange(m, dtype=np.int32)
    s = slice(o * 4 * sides, (o + m) * 4 * sides)
    out.corner_vert[s] = (tv[None] + (2 * sides * seg)[:, None, None]).reshape(-1)
    out.corner_edge[s] = (te[None] + (3 * sides * seg)[:, None, None]).reshape(-1)
    j = np.arange(sides, dtype=np.int32)
    base_v = (2 * sides * seg)[:, None]
    ev = out.edge_verts[o * 3 * sides:(o + m) * 3 * sides].reshape(m, 3 * sides, 2)
    ev[:, :sides, 0], ev[:, :sides, 1] = base_v + j, base_v + (j + 1) % sides                  # ring A
    ev[:, sides:2 * sides, 0], ev[:, sides:2 * sides, 1] = base_v + sides + j, base_v + sides + (j + 1) % sides
    ev[:, 2 * sides:, 0], ev[:, 2 * sides:, 1] = base_v + j, base_v + sides + j                # lengthwise
    for name in ATTRIBUTES:
        out.seg[name][o:o + m] = values[name][k0:k1]
    out.material_index[o:o + m] = lut[values["feature_type"][k0:k1]]


def fill_caps(out: PrismArrays) -> None:
    """Write the cap faces (a polygon of ``sides`` loops at the start of each path, one at its end) after the quads."""
    sides = out.sides
    nq = out.n * sides
    j = np.arange(sides, dtype=np.int32)
    base = 4 * nq
    # start cap: ring A in order (normal along -u); end cap: ring B reversed (normal along +u)
    for caps, first_vert, edge_first, step in ((out.cap_start, 0, 0, 1), (out.cap_end, sides, sides, -1)):
        if not len(caps):
            continue
        seg = caps.astype(np.int32)
        if step == 1:
            v = j
            e = j
        else:
            v = (sides - 1 - j)
            e = (sides - 2 - j) % sides
        verts = 2 * sides * seg[:, None] + first_vert + v[None, :]
        edges = 3 * sides * seg[:, None] + edge_first + e[None, :]
        out.corner_vert[base:base + verts.size] = verts.reshape(-1)
        out.corner_edge[base:base + edges.size] = edges.reshape(-1)
        base += verts.size


def face_attributes(out: PrismArrays) -> dict[str, np.ndarray]:
    """Per-face attribute arrays (:data:`ATTRIBUTES` and ``material_index``): a quad takes its segment's value, a cap its
    segment's."""
    seg_of_face = np.concatenate((np.repeat(np.arange(out.n, dtype=np.int64), out.sides), out.cap_start, out.cap_end))
    result = {name: values[seg_of_face] for name, values in out.seg.items()}
    result["material_index"] = out.material_index[seg_of_face]
    return result
