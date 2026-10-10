# SPDX-License-Identifier: GPL-3.0-or-later
"""Application handlers (03 sections 6.1 and 6.3). Everything registered here is removed again."""
from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from . import registry

REQUIRES_ENGINE = False


@persistent
def _load_pre(*args) -> None:
    registry.on_load_pre(*args)


@persistent
def _save_pre(*args) -> None:
    """Embed the presets in use (03 section 2.5). Never lets a failure block saving."""
    try:
        from . import user_presets
        user_presets.embed_all_scenes()
    except Exception:  # noqa: BLE001
        from ..core import logs
        logs.get_logger("handlers").exception("save_pre failed")


# (handler list name, function) pairs, so register and unregister cannot drift apart.
HANDLERS = (("load_pre", _load_pre), ("save_pre", _save_pre))


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
