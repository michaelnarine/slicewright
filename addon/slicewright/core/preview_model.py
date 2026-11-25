# SPDX-License-Identifier: GPL-3.0-or-later
"""What happens to the plate's source objects while the viewport shows the Preview. No ``bpy``.

The Preview option "Model" is Hidden (default), Ghost or Shown. Hidden and Ghost keep the source objects from blocking
the view of the toolpaths. The mechanism writes **no object data** (so nothing is lost to a crash, a save, an
undo or an add-on removal): it switches off the viewport's per-type *object visibility filters*
(``SpaceView3D.show_object_viewport_mesh`` and friends, the "Object Types Visibility" popover) of the 3D views that
show the scene, for the geometry types the plate holds. Those are screen (UI) state, they do not touch objects, the
depsgraph or the selection, so slicing, staleness and Arrange keep working on the hidden objects. Ghost additionally
draws the plate faintly from the add-on's own draw handler.

This module is the decision logic: what to switch, and the record of what *we* switched (so only those filters are
ever switched back, never one the user had turned off themselves). The Blender side is ``blender/model_visibility.py``.
"""
from __future__ import annotations

import json
from typing import Iterable, Mapping

HIDDEN, GHOST, SHOWN = "HIDDEN", "GHOST", "SHOWN"
MODELS = (HIDDEN, GHOST, SHOWN)
MODEL_ITEMS = (
    (HIDDEN, "Hidden", "Hide the model so it does not block the toolpaths"),
    (GHOST, "Ghost", "Draw the model faintly behind the toolpaths"),
    (SHOWN, "Shown", "Show the model as in Prepare"),
)

# Object type -> the SpaceView3D property that filters that type. Types the plate can hold (plate_collection).
TYPE_FILTERS = {
    "MESH": "show_object_viewport_mesh", "CURVE": "show_object_viewport_curve",
    "SURFACE": "show_object_viewport_surf", "META": "show_object_viewport_meta",
    "FONT": "show_object_viewport_font", "CURVES": "show_object_viewport_curves",
    "EMPTY": "show_object_viewport_empty",
}


def wants_hide(model: str, mode: str, has_preview: bool) -> bool:
    """The model is kept out of the viewport: the scene is in Preview, there is a preview to look at, and the
    option is Hidden or Ghost."""
    return mode == "PREVIEW" and has_preview and model in (HIDDEN, GHOST)


def filter_props(object_types: Iterable[str]) -> tuple[str, ...]:
    """The visibility-filter properties to switch for plate objects of these types (sorted, unknown types skipped)."""
    return tuple(sorted({TYPE_FILTERS[t] for t in object_types if t in TYPE_FILTERS}))


def space_key(screen: str, area: int, space: int, prop: str) -> str:
    return f"{screen}|{area}|{space}|{prop}"


def screen_of(key: str) -> str:
    return key.split("|", 1)[0]


def prop_of(key: str) -> str:
    return key.rsplit("|", 1)[1]


def encode_record(keys: Iterable[str]) -> str:
    return json.dumps(sorted(set(keys)))


def decode_record(text: str) -> set[str]:
    """The keys of a stored record. A damaged or foreign value is an empty record (nothing to put back)."""
    if not text:
        return set()
    try:
        value = json.loads(text)
    except ValueError:
        return set()
    return {k for k in value if isinstance(k, str) and k.count("|") == 3} if isinstance(value, list) else set()


def plan(state: Mapping[str, bool], eligible_screens: Iterable[str], wanted_props: Iterable[str], hide: bool,
         record: Iterable[str]) -> tuple[list[str], list[str], set[str]]:
    """What to switch. ``state`` maps every filter key (``space_key``) of every 3D view to its current value (True =
    the type is shown). ``eligible_screens`` are the screens whose scene wants the model hidden (``hide``); only
    keys with a property in ``wanted_props`` are hidden. ``record`` holds the keys we switched off earlier.

    Returns ``(switch_off, switch_on, new_record)``. A key is switched back on only when it is in the record, so a
    filter the user turned off is never touched; a recorded key whose view is gone is just dropped."""
    record = set(record)
    wanted, eligible = set(wanted_props), set(eligible_screens)
    off: list[str] = []
    on: list[str] = []
    keep: set[str] = set()
    for key in sorted(record):
        if key not in state:
            continue                                     # that view no longer exists: nothing to put back
        if hide and screen_of(key) in eligible and prop_of(key) in wanted:
            keep.add(key)                                # still wanted hidden
        elif not state[key]:
            on.append(key)                               # ours and still off: put it back
        # ours but already shown again (the user did it): just forget it
    if hide:
        for key, shown in state.items():
            if shown and screen_of(key) in eligible and prop_of(key) in wanted:
                off.append(key)
                keep.add(key)
    return sorted(off), on, keep
