# SPDX-License-Identifier: GPL-3.0-or-later
"""Writes a large synthetic G-code file (plain Orca tags) to drive the preview benchmarks.

``python make_large.py OUT.gcode 10000000`` writes roughly 10M extrusion/travel moves: a round
part, 50k moves per layer (outer wall, inner wall, then an Archimedean spiral standing in for
infill, with a travel jump every few thousand moves), one retract per layer, a fan ramp and a
temperature step. Deterministic and written from scratch (CC0 1.0). About 40 bytes per move.
"""
from __future__ import annotations

import math
import sys

MOVES_PER_LAYER = 50_000
RADIUS = 50.0
LAYER_H = 0.2
AREA = math.pi * (1.75 / 2) ** 2


def _e(length: float, width: float) -> float:
    return width * LAYER_H * length / AREA


def layer_lines(layer: int, n: int = MOVES_PER_LAYER):
    """Yield the G-code lines of one layer holding about ``n`` motion commands."""
    z = (layer + 1) * LAYER_H
    rot = layer * 0.7
    n_wall = max(8, n * 16 // 100)
    n_fill = max(8, n - 2 * n_wall - 4)
    yield ";LAYER_CHANGE"
    yield f";Z:{z:.3f}"
    yield f";HEIGHT:{LAYER_H}"
    yield f"M106 S{min(255, 20 + layer * 6)}"
    yield f"M104 S{200 + (layer // 40) * 5}"
    yield f"G1 Z{z:.3f} F600"
    yield f"G1 X{RADIUS * math.cos(rot):.3f} Y{RADIUS * math.sin(rot):.3f} F12000"
    yield "G1 E0.8 F2400"
    for role, width, speed, radius, count in (("Outer wall", 0.45, 1800, RADIUS, n_wall),
                                              ("Inner wall", 0.45, 3600, RADIUS - 0.5, n_wall)):
        yield f";TYPE:{role}"
        yield f";WIDTH:{width}"
        px, py = radius * math.cos(rot), radius * math.sin(rot)
        if role == "Inner wall":
            yield f"G1 X{px:.3f} Y{py:.3f} F12000"
        for i in range(1, count + 1):
            a = rot + 2 * math.pi * i / count
            qx, qy = radius * math.cos(a), radius * math.sin(a)
            yield f"G1 X{qx:.3f} Y{qy:.3f} E{_e(math.hypot(qx - px, qy - py), width):.5f}" + (
                f" F{speed}" if i == 1 else "")
            px, py = qx, qy
    top = layer % 20 >= 17
    yield ";TYPE:Top surface" if top else ";TYPE:Sparse infill"
    yield "G1 X0 Y0 F12000"
    px = py = 0.0
    rmax = RADIUS - 1.5
    for k in range(1, n_fill + 1):
        r = rmax * math.sqrt(k / n_fill)
        th = rot + 2 * math.pi * r / 0.45
        qx, qy = r * math.cos(th), r * math.sin(th)
        if k % 6000 == 0:                       # a travel jump: no extrusion
            yield f"G0 X{qx:.3f} Y{qy:.3f} F12000"
        else:
            yield f"G1 X{qx:.3f} Y{qy:.3f} E{_e(math.hypot(qx - px, qy - py), 0.45):.5f}"
        px, py = qx, qy
    yield "G1 E-0.8 F2400"


def write(path: str, n_moves: int) -> int:
    """Write about ``n_moves`` moves; returns the number of layers."""
    layers = max(1, n_moves // MOVES_PER_LAYER)
    with open(path, "w", newline="\n") as f:
        f.write("; CC0 1.0 Universal: large synthetic sample, make_large.py\n"
                "; filament_diameter = 1.75\n; filament_density = 1.24\nM83\nG90\n")
        for layer in range(layers):
            f.write("\n".join(layer_lines(layer)) + "\n")
    return layers


if __name__ == "__main__":
    out, count = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000
    print(f"{write(out, count)} layers -> {out}")
