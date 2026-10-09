# SPDX-License-Identifier: GPL-3.0-or-later
"""``from_gcode``: build a SliceResult from an existing G-code file (03 section 9.2).

Written from the dialect description in ``tags.py``: a small interpreter for G0/G1/G2/G3,
G90/G91, M82/M83, G92, T<n>, M104/M109 (temperature) and M106/M107 (fan), with the
role, width, height and layer comment tags of both dialects. It lets large real prints
drive the preview before the native engine exists.
"""
from __future__ import annotations

import io
import math
import re

import numpy as np

from .config import defaults
from .errors import issue
from .movetable import MoveTable, build_layers, build_stats
from .result import SliceResult
from .tags import BAMBU, PLAIN, TEXT_TO_ROLE

_WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")   # no exponents: "Y0E1" is Y0 then E1
_CMD = re.compile(r"\s*([GMTgmt]\d+(?:\.\d+)?)")
_META = re.compile(rb"^;[ \t]*([A-Za-z_][A-Za-z_0-9]*)[ \t]*=[ \t]*(.+?)[ \t\r]*$", re.M)
_LAYER_TAG = re.compile(rb"^(?:;" + re.escape(PLAIN["layer"].encode()) + rb"|;" + re.escape(BAMBU["layer"].encode()) + rb")", re.M)
_OBJ_START = re.compile(r"^ printing object (.+?)(?: id:\d+)?(?: copy \d+)?\s*$")
MAX_TOOL = 255      # filament indices are uint8 and 255 means "none" (04 section 5.2)
_ARC_STEP_RAD = math.radians(5.0)


def _tag_value(comment: str, tags: dict, which: str):
    """The text after tag ``which`` if ``comment`` (without the leading ';') starts with it."""
    tag = tags[which]
    return comment[len(tag):].strip() if comment.startswith(tag) else None


def _floats(text: str) -> list[float]:
    out = []
    for part in re.split(r"[,;]", text):
        try:
            out.append(float(part.strip().strip('"')))
        except ValueError:
            pass
    return out


def _arc_points(x0, y0, x1, y1, i, j, r, clockwise):
    """Intermediate points plus the end point of an arc, tessellated at 5 degrees.

    With I/J the centre is exact and start == end is a full circle. With R and start == end the
    arc is ambiguous: returns None and the caller skips the move with a warning."""
    if i is not None or j is not None:
        cx, cy = x0 + (i or 0.0), y0 + (j or 0.0)
    elif r is not None:
        dx, dy = x1 - x0, y1 - y0
        d = math.hypot(dx, dy)
        if d < 1e-9:
            return None     # "R" with start == end is ambiguous (any circle through the point)
        h = math.sqrt(max(r * r - d * d / 4.0, 0.0))
        sign = (1.0 if not clockwise else -1.0) * (1.0 if r > 0 else -1.0)
        cx, cy = x0 + dx / 2 - sign * h * dy / d, y0 + dy / 2 + sign * h * dx / d
    else:
        return [(x1, y1)]
    radius = math.hypot(x0 - cx, y0 - cy)
    a0, a1 = math.atan2(y0 - cy, x0 - cx), math.atan2(y1 - cy, x1 - cx)
    sweep = a1 - a0
    if clockwise:
        if sweep >= 0:
            sweep -= 2 * math.pi
    elif sweep <= 0:
        sweep += 2 * math.pi
    if abs(sweep) < 1e-9:                       # start == end: a full circle
        sweep = -2 * math.pi if clockwise else 2 * math.pi
    n = max(1, math.ceil(abs(sweep) / _ARC_STEP_RAD))
    pts = [(cx + radius * math.cos(a0 + sweep * k / n), cy + radius * math.sin(a0 + sweep * k / n))
           for k in range(1, n)]
    return pts + [(x1, y1)]


