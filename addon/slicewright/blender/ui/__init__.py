# SPDX-License-Identifier: GPL-3.0-or-later
"""UI panels. The diagnostics panel is always registered, even without an engine."""
from __future__ import annotations

from .. import registry
from . import diagnostics

REQUIRES_ENGINE = False
classes = (diagnostics.SLICEWRIGHT_PT_diagnostics,)


def register() -> None:
    registry.register_classes(classes)


def unregister() -> None:
    registry.unregister_classes(classes)
