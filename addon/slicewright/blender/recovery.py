# SPDX-License-Identifier: GPL-3.0-or-later
"""Recovery copies (03 section 6.1): cheap insurance against a native crash taking Blender down mid-slice.

Before a slice starts, if the preference is on, the file has unsaved changes (or was never saved) and the last
copy is older than ``recovery.INTERVAL_S``, the file is written with ``save_as_mainfile(copy=True)`` to
``<user dir>/recovery/<name>-<n>.blend`` and all but the newest ``KEEP`` copies are removed. ``copy=True`` leaves
the user's file path and dirty flag alone. A failing write is logged and never reaches the slice. Background mode
skips it (scripts and tests do not need insurance) unless ``allow_background`` is set, which tests do.
"""
from __future__ import annotations

import logging
import time

import bpy

from ..core import recovery as names
from ..core.checks import Throttle
from . import paths

log = logging.getLogger("slicewright.recovery")
allow_background = False                       # tests set this: -b normally skips recovery copies
clock = time.monotonic
_throttle: Throttle | None = None


def _gate() -> Throttle:
    global _throttle
    if _throttle is None:
        _throttle = Throttle(names.INTERVAL_S, lambda: clock())
    return _throttle


def reset() -> None:
    """Forget when the last copy was made (unregister, tests)."""
    global _throttle
    _throttle = None


def enabled() -> bool:
    from .. import prefs
    return bool(prefs.get().recovery_copy) and (allow_background or not bpy.app.background)


def directory() -> str | None:
    return paths.user_dir("recovery")


def maybe_save() -> str | None:
    """Write a recovery copy if one is due; return its path, or ``None`` (not due, not needed, or it failed)."""
    if not enabled() or not _gate().ready():
        return None
    if bpy.data.filepath and not bpy.data.is_dirty:
        return None                            # the saved file is as good as a copy
    try:
        folder = directory()
        if folder is None:
            return None
        path = names.next_path(folder, bpy.data.filepath)
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    except Exception as exc:  # noqa: BLE001 - a read-only directory, a full disk: the slice must go on
        log.warning("recovery copy failed: %s", exc)
        return None
    _gate().mark()
    names.prune(folder)
    log.info("recovery copy %s", path)
    return path
