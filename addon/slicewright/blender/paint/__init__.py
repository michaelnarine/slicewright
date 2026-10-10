# SPDX-License-Identifier: GPL-3.0-or-later
"""Registration stage for painting (03 section 5): attributes, edit-mode operators and the panel."""
from __future__ import annotations

from .. import registry
from . import brush, operators, overlay, tool, ui

REQUIRES_ENGINE = True
classes = (*operators.classes, *tool.classes, *ui.classes)


def register() -> None:
    registry.register_classes(classes)
    overlay.register()
    brush.register()
    tool.register()


def unregister() -> None:
    tool.unregister()
    brush.unregister()
    overlay.unregister()
    registry.unregister_classes(classes)
