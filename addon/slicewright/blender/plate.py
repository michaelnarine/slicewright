# SPDX-License-Identifier: GPL-3.0-or-later
"""Registration stage for the plate feature: bed drawing, plate operators and the Plate panel."""
from __future__ import annotations

from . import bed_draw, registry, volume
from .operators import plate as plate_ops
from .ui import plate as plate_ui

REQUIRES_ENGINE = True
classes = (*plate_ops.classes, *plate_ui.classes)


def register() -> None:
    registry.register_classes(classes)
    bed_draw.register()
    volume.register()


def unregister() -> None:
    volume.unregister()
    bed_draw.unregister()
    registry.unregister_classes(classes)
