# SPDX-License-Identifier: GPL-3.0-or-later
"""UI panels. The diagnostics panel is always registered; the rest need a usable engine."""
from __future__ import annotations

from .. import registry
from . import diagnostics, issues, picker, settings, slice_panel

REQUIRES_ENGINE = False
classes = (diagnostics.SLICEWRIGHT_PT_diagnostics,)
top_classes = slice_panel.classes + issues.classes   # first in the tab: the Slice button, then Issues (panels show in registration order)
engine_classes = (picker.SLICEWRIGHT_PT_printer, settings.SLICEWRIGHT_PT_settings)     # registered only when the engine is usable


def _wanted() -> tuple:
    status = registry.state.status
    ok = status is not None and status.ok
    return (top_classes if ok else ()) + classes + (engine_classes if ok else ())


def register() -> None:
    registry.register_classes(_wanted())


def unregister() -> None:
    registry.unregister_classes(top_classes + classes + engine_classes)
    settings.clear()
