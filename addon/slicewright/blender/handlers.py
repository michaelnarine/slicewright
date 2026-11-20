# SPDX-License-Identifier: GPL-3.0-or-later
"""Application handlers (03 sections 6.1 and 6.3). Everything registered here is removed again."""
from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from . import registry, staleness

REQUIRES_ENGINE = False


@persistent
def _load_pre(*args) -> None:
    registry.on_load_pre(*args)


@persistent
def _load_post(*args) -> None:
    registry.on_load_post(*args)


@persistent
def _save_pre(*args) -> None:
    """Embed the presets in use (03 section 2.5). Never lets a failure block saving."""
    try:
        from . import user_presets
        user_presets.embed_all_scenes()
    except Exception:  # noqa: BLE001
        from ..core import logs
        logs.get_logger("handlers").exception("save_pre failed")


@persistent
def _depsgraph_update_post(scene, depsgraph=None) -> None:
    """Stale preview detection (03 section 6.3): classify the update, never compute. Never breaks an edit."""
    try:
        staleness.on_depsgraph_update(scene, depsgraph)
    except Exception:  # noqa: BLE001
        from ..core import logs
        logs.get_logger("handlers").exception("depsgraph_update_post failed")


@persistent
def _undo_redo_post(*args) -> None:
    try:
        staleness.on_undo_redo(*args)
    except Exception:  # noqa: BLE001
        from ..core import logs
        logs.get_logger("handlers").exception("undo/redo handler failed")


# (handler list name, function) pairs, so register and unregister cannot drift apart.
HANDLERS = (("load_pre", _load_pre), ("load_post", _load_post), ("save_pre", _save_pre),
            ("depsgraph_update_post", _depsgraph_update_post),
            ("undo_post", _undo_redo_post), ("redo_post", _undo_redo_post))


def register() -> None:
    for name, fn in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn not in handlers:
            handlers.append(fn)


def unregister() -> None:
    for name, fn in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        while fn in handlers:
            handlers.remove(fn)
