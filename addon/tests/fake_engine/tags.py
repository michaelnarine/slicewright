# SPDX-License-Identifier: GPL-3.0-or-later
"""G-code comment tags the fake writes and ``from_gcode`` reads (03 section 9.2).

Two dialects exist. Plain (non-Bambu) output marks features with
``;TYPE:<role>``, ``;WIDTH:<mm>``, ``;HEIGHT:<mm>`` and ``;LAYER_CHANGE``.
Bambu-style output uses ``; FEATURE: <role>``, ``; LINE_WIDTH: <mm>``,
``; LAYER_HEIGHT: <mm>`` and ``; CHANGE_LAYER``. The role strings are the
user-visible feature names printed by the slicer.
"""
from __future__ import annotations

ROLE_TO_TEXT = {
    "None": "Undefined",
    "Perimeter": "Inner wall",
    "ExternalPerimeter": "Outer wall",
    "OverhangPerimeter": "Overhang wall",
    "InternalInfill": "Sparse infill",
    "SolidInfill": "Internal solid infill",
    "TopSolidInfill": "Top surface",
    "BottomSurface": "Bottom surface",
    "Ironing": "Ironing",
    "BridgeInfill": "Bridge",
    "InternalBridgeInfill": "Internal Bridge",
    "GapFill": "Gap infill",
    "Skirt": "Skirt",
    "Brim": "Brim",
    "SupportMaterial": "Support",
    "SupportMaterialInterface": "Support interface",
    "SupportTransition": "Support transition",
    "WipeTower": "Prime tower",
    "Custom": "Custom",
    "Mixed": "Multiple",
}
TEXT_TO_ROLE = {v: k for k, v in ROLE_TO_TEXT.items()}

PLAIN = {"role": "TYPE:", "width": "WIDTH:", "height": "HEIGHT:", "layer": "LAYER_CHANGE"}
BAMBU = {"role": " FEATURE: ", "width": " LINE_WIDTH: ", "height": " LAYER_HEIGHT: ",
         "layer": " CHANGE_LAYER"}
