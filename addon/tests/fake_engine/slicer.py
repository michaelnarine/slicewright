# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic slicing, validation and arranging for the fake (03 section 9.2).

Not a slicer: each object becomes bounding-box layers with rectangular walls,
zigzag infill, travel, retracts and plausible times. What matters is that the
result has the exact shape, dtypes and guarantees of 04 section 5.
"""
from __future__ import annotations

import math
import os
import uuid
from dataclasses import dataclass, field

import numpy as np

from .api import ROLES
from .config import split_vector
from .errors import ArrangeError, issue
from .movetable import MoveTable, build_layers, build_stats
from .result import SliceResult
from .tags import PLAIN, ROLE_TO_TEXT

TRAVEL_SPEED = 150.0      # mm/s
RETRACT_LEN = 0.8         # mm
RETRACT_SPEED = 30.0      # mm/s
MIN_RETRACT_TRAVEL = 2.0  # mm
MAX_PAINT_STATE = 16      # 04 section 2.4: TriangleSelector's Extruder16


@dataclass
class ObjectData:
    name: str
    vertices: np.ndarray
    triangles: np.ndarray
    extruder: int = 0
    overrides: dict = field(default_factory=dict)
    face_extruder: np.ndarray | None = None
    face_support: np.ndarray | None = None
    face_seam: np.ndarray | None = None
    moved_to_bed: bool = False

    @property
    def bbox(self) -> tuple[float, float, float, float, float, float]:
        lo, hi = self.vertices.min(axis=0), self.vertices.max(axis=0)
        return (float(lo[0]), float(hi[0]), float(lo[1]), float(hi[1]), float(lo[2]), float(hi[2]))


def _floats(config: dict, key: str) -> list[float]:
    return [float(x) for x in split_vector("floats", config[key])]


def filament_count(config: dict) -> int:
    return max(1, len(split_vector("strings", config["filament_colour"])))


def bed_bbox(config: dict) -> tuple[float, float, float, float]:
    pts = [tuple(float(c) for c in p.split("x")) for p in split_vector("points", config["printable_area"])]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# validation (04 section 4.2)
# ---------------------------------------------------------------------------

def _open_edge_count(tri: np.ndarray, n_vertices: int) -> int:
    e = np.sort(np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
    key = e[:, 0].astype(np.int64) * n_vertices + e[:, 1]
    _, counts = np.unique(key, return_counts=True)
    return int((counts != 2).sum())


def validate_objects(objects: list[ObjectData], config: dict) -> list[dict]:
    nfil = filament_count(config)
    issues = []
    for o in objects:
        if o.face_extruder is not None and len(o.face_extruder) and int(o.face_extruder.max()) > min(MAX_PAINT_STATE, nfil):
            issues.append(issue(
                "error", "paint_out_of_range",
                f"{o.name}: painted filament {int(o.face_extruder.max())} but only {min(MAX_PAINT_STATE, nfil)} are usable",
                "filament_colour", o.name))
        if _open_edge_count(o.triangles, len(o.vertices)):
            issues.append(issue("warning", "mesh_open_edges", f"{o.name} has open edges",
                                None, o.name))
        if o.moved_to_bed:
            issues.append(issue("info", "moved_to_bed", f"{o.name} was moved onto the bed",
                                None, o.name))
    issues.sort(key=lambda i: ("error", "warning", "info").index(i["level"]))
    return issues


# ---------------------------------------------------------------------------
# arrange (04 section 4.5)
# ---------------------------------------------------------------------------

def arrange(objects: list[ObjectData], config: dict, spacing: float | None) -> list[dict]:
    gap = 6.0 if spacing is None else float(spacing)
    bx0, by0, bx1, by1 = bed_bbox(config)
    placed, unfit = [], []
    x = y = row_h = 0.0
    for i, o in enumerate(objects):
        x0, x1, y0, y1, _, _ = o.bbox
        w, h = x1 - x0, y1 - y0
        if x > 0 and x + w > bx1 - bx0:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        if w > bx1 - bx0 or y + h > by1 - by0:
            unfit.append(o.name)
            continue
        placed.append((i, x, y, w, h))
        x += w + gap
        row_h = max(row_h, h)
    if unfit:
        raise ArrangeError(f"{len(unfit)} object(s) do not fit on the bed", unfit)
    if not placed:
        return []
    used_w = max(px + w for _, px, _, w, _ in placed)
    used_h = max(py + h for _, _, py, _, h in placed)
    off_x = bx0 + ((bx1 - bx0) - used_w) / 2.0
    off_y = by0 + ((by1 - by0) - used_h) / 2.0
    out = []
    for i, px, py, _, _ in placed:
        o = objects[i]
        x0, _, y0, _, _, _ = o.bbox
        t = np.eye(4, dtype=np.float64)
        t[0, 3], t[1, 3] = off_x + px - x0, off_y + py - y0
        out.append({"index": i, "name": o.name, "transform": t,
                    "translation": (float(t[0, 3]), float(t[1, 3]), 0.0), "rotation_z": 0.0})
    return out


# ---------------------------------------------------------------------------
# synthetic slicing
# ---------------------------------------------------------------------------

class _Emitter:
    """Writes G-code text and the matching move table in lock step."""

    def __init__(self, config: dict, nfil: int) -> None:
        self.cfg = config
        self.lines: list[str] = []
        self.mt = MoveTable()
        self.temps = [float(x) for x in split_vector("ints", config["nozzle_temperature"])]
        self.diam = _floats(config, "filament_diameter")
        self.x = self.y = self.z = 0.0
        self.e = 0.0
        self.retracted = False
        self.layer = 0
        self.target_z = 0.0
        self.fan = 0.0
        self.height = 0.2
        self.fil = 0
        self.last_role = None
        self.last_width = None
        self.nfil = nfil

    def line(self, text: str) -> int:
        self.lines.append(text)
        return len(self.lines)

    def _temp(self) -> float:
        return self.temps[min(self.fil, len(self.temps) - 1)]

    def start(self) -> None:
        self.mt.add("Noop", 0.0, 0.0, 0.0, gcode_line=1)

    def layer_change(self, layer: int, z: float, height: float, fan: float) -> None:
        self.layer, self.height, self.fan = layer, height, fan
        self.line(f";{PLAIN['layer']}")
        self.line(f";Z:{z:.3f}")
        self.line(f";{PLAIN['height']}{height:.3f}")
        self.line(f"M106 S{int(fan * 2.55)}")
        self.target_z = z
        self.last_role = None

    def _retract(self, sign: int) -> None:
        kind = "Retract" if sign < 0 else "Unretract"
        self.e += sign * RETRACT_LEN
        ln = self.line(f"G1 E{self.e:.5f} F{int(RETRACT_SPEED * 60)}")
        self.mt.add(kind, self.x, self.y, self.z, filament=self.fil, feedrate=RETRACT_SPEED,
                    duration=RETRACT_LEN / RETRACT_SPEED, layer_id=self.layer, gcode_line=ln,
                    temperature=self._temp(), fan=self.fan)
        self.retracted = sign < 0

    def travel(self, x: float, y: float) -> None:
        dist = math.hypot(x - self.x, y - self.y) + abs(self.target_z - self.z)
        if dist > MIN_RETRACT_TRAVEL and not self.retracted:
            self._retract(-1)
        z_part = f" Z{self.target_z:.3f}" if self.target_z != self.z else ""
        ln = self.line(f"G0 X{x:.3f} Y{y:.3f}{z_part} F{int(TRAVEL_SPEED * 60)}")
        self.x, self.y, self.z = x, y, self.target_z
        self.mt.add("Travel", x, y, self.z, filament=self.fil, feedrate=TRAVEL_SPEED,
                    duration=dist / TRAVEL_SPEED, layer_id=self.layer, gcode_line=ln,
                    temperature=self._temp(), fan=self.fan, print_z=self.target_z)

    def extrude(self, x: float, y: float, role: str, width: float, speed: float, obj: int) -> None:
        if self.retracted:
            self._retract(+1)
        if role != self.last_role:
            self.line(f";{PLAIN['role']}{ROLE_TO_TEXT[role]}")
            self.line(f";{PLAIN['width']}{width:.3f}")
            self.last_role = role
        dist = math.hypot(x - self.x, y - self.y)
        mm3_per_mm = width * self.height
        self.e += mm3_per_mm * dist / (math.pi * (self.diam[min(self.fil, len(self.diam) - 1)] / 2) ** 2)
        ln = self.line(f"G1 X{x:.3f} Y{y:.3f} E{self.e:.5f} F{int(speed * 60)}")
        self.x, self.y = x, y
        self.mt.add("Extrude", x, y, self.z, role=role, filament=self.fil, object_id=obj,
                    width=width, height=self.height, mm3_per_mm=mm3_per_mm, feedrate=speed,
                    fan=self.fan, temperature=self._temp(), acceleration=5000.0, jerk=9.0,
                    pressure_advance=0.02, duration=dist / speed, layer_id=self.layer,
                    gcode_line=ln, print_z=self.z)


def _rect(x0: float, x1: float, y0: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _loop(em: _Emitter, pts, role: str, width: float, speed: float, obj: int) -> None:
    em.travel(*pts[0])
    for p in pts[1:] + [pts[0]]:
        em.extrude(p[0], p[1], role, width, speed, obj)


def _infill(em: _Emitter, box, role: str, width: float, spacing: float, speed: float, obj: int,
            along_x: bool) -> None:
    x0, x1, y0, y1 = box
    if x1 - x0 < width or y1 - y0 < width:
        return
    lo, hi = (y0, y1) if along_x else (x0, x1)
    positions = []
    p = lo + width / 2
    while p <= hi - width / 2 + 1e-9:
        positions.append(p)
        p += spacing
    for i, c in enumerate(positions):
        a, b = (x0, x1) if i % 2 == 0 else (x1, x0)
        start, end = ((a, c), (b, c)) if along_x else ((c, a), (c, b))
        if i == 0:
            em.travel(*start)
        else:
            em.extrude(start[0], start[1], role, width, speed, obj)
        em.extrude(end[0], end[1], role, width, speed, obj)


def synthesize(objects: list[ObjectData], config: dict, thumbnails: list, out_dir: str) -> SliceResult:
    nfil = filament_count(config)
    lh = float(config["layer_height"])
    first = float(config["initial_layer_print_height"])
    nozzle = _floats(config, "nozzle_diameter")[0]
    width = round(nozzle * 1.05, 4)
    em = _Emitter(config, nfil)
    em.line("; synthetic G-code from the slicewright fake engine (CC0)")
    em.line("M82")
    em.line("G90")
    em.line("G28")
    em.start()

    boxes = [o.bbox for o in objects]
    ztop = max((b[5] for b in boxes), default=0.0)
    n_grid = 1 + max(0, math.ceil((ztop - first) / lh - 1e-6)) if objects else 0
    emitted = 0
    for k in range(n_grid):
        z = first + k * lh
        bottom = z - (first if k == 0 else lh)
        active = [i for i, b in enumerate(boxes) if b[4] < z - 1e-6 and b[5] > bottom + 1e-6]
        if not active:
            continue
        em.layer_change(emitted, z, first if k == 0 else lh, 0.0 if k < 2 else 100.0)
        em.target_z = z
        if k == 0:
            loops = int(float(config["skirt_loops"]))
            ux0 = min(boxes[i][0] for i in active) - 3.0
            ux1 = max(boxes[i][1] for i in active) + 3.0
            uy0 = min(boxes[i][2] for i in active) - 3.0
            uy1 = max(boxes[i][3] for i in active) + 3.0
            for j in range(loops):
                d = j * width
                _loop(em, _rect(ux0 - d, ux1 + d, uy0 - d, uy1 + d), "Skirt", width, 50.0, -1)
        for i in active:
            _object_layer(em, i, objects[i], boxes[i], config, k, n_grid, width)
        emitted += 1

    em.line("M104 S0")
    em.line("; end of synthetic G-code")

    moves = em.mt.arrays()
    layers = build_layers(moves)
    stats = build_stats(
        moves, n_filaments=nfil, diameters=em.diam[:nfil] + em.diam[-1:] * max(0, nfil - len(em.diam)),
        densities=(_floats(config, "filament_density") * nfil)[:nfil],
        costs=(_floats(config, "filament_cost") * nfil)[:nfil], layer_count=len(layers["z"]))

    text = "".join(line + "\n" for line in em.lines)
    path = os.path.join(out_dir, f"slice_{uuid.uuid4().hex[:12]}.gcode")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    ends = np.cumsum([len(line.encode("utf-8")) + 1 for line in em.lines]).astype(np.uint64)

    warnings = _slice_warnings(objects, boxes, config, thumbnails)
    return SliceResult(gcode_path=path, moves=moves, layers=layers, gcode_line_ends=ends,
                       stats=stats, warnings=warnings, objects=[o.name for o in objects],
                       wipe_tower=None, config=config, thumbnails=thumbnails)


def _object_layer(em: _Emitter, idx: int, o: ObjectData, box, config: dict, k: int, n_grid: int,
                  width: float) -> None:
    cfg = {**config, **o.overrides}
    em.fil = min(max(o.extruder, 1) - 1, em.nfil - 1)
    x0, x1, y0, y1, _, _ = box
    loops = int(float(cfg["wall_loops"]))
    drawn = 0
    for i in range(loops):
        inset = width / 2 + i * width
        if x1 - x0 <= 2 * inset or y1 - y0 <= 2 * inset:
            break
        role = "ExternalPerimeter" if i == 0 else "Perimeter"
        speed = float(cfg["outer_wall_speed"] if i == 0 else cfg["inner_wall_speed"])
        _loop(em, _rect(x0 + inset, x1 - inset, y0 + inset, y1 - inset), role, width, speed, idx)
        drawn += 1
    inner = drawn * width
    density = float(cfg["sparse_infill_density"].rstrip("%")) / 100.0
    last = k == n_grid - 1 or box[5] <= em.target_z + 1e-6
    solid = (k < int(float(cfg["bottom_shell_layers"]))
             or em.target_z > box[5] - int(float(cfg["top_shell_layers"])) * float(cfg["layer_height"]))
    if last:
        role, spacing, speed = "TopSolidInfill", width, float(cfg["inner_wall_speed"])
    elif k == 0:
        role, spacing, speed = "BottomSurface", width, float(cfg["inner_wall_speed"])
    elif solid:
        role, spacing, speed = "SolidInfill", width, float(cfg["inner_wall_speed"])
    elif density > 0:
        role, spacing, speed = "InternalInfill", width / density, float(cfg["sparse_infill_speed"])
    else:
        return
    assert role in ROLES
    _infill(em, (x0 + inner, x1 - inner, y0 + inner, y1 - inner), role, width, spacing, speed, idx,
            along_x=(k % 2 == 0))


def _slice_warnings(objects, boxes, config: dict, thumbnails: list) -> list[dict]:
    out = []
    bx0, by0, bx1, by1 = bed_bbox(config)
    height = float(config["printable_height"])
    for o, b in zip(objects, boxes):
        if b[0] < bx0 or b[1] > bx1 or b[2] < by0 or b[3] > by1:
            out.append(issue("warning", "out_of_printable_area",
                             f"{o.name} extends beyond the printable area", "printable_area", o.name))
        if b[5] > height:
            out.append(issue("warning", "out_of_printable_height",
                             f"{o.name} is taller than the printable height", "printable_height",
                             o.name))
    sizes = {(a.shape[1], a.shape[0]) for a in thumbnails}
    for spec in [s.strip() for s in config["thumbnails"].split(",") if s.strip()]:
        dims = spec.split("/")[0]
        try:
            w, h = (int(v) for v in dims.lower().split("x"))
        except ValueError:
            continue
        if (w, h) not in sizes:
            out.append(issue("warning", "thumbnail_missing", f"no {dims} thumbnail was supplied",
                             "thumbnails", None))
    return out
