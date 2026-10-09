# SPDX-License-Identifier: GPL-3.0-or-later
"""The toolpath preview (03 section 7): shaders, chunked renderer, scrubbing, views.

This module is a registration stage (see ``slicewright.STAGES``). It imports ``bpy`` only inside
``register``/``unregister``, so the pure submodules (``shaders``, ``templates``) import anywhere.
"""
from __future__ import annotations

REQUIRES_ENGINE = True


def register() -> None:
    from .. import registry
    from . import operators, props
    props.register()
    registry.register_classes(operators.classes)
    operators.register_keymap()


def unregister() -> None:
    from .. import registry
    from . import operators, props, runtime
    runtime.clear_all()
    operators.unregister_keymap()
    registry.unregister_classes(operators.classes)
    props.unregister()
