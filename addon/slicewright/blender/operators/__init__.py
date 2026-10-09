# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators. The diagnostics operator is always available, even without an engine."""
from __future__ import annotations

from .. import registry
from . import diagnostics

REQUIRES_ENGINE = False
classes = (diagnostics.SLICEWRIGHT_OT_copy_diagnostics,)


def register() -> None:
    registry.register_classes(classes)


def unregister() -> None:
    registry.unregister_classes(classes)
