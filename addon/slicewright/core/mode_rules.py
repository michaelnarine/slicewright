# SPDX-License-Identifier: GPL-3.0-or-later
"""The Prepare / Preview switch as plain rules (03 section 1.1). No ``bpy``.

``Scene.slicewright.mode`` is PREPARE (arrange, paint, live out-of-volume outlines) or PREVIEW (the toolpaths of the
last slice). The Blender side (``blender/mode.py``) asks these functions what a switch needs and what a file load
should leave behind, so the decisions are unit-tested without Blender.
"""
from __future__ import annotations

PREPARE, PREVIEW = "PREPARE", "PREVIEW"
MODES = (PREPARE, PREVIEW)

NO_RESULT_REASON = "Slice the plate to preview it"
PREPARE_ONLY_REASON = "Switch to Prepare to do this"


def switch_problem(target: str, has_preview: bool) -> str | None:
    """Why the scene cannot switch to ``target``, or ``None`` when it can. Prepare is always possible; Preview
    needs a slice result held for the scene (a preview that is loaded or still loading)."""
    if target not in MODES:
        return f"Unknown mode {target!r}"
    if target == PREVIEW and not has_preview:
        return NO_RESULT_REASON
    return None


def mode_after_load(saved: str, has_key: bool, restoring: bool) -> str:
    """The mode a scene should have after a file load. A file saved in Preview stays in Preview while its cached
    slice is being brought back (``restoring``); with nothing to bring back (no ``last_cache_key``, or its cache
    entry is gone) there is no preview to look at, so the scene opens in Prepare."""
    if saved == PREVIEW and has_key and restoring:
        return PREVIEW
    return PREPARE
