# SPDX-License-Identifier: AGPL-3.0-only
"""Meshes and jobs that keep the engine busy for seconds, for the cancel-latency and RSS measurements.

A subdivided icosphere has overhangs on its lower half, so with supports on every stage of the pipeline has real
work to do (slicing, perimeters, infill, support generation, G-code)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import cube_case  # noqa: E402


def icosphere(radius: float = 30.0, subdivisions: int = 4, centre=(128.0, 128.0, None)):
    t = (1.0 + 5.0 ** 0.5) / 2.0
    v = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
         (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6),
         (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
         (8, 6, 7), (9, 8, 1)]
    verts = [np.array(p, dtype=np.float64) / np.linalg.norm(p) for p in v]
    faces = list(f)
    for _ in range(subdivisions):
        cache: dict[tuple, int] = {}

        def mid(a, b):
            key = (min(a, b), max(a, b))
            if key not in cache:
                m = verts[a] + verts[b]
                verts.append(m / np.linalg.norm(m))
                cache[key] = len(verts) - 1
            return cache[key]

        nf = []
        for a, b, c in faces:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = nf
    pts = np.array(verts) * radius
    cz = radius if centre[2] is None else centre[2]
    pts += np.array([centre[0], centre[1], cz])
    return np.ascontiguousarray(pts.astype(np.float32)), np.ascontiguousarray(np.array(faces, dtype=np.int32))


def heavy_job(sc, threads=None, subdivisions=4, supports=True, extra=None):
    """A job whose slice takes a few seconds: a sphere with tree supports under it."""
    p = cube_case.profiles()
    process = dict(p["process"])
    process.update({"enable_support": "1" if supports else "0", "support_type": "tree(auto)",
                    "sparse_infill_density": "20%", "layer_height": "0.1", "initial_layer_print_height": "0.2"})
    process.update(extra or {})
    flat = sc.normalize_config(sc.compose_config(p["machine"], process, [p["filament"]]))["config"]
    job = sc.SliceJob()
    job.set_config(flat)
    if threads:
        job.set_threads(threads)
    v, t = icosphere(subdivisions=subdivisions)
    job.add_object("sphere", v, t)
    return job
