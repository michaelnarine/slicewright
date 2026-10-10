# SPDX-License-Identifier: GPL-3.0-or-later
"""Legend rows and the summary text from ``SliceResult.stats`` (03 section 7.7), pure Python."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from . import preview_palette as pp


@dataclass(frozen=True)
class LegendRow:
    role_id: int
    role: str            # engine role name
    label: str
    color: int           # 0xRRGGBB
    time_s: float
    percent: float       # of the summed role times
    filament_m: float
    filament_g: float


def format_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {sec:02d}s" if m else f"{sec}s"


def legend_rows(stats: Mapping, role_ids: Mapping[str, int], silent: bool = False) -> list[LegendRow]:
    """One row per role that has time, in role-id order; ``percent`` sums to 100."""
    by_role = stats.get("time_by_role_s", {})
    filament = stats.get("used_filament_per_role", {})
    col = 1 if silent else 0
    total = sum(v[col] for v in by_role.values()) or 1.0
    rows = []
    for name, times in by_role.items():
        label, color = pp.ROLE_STYLE.get(name, (name, 0x808080))
        used = filament.get(name, {})
        rows.append(LegendRow(role_ids.get(name, 31), name, label, color, float(times[col]),
                              100.0 * times[col] / total, float(used.get("m", 0.0)), float(used.get("g", 0.0))))
    return sorted(rows, key=lambda r: r.role_id)


def summary_lines(stats: Mapping) -> list[str]:
    """Human-readable summary: times, filament per extruder, layers, changes, travel."""
    t = stats["time_s"]
    lines = [f"Time: {format_duration(t['normal'])} (silent {format_duration(t['silent'])})"]
    for i, f in enumerate(stats.get("filament_per_extruder", [])):
        lines.append(f"Filament {i + 1}: {f['mm'] / 1000:.2f} m, {f['g']:.1f} g"
                     + (f", {f['cost']:.2f}" if f.get("cost") else ""))
    lines.append(f"Layers: {stats.get('layer_count', 0)}")
    changes, tools = stats.get("total_filament_changes", 0), stats.get("total_tool_changes", 0)
    if changes or tools:
        lines.append(f"Filament changes: {changes}, tool changes: {tools}")
    flush = sum(stats.get("flush_per_filament_g", []))
    if flush:
        lines.append(f"Flushed: {flush:.1f} g")
    lines.append(f"Travel: {stats.get('total_travel_mm', 0.0) / 1000:.2f} m")
    return lines


def accounted_time(stats: Mapping) -> float:
    """Σ per-role + Σ per-move-type time (normal mode): equals ``time_s`` within 1 % (04 5.4)."""
    return (sum(v[0] for v in stats.get("time_by_role_s", {}).values())
            + sum(v[0] for v in stats.get("time_by_move_type_s", {}).values()))


def gradient_pixels(width: int = 64, height: int = 1) -> list[float]:
    """RGBA float pixels of the range ramp, row-major, for a legend chip."""
    out: list[float] = []
    for _ in range(height):
        for x in range(width):
            out.extend((*pp.range_color(x / max(width - 1, 1)), 1.0))
    return out


def chip_pixels(color: int, size: int = 16) -> list[float]:
    r, g, b = pp.rgb(color)
    return [r, g, b, 1.0] * (size * size)
