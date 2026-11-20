# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests of the add-on's orchestration (``engine/jobs.py``, ``engine/cache.py``) against the REAL engine.

They run only when ``slicewright_engine`` is built (``real_sc`` in ``tests/conftest.py`` skips them otherwise, or
fails with ``--backend=real``). The builders are the contract suite's: a minimal printer the real Orca accepts.
"""
import sys
import time
from pathlib import Path

import numpy as np
import pytest

CONTRACT = Path(__file__).resolve().parents[1] / "contract"
if str(CONTRACT) not in sys.path:
    sys.path.insert(0, str(CONTRACT))

from contract_helpers import FILAMENT, PRINTER, PROCESS, box  # noqa: E402
from slicewright.core.plate_data import ExtractedObject  # noqa: E402
from slicewright.engine import jobs  # noqa: E402
from slicewright.engine.jobs import JobRunner, SliceRequest  # noqa: E402


def cube(name="cube", cx=128.0, cy=128.0, size=20.0, height=None) -> ExtractedObject:
    v, t = box(cx, cy, size, height)
    return ExtractedObject(name, name, v, t)


def request(objects=None, process=None, printer=None, filaments=None, project=None, **kw) -> SliceRequest:
    return SliceRequest({**PRINTER, **(printer or {})}, {**PROCESS, **(process or {})},
                        filaments or [dict(FILAMENT)], objects if objects is not None else [cube()],
                        project=project, **kw)


def flush_project(n: int) -> dict:
    """The project overrides a plate with ``n`` filaments needs (as ``slice_inputs.project_overrides``)."""
    return {"flush_volumes_matrix": ",".join("0" if i == j else "280" for i in range(n) for j in range(n))}


def drive(runner: JobRunner, timeout: float = 60.0) -> list:
    """Poll (the engine runs on its own threads) to a terminal state. Returns every snapshot."""
    seen, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = runner.poll()
        seen.append(status)
        if status.terminal:
            return seen
        time.sleep(0.002)
    raise AssertionError(f"no terminal state in {timeout} s: {seen[-1]}")


@pytest.fixture
def runner(real_sc):
    r = JobRunner(real_sc)
    yield r
    r.cancel(discard=True)
    deadline = time.monotonic() + 30
    while r.live and time.monotonic() < deadline:     # let a live job stop: it holds the engine lock
        r.poll()
        time.sleep(0.005)


@pytest.fixture
def sliced(real_sc, runner):
    """The final ``done`` status of the default cube."""
    runner.start(request())
    final = drive(runner)[-1]
    assert final.state == jobs.DONE, final
    return final
