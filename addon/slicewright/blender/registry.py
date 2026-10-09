# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime state and registration helpers (03 sections 2.1 and 9.1).

Runtime objects (the engine status, jobs, GPU resources later on) live here, in a
module registry, never in RNA. Everything the add-on registers with Blender that is
not a class (timers, handlers) is tracked so ``unregister`` can prove it removed it.
"""
from __future__ import annotations

from typing import Callable

import bpy

from ..core import logs
from ..engine.adapter import EngineStatus

# 04 section 3: set_log levels 0 fatal ... 5 trace.
ENGINE_LOG_LEVEL = {"ERROR": 1, "WARNING": 2, "INFO": 3, "DEBUG": 4}


class _State:
    def __init__(self) -> None:
        self.status: EngineStatus | None = None
        self.log_dir: str | None = None
        self.timers: list[Callable] = []
        self.plate_issues: list[dict] = []     # the last Check plate / Slice result (04 section 2.6 Issues)

    def reset(self) -> None:
        self.status = None
        self.log_dir = None
        self.timers.clear()
        self.plate_issues.clear()


state = _State()


def register_classes(classes) -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister_classes(classes) -> None:
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass   # already gone: unregistering must always finish


def register_timer(fn: Callable[[], float | None], first_interval: float = 0.0) -> None:
    """Register ``fn`` as a persistent timer (survives file loads, 03 section 6.1) and track it."""
    if not bpy.app.timers.is_registered(fn):
        bpy.app.timers.register(fn, first_interval=first_interval, persistent=True)
    if fn not in state.timers:
        state.timers.append(fn)


def unregister_timers() -> None:
    for fn in state.timers:
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    state.timers.clear()


def apply_log_level(level: str) -> None:
    """Set the add-on's log level and, when an engine is loaded, the engine's."""
    logs.set_level(level)
    status = state.status
    if status is not None and status.ok:
        try:
            status.module.set_log(ENGINE_LOG_LEVEL.get(level, 2))
        except Exception:  # noqa: BLE001 - logging must never break registration
            logs.get_logger("registry").exception("engine set_log failed")


def on_load_pre(*_args) -> None:
    """``load_pre``: a file load invalidates runtime objects (later: cancel any live job)."""
    logs.get_logger("registry").debug("load_pre")
