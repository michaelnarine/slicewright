# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators. The diagnostics operator is always available; the rest need a usable engine."""
from __future__ import annotations

from .. import registry
from . import diagnostics, importer, picker, presets, settings

REQUIRES_ENGINE = False
classes = (diagnostics.SLICEWRIGHT_OT_copy_diagnostics,)
engine_classes = picker.classes + settings.classes + presets.classes + importer.classes          # registered only when the engine is usable


def _wanted() -> tuple:
    status = registry.state.status
    return classes + (engine_classes if status is not None and status.ok else ())


def register() -> None:
    registry.register_classes(_wanted())


def unregister() -> None:
    registry.unregister_classes(classes + engine_classes)
