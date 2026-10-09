# SPDX-License-Identifier: AGPL-3.0-only
"""The spike's cube and profiles as a reusable case: build a SliceJob through the public API, slice, and
compare with the official OrcaSlicer v2.4.2 G-code. Used by the pytest suite (plain Python) and by the
Blender script (engine/tests/blender/run_cube.py), so both run the very same code path."""
from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "spike_cube"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import normalize_gcode  # noqa: E402


def load_stl(path: Path):
    """ASCII STL -> (vertices float32 (N,3), triangles int32 (M,3)), vertices shared by coordinates."""
    index: dict[tuple, int] = {}
    verts: list[tuple] = []
    tris: list[list[int]] = []
    cur: list[int] = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts[:1] == ["vertex"]:
            v = tuple(float(x) for x in parts[1:4])
            if v not in index:
                index[v] = len(verts)
                verts.append(v)
            cur.append(index[v])
            if len(cur) == 3:
                tris.append(cur)
                cur = []
    return np.ascontiguousarray(np.array(verts, dtype=np.float32)), np.ascontiguousarray(np.array(tris, dtype=np.int32))


def profiles() -> dict:
    return {n: json.loads((FIXTURES / f"{n}.json").read_text()) for n in ("machine", "process", "filament")}


def bed_centre(machine: dict):
    pts = [tuple(float(c) for c in p.split("x")) for p in machine["printable_area"]]
    xs, ys = zip(*pts)
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def build_job(sc, threads=None):
    p = profiles()
    flat = sc.normalize_config(sc.compose_config(p["machine"], p["process"], [p["filament"]]))["config"]
    job = sc.SliceJob()
    job.set_config(flat)
    if threads:
        job.set_threads(threads)
    v, t = load_stl(FIXTURES / "cube.stl")
    cx, cy = bed_centre(p["machine"])
    v = np.ascontiguousarray(v + np.array([cx - 10.0, cy - 10.0, 0.0], dtype=np.float32))  # cube is 0..20 at the origin
    job.add_object("cube", v, t)
    return job


def diff_against_reference(gcode_text: str):
    ref = normalize_gcode.normalize((FIXTURES / "reference_orca_v2.4.2.gcode").read_text(errors="replace"))
    got = normalize_gcode.normalize(gcode_text)
    return [d for d in difflib.unified_diff(ref, got, "official-orca-v2.4.2", "slicewright", lineterm="", n=0)
            if not d.startswith(("---", "+++", "@@"))]
