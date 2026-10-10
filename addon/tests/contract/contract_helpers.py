# SPDX-License-Identifier: GPL-3.0-or-later
"""Small shared builders for the contract tests. Backend-agnostic: only the 04 API is used."""
from __future__ import annotations

import time

import numpy as np
import pytest

TERMINAL = ("done", "failed", "cancelled")
STATES = ("idle", "validating", "running", "cancelling") + TERMINAL
LEVELS = ("error", "warning", "info")

PRINTER = {
    "name": "Contract Printer",
    "printable_area": ["0x0", "256x0", "256x256", "0x256"],
    "printable_height": "250",
    "nozzle_diameter": ["0.4"],
}
PROCESS = {
    "name": "0.20mm Contract",
    "layer_height": "0.2",
    "initial_layer_print_height": "0.2",
    "wall_loops": "2",
    "sparse_infill_density": "15%",
}
FILAMENT = {
    "name": "Contract PLA",
    "filament_type": ["PLA"],
    "filament_colour": ["#FFFFFF"],
    "nozzle_temperature": ["220"],
}

_CUBE_TRIS = np.array([
    (0, 2, 1), (0, 3, 2),   # bottom
    (4, 5, 6), (4, 6, 7),   # top
    (0, 1, 5), (0, 5, 4),   # y-
    (1, 2, 6), (1, 6, 5),   # x+
    (2, 3, 7), (2, 7, 6),   # y+
    (3, 0, 4), (3, 4, 7),   # x-
], dtype=np.int32)


def box(cx: float = 128.0, cy: float = 128.0, size: float = 20.0, height: float | None = None,
        z0: float = 0.0):
    """A closed, outward-wound box in the bed frame. Returns (vertices float32, triangles int32)."""
    h = size if height is None else height
    r = size / 2.0
    v = np.array([
        (cx - r, cy - r, z0), (cx + r, cy - r, z0), (cx + r, cy + r, z0), (cx - r, cy + r, z0),
        (cx - r, cy - r, z0 + h), (cx + r, cy - r, z0 + h), (cx + r, cy + r, z0 + h),
        (cx - r, cy + r, z0 + h),
    ], dtype=np.float32)
    return np.ascontiguousarray(v), np.ascontiguousarray(_CUBE_TRIS.copy())


def flat_config(sc, process_overrides: dict | None = None, filaments: list | None = None,
                project: dict | None = None, printer_overrides: dict | None = None) -> dict:
    """A normalized FlatConfig built only through the public API."""
    process = {**PROCESS, **(process_overrides or {})}
    composed = sc.compose_config({**PRINTER, **(printer_overrides or {})}, process, filaments or [FILAMENT], project)
    return sc.normalize_config(composed)["config"]


def new_job(sc, objects=None, **config_kwargs):
    """A configured job holding ``objects`` (default: one 20 mm cube named "cube")."""
    job = sc.SliceJob()
    job.set_config(flat_config(sc, **config_kwargs))
    if objects is None:
        objects = [("cube", *box())]
    for name, v, t in objects:
        job.add_object(name, v, t)
    return job


def drive(job, timeout: float = 60.0):
    """Poll until a terminal state. Returns the list of every (state, percent, message) seen."""
    seen = []
    deadline = time.monotonic() + timeout
    while True:
        snap = job.poll()
        seen.append(snap)
        if snap[0] in TERMINAL:
            return seen
        if time.monotonic() > deadline:
            raise AssertionError(f"job did not finish within {timeout}s; last poll {snap}")
        time.sleep(0.002)


def sliced(sc, objects=None, **config_kwargs):
    """Run a job to ``done`` and return ``(job, result)``."""
    job = new_job(sc, objects, **config_kwargs)
    job.start()
    seen = drive(job)
    assert seen[-1][0] == "done", seen[-1]
    return job, job.result()


def assert_issue(issue: dict) -> None:
    """04 section 2.6."""
    assert set(issue) >= {"level", "code", "message", "opt_key", "object_name"}, issue
    assert issue["level"] in LEVELS
    assert isinstance(issue["code"], str) and issue["code"]
    assert isinstance(issue["message"], str)
    assert issue["opt_key"] is None or isinstance(issue["opt_key"], str)
    assert issue["object_name"] is None or isinstance(issue["object_name"], str)


def split_vector(text: str) -> list[str]:
    """Elements of a serialized string/number vector (',' or ';' separated, quotes stripped)."""
    sep = ";" if ";" in text else ","
    return [p.strip().strip('"') for p in text.split(sep) if p.strip()]


def unavailable(config, name: str, reason: str):
    """Skip when backend ``name`` was not asked for with ``--backend``; fail when it was."""
    if name in (config.getoption("--backend") or ()):
        pytest.fail(f"backend {name!r} was requested but is unavailable: {reason}")
    pytest.skip(f"backend {name!r} unavailable: {reason}")