def from_gcode(path: str) -> SliceResult:
    with open(path, "rb") as f:
        data = f.read()
    # Line offsets and the header scan run in numpy/regex so a 10M-line file never becomes a
    # list of Python strings; the interpreter loop below streams the lines one at a time.
    ends = np.flatnonzero(np.frombuffer(data, np.uint8) == 10).astype(np.uint64) + 1
    if data and not data.endswith(b"\n"):
        ends = np.append(ends, np.uint64(len(data)))
    meta: dict[str, str] = {}
    for m in _META.finditer(data):
        meta[m.group(1).decode()] = m.group(2).decode("utf-8", errors="replace")
    has_layer_tags = _LAYER_TAG.search(data) is not None
    diameters = _floats(meta.get("filament_diameter", "")) or [1.75]
    densities = _floats(meta.get("filament_density", "")) or [1.24]
    costs = _floats(meta.get("filament_cost", "")) or [0.0]

    known_filaments = len(_floats(meta["filament_diameter"])) if "filament_diameter" in meta else 0
    warned_tools: set[str] = set()
    mt = MoveTable()
    warnings: list[dict] = []
    objects: list[str] = []
    x = y = z = e = 0.0
    feed = 0.0                       # mm/s
    abs_xyz = e_abs = True          # G90/G91, and M82/M83; E is absolute only if both say so
    tool, role, obj = 0, "None", -1
    width, height = 0.42, 0.2
    layer, temp, fan, accel = 0, 0.0, 0.0, 0.0
    layer_seen = 0
    layer_z = None                   # z of the last extrusion, for files without layer tags
    wiping = False
    seam_pending = False             # an outer-wall run starts: mark its first point (a Seam move)
    max_tool = 0

    mt.add("Noop", 0.0, 0.0, 0.0, gcode_line=1)

    def add_move(kind, nx, ny, nz, de, line_no, length=None, rel_extrusion=0.0):
        nonlocal layer, layer_z, seam_pending
        seg = math.dist((x, y, z), (nx, ny, nz)) if length is None else length
        dur = (seg if kind in ("Travel", "Extrude", "Wipe") else abs(de)) / feed if feed > 0 else 0.0
        area = math.pi * (diameters[min(tool, len(diameters) - 1)] / 2) ** 2
        mm3 = (rel_extrusion * area / seg) if (kind == "Extrude" and seg > 0) else 0.0
        if kind == "Extrude" and mm3 <= 0:
            mm3 = width * height
        if not has_layer_tags and kind == "Extrude":
            if layer_z is not None and nz > layer_z + 1e-9:
                layer += 1
            layer_z = nz
        if kind == "Extrude" and seam_pending:
            seam_pending = False
            mt.add("Seam", x, y, z, filament=tool, object_id=obj, feedrate=feed, fan=fan,
                   temperature=temp, acceleration=accel, layer_id=layer, print_z=z, gcode_line=line_no)
        mt.add(kind, nx, ny, nz, role=role if kind == "Extrude" else "None", filament=tool,
               object_id=obj, width=width if kind == "Extrude" else 0.0,
               height=height if kind == "Extrude" else 0.0, mm3_per_mm=mm3, feedrate=feed,
               fan=fan, temperature=temp, acceleration=accel, duration=dur, layer_id=layer,
               print_z=nz, gcode_line=line_no)

    for idx, raw in enumerate(io.BytesIO(data)):
        line_no = idx + 1
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        code, sep, comment = text.partition(";")
        if sep:
            c = comment
            for tags in (PLAIN, BAMBU):
                v = _tag_value(c, tags, "role")
                if v is not None:
                    role = TEXT_TO_ROLE.get(v, "Custom")
                    seam_pending = role == "ExternalPerimeter"
                v = _tag_value(c, tags, "width")
                if v is not None and _floats(v):
                    width = _floats(v)[0]
                v = _tag_value(c, tags, "height")
                if v is not None and _floats(v):
                    height = _floats(v)[0]
                if c.startswith(tags["layer"]):
                    layer = layer_seen
                    layer_seen += 1
            if c.startswith(" WIPE_START"):
                wiping = True
            elif c.startswith(" WIPE_END"):
                wiping = False
            m = _OBJ_START.match(c)
            if m:
                name = m.group(1)
                if name not in objects:
                    objects.append(name)
                obj = objects.index(name)
            elif c.startswith(" stop printing object"):
                obj = -1
        code = code.strip()
        if not code:
            continue
        m = _CMD.match(code)            # "G1X10Y0E1" has no spaces
        cmd = m.group(1).upper() if m else code.split()[0].upper()
        rest = code[m.end():] if m else code[len(cmd):]
        words = {}
        for letter, value in _WORD.findall(rest):
            words[letter.upper()] = float(value)

        if cmd in ("G0", "G1", "G2", "G3"):
            if re.search(r"[XYZEFIJR](?![-+]?\.?\d)", rest, re.I):
                warnings.append(issue("warning", "gcode_processor",
                                      f"line {line_no}: cannot parse {code!r}"))
                continue
            if "F" in words:
                feed = words["F"] / 60.0
            nx = (words["X"] if abs_xyz else x + words["X"]) if "X" in words else x
            ny = (words["Y"] if abs_xyz else y + words["Y"]) if "Y" in words else y
            nz = (words["Z"] if abs_xyz else z + words["Z"]) if "Z" in words else z
            ne = (words["E"] if (e_abs and abs_xyz) else e + words["E"]) if "E" in words else e
            de = ne - e
            moved_xy = (nx, ny) != (x, y)
            if cmd in ("G2", "G3"):
                pts = _arc_points(x, y, nx, ny, words.get("I"), words.get("J"), words.get("R"),
                                  clockwise=(cmd == "G2"))
                if pts is None:
                    warnings.append(issue("warning", "gcode_processor",
                                          f"line {line_no}: arc with R and identical endpoints "
                                          "is ambiguous; skipped"))
                    e = ne
                    continue
                lengths, px, py = [], x, y
                for qx, qy in pts:
                    lengths.append(math.hypot(qx - px, qy - py))
                    px, py = qx, qy
                total = sum(lengths) or 1.0
                kind = "Extrude" if de > 0 else "Travel"
                for (qx, qy), seg in zip(pts, lengths):
                    share = de * seg / total
                    add_move(kind, qx, qy, nz, share, line_no, seg, rel_extrusion=max(share, 0.0))
                    x, y = qx, qy
                z, e = nz, ne
                continue
            if de > 1e-9 and moved_xy:
                add_move("Extrude", nx, ny, nz, de, line_no, rel_extrusion=de)
            elif de < -1e-9 and moved_xy and wiping:
                add_move("Wipe", nx, ny, nz, de, line_no)
            elif de < -1e-9 and not moved_xy:
                add_move("Retract", nx, ny, nz, de, line_no)
            elif de > 1e-9:
                add_move("Unretract", nx, ny, nz, de, line_no)
            elif moved_xy or nz != z:
                add_move("Travel", nx, ny, nz, 0.0, line_no)
            x, y, z, e = nx, ny, nz, ne
        elif cmd == "G90":
            abs_xyz = True
        elif cmd == "G91":
            abs_xyz = False
        elif cmd == "M82":
            e_abs = True
        elif cmd == "M83":
            e_abs = False
        elif cmd == "G92":
            x, y, z = words.get("X", x), words.get("Y", y), words.get("Z", z)
            e = words.get("E", e)
        elif cmd in ("M104", "M109") and "S" in words:
            temp = words["S"]
        elif cmd == "M106" and words.get("P", 0) == 0:      # P<n> with n != 0 is another fan
            fan = min(100.0, words.get("S", 255.0) / 2.55)
        elif cmd == "M107" and words.get("P", 0) == 0:
            fan = 0.0
        elif cmd == "M204" and ("S" in words or "P" in words):
            accel = words.get("S", words.get("P", accel))
        elif re.fullmatch(r"T\d+", cmd):
            n = int(cmd[1:])
            if n >= MAX_TOOL or (known_filaments and n >= known_filaments):
                # Bambu-style T255, T1000... are not tool changes; so are tools the file's own
                # configuration does not list. Warn once per command, keep the current tool.
                if cmd not in warned_tools:
                    warned_tools.add(cmd)
                    warnings.append(issue("warning", "gcode_processor",
                                          f"line {line_no}: {cmd} is not a tool change; ignored"))
            else:
                tool = n
                max_tool = max(max_tool, tool)
        elif cmd == "EXCLUDE_OBJECT_START":
            m = re.search(r"NAME=(\S+)", code)
            if m:
                if m.group(1) not in objects:
                    objects.append(m.group(1))
                obj = objects.index(m.group(1))
        elif cmd == "EXCLUDE_OBJECT_END":
            obj = -1

    moves = mt.arrays()
    layers = build_layers(moves)
    nfil = max_tool + 1
    pad = lambda v: (v * nfil)[:nfil] if len(v) < nfil else v[:nfil]  # noqa: E731
    stats = build_stats(moves, n_filaments=nfil, diameters=pad(diameters), densities=pad(densities),
                        costs=pad(costs), layer_count=len(layers["z"]))
    config = defaults()
    if "layer_height" in meta and _floats(meta["layer_height"]):
        config["layer_height"] = f"{_floats(meta['layer_height'])[0]:g}"
    return SliceResult(gcode_path=path, moves=moves, layers=layers,
                       gcode_line_ends=ends, stats=stats,
                       warnings=warnings, objects=objects, wipe_tower=None, config=config,
                       owns_file=False)
