# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixtures of the real-engine tests (builders are in ``real_helpers``); ``real_sc`` is in ``tests/conftest.py``."""
import time

import pytest
from real_helpers import JobRunner, drive, jobs, request


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
