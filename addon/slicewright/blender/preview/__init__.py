# SPDX-License-Identifier: GPL-3.0-or-later
"""The toolpath preview (03 section 7): shaders, chunked renderer, scrubbing, views.

This module is a registration stage (see ``slicewright.STAGES``). It imports ``bpy`` only inside
``register``/``unregister``, so the pure submodules (``shaders``, ``templates``) import anywhere.
"""
from __future__ import annotations

REQUIRES_ENGINE = True


def register() -> None:
    from .. import registry
    from . import handler, operators, props, ui
    props.register()
    registry.register_classes(operators.classes + ui.classes)
    operators.register_keymap()
    handler.register()


def unregister() -> None:
    from .. import registry
    from . import chips, handler, operators, props, ui
    handler.unregister()
    chips.release()
    operators.unregister_keymap()
    registry.unregister_classes(operators.classes + ui.classes)
    props.unregister()
