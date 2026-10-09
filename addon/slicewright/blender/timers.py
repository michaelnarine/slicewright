# SPDX-License-Identifier: GPL-3.0-or-later
"""The add-on's single ``bpy.app.timers`` callback, driving ``core.ticking.TickRunner`` (03 section 6.1).

One persistent timer serves every chunked task (index build, preview upload, cache writes,
network sends). It runs only while there is work: it re-arms itself when a task is
submitted to an idle runner and stops itself (returns ``None``) when the queue is empty.
"""
from __future__ import annotations

import bpy

from ..core.ticking import TickRunner
from . import registry

REQUIRES_ENGINE = False


def _tick() -> float | None:
    return runner.tick()


def ensure_running() -> None:
    """Arm the timer if it is not already registered."""
    if not bpy.app.timers.is_registered(_tick):
        registry.register_timer(_tick, first_interval=0.0)


runner = TickRunner(wake=ensure_running)


def register() -> None:
    runner.wake = ensure_running


def unregister() -> None:
    runner.cancel_all()
    runner.wake = None
    registry.unregister_timers()
