# SPDX-License-Identifier: GPL-3.0-or-later
"""Slicewright: an FDM slicer for Blender.

``register`` loads and version-checks the engine first (04 section 10). If it cannot be
used, only the preferences, the copy-diagnostics operator and the diagnostics panel are
registered: the add-on never half-works. Otherwise every stage registers, in order
(03 section 9.1: props, config_pg, operators, ui, handlers), and ``unregister`` undoes
exactly the stages that registered, in reverse.

Importing this package must not import ``bpy``, so pure-Python tests can import the
``core`` and ``engine`` subpackages.
"""
from __future__ import annotations

import importlib

# (module relative to this package, name used in log messages). Each module has
# ``register()``, ``unregister()`` and ``REQUIRES_ENGINE``.
STAGES = (
    "prefs",
    "blender.props",          # Scene.slicewright
    "blender.operators",
    "blender.ui",
    "blender.handlers",
)

_registered: list = []


def _log_dir() -> str | None:
    """``extension_path_user(..., 'logs')``; None when not installed as an extension."""
    try:
        import bpy
        return bpy.utils.extension_path_user(__package__, path="logs", create=True)
    except Exception:  # noqa: BLE001 - e.g. loaded from a source checkout in tests
        return None


def register() -> None:
    from .blender import registry
    from .core import logs
    from .engine import adapter

    if _registered:          # tolerate a double register (e.g. reloading scripts)
        unregister()
    status = adapter.load()
    registry.state.status = status
    registry.state.log_dir = _log_dir()
    logs.setup("WARNING", registry.state.log_dir)
    log = logs.get_logger()
    log.info("%s", status.summary)
    if not status.ok:
        log.error("engine unavailable, registering diagnostics only: %s", status.error)
    try:
        for name in STAGES:
            module = importlib.import_module(f".{name}", __package__)
            if module.REQUIRES_ENGINE and not status.ok:
                continue
            module.register()
            _registered.append(module)
    except Exception:
        log.exception("registration failed; rolling back")
        unregister()
        raise
    from . import prefs
    registry.apply_log_level(prefs.current_log_level())


def unregister() -> None:
    from .blender import registry
    from .core import logs

    registry.unregister_timers()
    for module in reversed(_registered):
        try:
            module.unregister()
        except Exception:  # noqa: BLE001 - keep unwinding the remaining stages
            logs.get_logger().exception("unregistering %s failed", module.__name__)
    _registered.clear()
    registry.state.reset()
    logs.teardown()
