# SPDX-License-Identifier: GPL-3.0-or-later
"""Structure-of-arrays move table shared by the synthetic slicer and ``from_gcode`` (04 section 5)."""
from __future__ import annotations

import math

import numpy as np

from .api import MOVE_TYPES, ROLES

_DTYPES = {
    "position": np.float32, "type": np.uint8, "role": np.uint8, "filament": np.uint8,
    "nozzle": np.uint8, "color_id": np.uint8, "width": np.float32, "height": np.float32,
    "mm3_per_mm": np.float32, "feedrate": np.float32, "actual_feedrate": np.float32,
    "fan": np.float32, "temperature": np.float32, "pressure_advance": np.float32,
    "acceleration": np.float32, "jerk": np.float32, "time": np.float32,
    "layer_id": np.uint32, "print_z": np.float32, "object_id": np.int32, "gcode_line": np.uint32,
}
_ROLE_NAMES = {v: k for k, v in ROLES.items()}
_TYPE_NAMES = {v: k for k, v in MOVE_TYPES.items()}
FILAMENT_NONE = 255
AREA_175 = math.pi * (1.75 / 2) ** 2


class MoveTable:
    """Collects moves; ``arrays()`` builds the 04 section 5.2 dict, ``layers()`` section 5.3."""

    def __init__(self) -> None:
        self._rows: list[tuple] = []

    def __len__(self) -> int:
        return len(self._rows)

    def add(self, kind: str, x: float, y: float, z: float, *, role: str = "None",
            filament: int = 0, object_id: int = -1, width: float = 0.0, height: float = 0.0,
            mm3_per_mm: float = 0.0, feedrate: float = 0.0, fan: float = 0.0,
            temperature: float = 0.0, acceleration: float = 0.0, jerk: float = 0.0,
            pressure_advance: float = 0.0, duration: float = 0.0, silent_factor: float = 1.25,
            layer_id: int = 0, print_z: float | None = None, gcode_line: int = 1,
            nozzle: int = 0, color_id: int = 0) -> None:
        self._rows.append((
            MOVE_TYPES[kind], ROLES[role], filament, nozzle, color_id, x, y, z, width, height,
            mm3_per_mm, feedrate, fan, temperature, pressure_advance, acceleration, jerk,
            duration, duration * silent_factor, layer_id, z if print_z is None else print_z,
            object_id, gcode_line))

    def arrays(self) -> dict[str, np.ndarray]:
        rows = self._rows
        col = lambda i: [r[i] for r in rows]  # noqa: E731
        out = {
            "type": col(0), "role": col(1), "filament": col(2), "nozzle": col(3),
            "color_id": col(4), "width": col(8), "height": col(9), "mm3_per_mm": col(10),
            "feedrate": col(11), "actual_feedrate": col(11), "fan": col(12),
            "temperature": col(13), "pressure_advance": col(14), "acceleration": col(15),
            "jerk": col(16), "layer_id": col(19), "print_z": col(20), "object_id": col(21),
            "gcode_line": col(22),
        }
        result = {k: np.asarray(v, dtype=_DTYPES[k]) for k, v in out.items()}
        result["position"] = np.asarray([(r[5], r[6], r[7]) for r in rows],
                                        dtype=np.float32).reshape(-1, 3)
        result["time"] = np.asarray([(r[17], r[18]) for r in rows], dtype=np.float32).reshape(-1, 2)
        return result


def renumber_layers(layer_id: np.ndarray) -> np.ndarray:
    """Make ids contiguous 0..L-1 in order of appearance (the 5.3 guarantee)."""
    if len(layer_id) == 0:
        return layer_id.astype(np.uint32)
    change = np.concatenate(([0], (np.diff(layer_id.astype(np.int64)) != 0).astype(np.int64)))
    return np.cumsum(change).astype(np.uint32)


def build_layers(moves: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Renumber ``moves['layer_id']`` in place if needed and return the ``layers`` dict."""
    ids = renumber_layers(moves["layer_id"])
    moves["layer_id"] = ids
    k = len(ids)
    if k == 0:
        return {"z": np.zeros(0, np.float32), "first": np.zeros(0, np.uint32),
                "last": np.zeros(0, np.uint32)}
    starts = np.flatnonzero(np.concatenate(([True], ids[1:] != ids[:-1])))
    ends = np.concatenate((starts[1:] - 1, [k - 1]))
    z = np.asarray([moves["print_z"][s:e + 1].max() for s, e in zip(starts, ends)], np.float32)
    return {"z": z, "first": starts.astype(np.uint32), "last": ends.astype(np.uint32)}


def build_stats(moves: dict[str, np.ndarray], *, n_filaments: int = 1,
                diameters: list[float] | None = None, densities: list[float] | None = None,
                costs: list[float] | None = None, layer_count: int = 0,
                prepare_time_s: float = 0.0) -> dict:
    """Statistics per 04 section 5.4, computed from ``moves`` alone."""
    diameters = diameters or [1.75] * n_filaments
    densities = densities or [1.24] * n_filaments
    costs = costs or [0.0] * n_filaments
    t = moves["time"].astype(np.float64)
    types, roles = moves["type"], moves["role"]
    extrude = types == MOVE_TYPES["Extrude"]

    by_role: dict[str, list[float]] = {}
    for rid in np.unique(roles[extrude]):
        sel = extrude & (roles == rid)
        by_role[_ROLE_NAMES[int(rid)]] = [float(t[sel, 0].sum()), float(t[sel, 1].sum())]
    by_type: dict[str, list[float]] = {}
    for tid in np.unique(types[~extrude]):
        sel = ~extrude & (types == tid)
        if t[sel].sum() > 0:
            by_type[_TYPE_NAMES[int(tid)]] = [float(t[sel, 0].sum()), float(t[sel, 1].sum())]

    pos = moves["position"].astype(np.float64)
    seg = np.zeros(len(pos))
    if len(pos) > 1:
        seg[1:] = np.linalg.norm(np.diff(pos, axis=0), axis=1)
    volume = seg * moves["mm3_per_mm"].astype(np.float64) * extrude
    per_fil, per_role = [], {}
    for f in range(n_filaments):
        v = float(volume[moves["filament"] == f].sum())
        area = math.pi * (diameters[f] / 2) ** 2
        grams = v / 1000.0 * densities[f]
        per_fil.append({"mm": v / area, "cm3": v / 1000.0, "g": grams, "cost": grams / 1000.0 * costs[f]})
    for name, _ in by_role.items():
        sel = extrude & (roles == ROLES[name])
        v = float(volume[sel].sum())
        per_role[name] = {"m": v / AREA_175 / 1000.0, "g": v / 1000.0 * densities[0]}

    total = [float(t[:, 0].sum()), float(t[:, 1].sum())]
    return {
        "time_s": {"normal": total[0], "silent": total[1]},
        "prepare_time_s": prepare_time_s,
        "time_by_role_s": by_role,
        "time_by_move_type_s": by_type,
        "filament_per_extruder": per_fil,
        "used_filament_per_role": per_role,
        "flush_per_filament_g": [0.0] * n_filaments,
        "total_filament_changes": 0,
        "total_tool_changes": 0,
        "layer_count": layer_count,
        "total_travel_mm": float(seg[types == MOVE_TYPES["Travel"]].sum()),
        "display": {
            "Total filament length": f"{sum(f['mm'] for f in per_fil) / 1000.0:.2f} m",
            "Total filament weight": f"{sum(f['g'] for f in per_fil):.2f} g",
            "Estimated time": f"{total[0]:.0f} s",
        },
    }
