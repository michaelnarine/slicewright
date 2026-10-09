# SPDX-License-Identifier: GPL-3.0-or-later
"""UI panels. The diagnostics panel is always registered; the rest need a usable engine."""
from __future__ import annotations

from .. import registry
from . import diagnostics, picker

REQUIRES_ENGINE = False
classes = (diagnostics.SLICEWRIGHT_PT_diagnostics,)
engine_classes = (picker.SLICEWRIGHT_PT_printer,)     # registered only when the engine is usable


def _wanted() -> tuple:
    status = registry.state.status
    return classes + (engine_classes if status is not None and status.ok else ())


def register() -> None:
    registry.register_classes(_wanted())


def unregister() -> None:
    registry.unregister_classes(classes + engine_classes)
