# SPDX-License-Identifier: GPL-3.0-or-later
"""Local corrections to the engine's setting tooltips (03 section 2.4). No ``bpy``.

Labels and tooltips are the engine's own data and are read at runtime (04 section 6.4); the add-on ships no copy. A few of
Orca's tooltips are wrong or leave out something that decides whether a value works, and the engine is not ours to
patch, so this small table carries the corrected wording. Each entry has a ``marker``: the correction applies only while
the engine's tooltip still contains it, so an engine that fixes its text is shown as it is. ``replace`` swaps the whole
tooltip, ``append`` adds a note after it. The corrected text is what the setting's property carries (the tooltip in
the settings pages and the Object panel's picker) and what the settings filter searches.

Both entries are measured, not guessed: ``research/non-planar/stage0/STAGE0.md`` of the Z contouring experiments (an
engine slice of a dome and a ramp over each value).
"""
from __future__ import annotations

from dataclasses import dataclass

REPLACE, APPEND = "replace", "append"


@dataclass(frozen=True)
class Correction:
    marker: str          # the engine's tooltip must contain this for the correction to apply
    text: str
    how: str = REPLACE


CORRECTIONS: dict[str, Correction] = {
    "zaa_minimize_perimeter_height": Correction(
        marker="slope less than",
        text=("Lowers the outer walls of sloped top surfaces so they follow the model's edge height. It acts where the "
              "surface slope is GREATER than this angle (degrees); flatter surfaces are left alone. (Orca's own text "
              "says \"less than\"; the engine does the opposite.) Low values lower more walls: 10 to 20 degrees made a "
              "29 degree dome print worse in tests. 35 is the default; 0 turns it off.")),
    "zaa_min_z": Correction(
        marker="Minimum Z-layer height",
        text=("This is a fixed height in mm: it does not scale with the layer height. 0.05 is near-optimal at 0.2 mm "
              "layers; use a smaller value for finer layers (0.05 gave no gain at 0.08 mm). A value above the layer "
              "height (for example 0.25 at 0.2 mm) is refused with \"Bad min Z value\"."),
        how=APPEND),
}


def corrected(key: str, tooltip: str) -> str:
    """The tooltip to show for ``key``: the engine's ``tooltip``, or its corrected form when this table has one that
    still applies."""
    fix = CORRECTIONS.get(key)
    if fix is None or fix.marker not in (tooltip or ""):
        return tooltip or ""
    if fix.how == APPEND:
        return f"{tooltip.rstrip()}\n{fix.text}"
    return fix.text
