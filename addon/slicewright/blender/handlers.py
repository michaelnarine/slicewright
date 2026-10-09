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


# (handler list name, function) pairs, so register and unregister cannot drift apart.
HANDLERS = (("load_pre", _load_pre),)


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
