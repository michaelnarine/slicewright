# SPDX-License-Identifier: GPL-3.0-or-later
"""The "Slicer" workspace: what to create and what to leave alone, and the layout as data (03 section 1.5).

Pure Python (no ``bpy``). ``blender/workspace.py`` does the Blender calls; this module decides.

* :func:`add_decision` / :func:`remove_targets` keep the add-on from ever touching a workspace it did not create:
  a workspace is the add-on's only if it carries the tag (a custom property set at creation).
* :func:`join_plan` turns whatever areas the duplicated workspace came with into one area, as a list of
  ``area_join`` calls, so the layout starts from the same place whatever the user did to their own workspace.
* :data:`LAYOUT` is the split tree built from that one area. A split's ``factor`` is the share of the *low* side
  (left for ``VERTICAL``, bottom for ``HORIZONTAL``); this is Blender's own ``area_split`` factor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

NAME = "Slicer"
TAG = "slicewright_workspace"           # custom property on the workspace the add-on created
TAG_VALUE = 1
BASE_NAME = "Layout"                    # the workspace that is duplicated when there is one

CREATE, ACTIVATE, REFUSE = "create", "activate", "refuse"
NAME_TAKEN = ("A workspace named \"Slicer\" already exists and was not made by Slicewright. "
              "Rename or remove it first; it was left alone.")

HELP = ("Adds a \"Slicer\" workspace tab: a large viewport, the outliner, a wide Settings editor and the Info log. "
        "It is stored in the .blend file; use File > Defaults > Save Startup File yourself if you want it in every new file. "
        "The add-on never saves the startup file or your preferences.")


@dataclass(frozen=True)
class Add:
    action: str            # CREATE, ACTIVATE (ours exists: just switch to it) or REFUSE
    message: str = ""


def add_decision(names: Sequence[str], tagged: Sequence[str]) -> Add:
    """``names``: every workspace in the file; ``tagged``: those carrying the add-on's tag."""
    if NAME in tagged:
        return Add(ACTIVATE)
    if NAME in names:
        return Add(REFUSE, NAME_TAKEN)
    return Add(CREATE)


def remove_targets(names: Sequence[str], tagged: Sequence[str]) -> list[str]:
    """The workspaces the add-on may remove: tagged ones only."""
    return [n for n in names if n in tagged]


def fallback_workspace(names: Sequence[str], removing: Sequence[str]) -> str | None:
    """Where to go before removing the active workspace: Layout, else the first one that stays."""
    keep = [n for n in names if n not in removing]
    if BASE_NAME in keep:
        return BASE_NAME
    return keep[0] if keep else None


def base_workspace(names: Sequence[str], tagged: Sequence[str], current: str) -> str:
    """The workspace to duplicate: Layout when present, else the current one unless that is ours."""
    if BASE_NAME in names and BASE_NAME not in tagged:
        return BASE_NAME
    if current in names and current not in tagged:
        return current
    return next((n for n in names if n not in tagged), current)


# --------------------------------------------------------------------------- joining areas into one

Rect = tuple[float, float, float, float]       # x, y, width, height

EDGE_TOL = 24.0                                # pixels: the gap between two areas is a few pixels of border


def _adjacent(a: Rect, b: Rect, tol: float) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    same_cols = abs(ax - bx) <= tol and abs(aw - bw) <= tol
    same_rows = abs(ay - by) <= tol and abs(ah - bh) <= tol
    if same_cols and (abs((ay + ah) - by) <= tol or abs((by + bh) - ay) <= tol):
        return True
    return same_rows and (abs((ax + aw) - bx) <= tol or abs((bx + bw) - ax) <= tol)


def _union(a: Rect, b: Rect) -> Rect:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def join_plan(rects: Sequence[Rect], tol: float = EDGE_TOL) -> list[tuple[int, int]]:
    """``(source, target)`` index pairs to pass to ``area_join``, in order, until one area is left (or none can join).

    The source survives with the union of both rectangles (observed on Blender 5.1.2), so a later pair may name a
    survivor. Indices refer to the input list."""
    live = {i: tuple(r) for i, r in enumerate(rects)}
    plan: list[tuple[int, int]] = []
    while len(live) > 1:
        pair = next(((i, j) for i in live for j in live if i != j and _adjacent(live[i], live[j], tol)), None)
        if pair is None:
            break
        i, j = pair
        live[i] = _union(live[i], live[j])
        del live[j]
        plan.append((i, j))
    return plan


# --------------------------------------------------------------------------- the layout

VERTICAL, HORIZONTAL = "VERTICAL", "HORIZONTAL"


@dataclass(frozen=True)
class Split:
    target: str            # key of the area being split
    direction: str         # VERTICAL: a vertical edge (left | right); HORIZONTAL: a horizontal edge (bottom / top)
    factor: float          # share of the low side (left or bottom)
    low: str               # key of the left / bottom part
    high: str              # key of the right / top part


ROOT = "root"
# Left column: the viewport over a thin Info strip. Right column: a compact outliner over the wide settings editor.
LAYOUT: tuple[Split, ...] = (
    Split(ROOT, VERTICAL, 0.62, "left", "right"),
    Split("left", HORIZONTAL, 0.10, "info", "viewport"),
    Split("right", HORIZONTAL, 0.86, "settings", "outliner"),
)
AREA_TYPES = {"viewport": "VIEW_3D", "info": "INFO", "outliner": "OUTLINER", "settings": "PROPERTIES"}


def final_keys(layout: Sequence[Split] = LAYOUT) -> set[str]:
    """Keys that are never split again: the areas the user ends up with."""
    keys = {ROOT}
    for s in layout:
        keys.discard(s.target)
        keys.update((s.low, s.high))
    return keys


def layout_problems(layout: Sequence[Split] = LAYOUT, types: dict[str, str] = AREA_TYPES) -> list[str]:
    """Checks used by the tests: every split names an existing, unsplit area; every final area has a type."""
    problems, known = [], {ROOT}
    for s in layout:
        if s.target not in known:
            problems.append(f"split of unknown area {s.target}")
        known.discard(s.target)
        if s.direction not in (VERTICAL, HORIZONTAL):
            problems.append(f"bad direction {s.direction}")
        if not 0.05 <= s.factor <= 0.95:
            problems.append(f"factor {s.factor} out of range")
        known.update((s.low, s.high))
    for key in known:
        if key not in types:
            problems.append(f"no area type for {key}")
    return problems
