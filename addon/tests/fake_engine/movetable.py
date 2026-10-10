# SPDX-License-Identifier: GPL-3.0-or-later
"""Structure-of-arrays move table shared by the synthetic slicer and ``from_gcode`` (04 section 5)."""
from __future__ import annotations

import math
from array import array

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


# Column order = ``_DTYPES`` order minus the two stacked columns: position (x, y, z) and time
# (normal, silent) are kept as separate scalar columns and stacked once in ``arrays()``.
_CODES = {np.float32: "f", np.uint8: "B", np.uint32: "I", np.int32: "i"}
_SCALARS = ("type", "role", "filament", "nozzle", "color_id", "x", "y", "z", "width", "height",
            "mm3_per_mm", "feedrate", "fan", "temperature", "pressure_advance", "acceleration",
            "jerk", "t_normal", "t_silent", "layer_id", "print_z", "object_id", "gcode_line")
_SCALAR_DTYPE = {**_DTYPES, "x": np.float32, "y": np.float32, "z": np.float32,
                 "t_normal": np.float32, "t_silent": np.float32}


class MoveTable:
    """Collects moves; ``arrays()`` builds the 04 section 5.2 dict, ``layers()`` section 5.3.

    Storage is one compact ``array.array`` column per field (4 bytes or less per value, no
    per-row Python objects), so a 10M-move table costs about 0.8 GB while it is built, not the
    ~9 GB of a list of tuples. ``arrays()`` wraps the columns without copying (``np.frombuffer``)
    and stacks position and time; after it is called the table is frozen and ``add`` raises.
    """

    def __init__(self) -> None:
        self._cols = {name: array(_CODES[_SCALAR_DTYPE[name]]) for name in _SCALARS}
        self._n = 0

    def __len__(self) -> int:
        return self._n

    def add(self, kind: str, x: float, y: float, z: float, *, role: str = "None",
            filament: int = 0, object_id: int = -1, width: float = 0.0, height: float = 0.0,
            mm3_per_mm: float = 0.0, feedrate: float = 0.0, fan: float = 0.0,
            temperature: float = 0.0, acceleration: float = 0.0, jerk: float = 0.0,
            pressure_advance: float = 0.0, duration: float = 0.0, silent_factor: float = 1.25,
            layer_id: int = 0, print_z: float | None = None, gcode_line: int = 1,
            nozzle: int = 0, color_id: int = 0) -> None:
        c = self._cols
        c["type"].append(MOVE_TYPES[kind])
        c["role"].append(ROLES[role])
        c["filament"].append(filament)
        c["nozzle"].append(nozzle)
        c["color_id"].append(color_id)
        c["x"].append(x)
        c["y"].append(y)
        c["z"].append(z)
        c["width"].append(width)
        c["height"].append(height)
        c["mm3_per_mm"].append(mm3_per_mm)
        c["feedrate"].append(feedrate)
        c["fan"].append(fan)
        c["temperature"].append(temperature)
        c["pressure_advance"].append(pressure_advance)
        c["acceleration"].append(acceleration)
        c["jerk"].append(jerk)
        c["t_normal"].append(duration)
        c["t_silent"].append(duration * silent_factor)
        c["layer_id"].append(layer_id)
        c["print_z"].append(z if print_z is None else print_z)
        c["object_id"].append(object_id)
        c["gcode_line"].append(gcode_line)
        self._n += 1

    def arrays(self) -> dict[str, np.ndarray]:
        col = {name: np.frombuffer(a, dtype=_SCALAR_DTYPE[name]) if len(a) else
               np.zeros(0, _SCALAR_DTYPE[name]) for name, a in self._cols.items()}
        out = {k: col[k] for k in ("type", "role", "filament", "nozzle", "color_id", "width",
                                   "height", "mm3_per_mm", "feedrate", "fan", "temperature",
                                   "pressure_advance", "acceleration", "jerk", "layer_id",
                                   "print_z", "object_id", "gcode_line")}
        out["actual_feedrate"] = col["feedrate"].copy()
        out["position"] = np.stack((col["x"], col["y"], col["z"]), axis=1).astype(np.float32)
        out["time"] = np.stack((col["t_normal"], col["t_silent"]), axis=1).astype(np.float32)
        return {k: out[k] for k in _DTYPES}


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
