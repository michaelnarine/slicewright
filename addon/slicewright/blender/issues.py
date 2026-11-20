# SPDX-License-Identifier: GPL-3.0-or-later
"""The Issues list at runtime (03 section 6.5): the merged list, its UIList mirror, and where it is cleared.

``core.issues.IssueBook`` is the source of truth (a runtime registry, never saved in the .blend). Blender's
``template_list`` needs an RNA collection, so the merged list is mirrored into ``WindowManager.slicewright_issues``;
a window manager's custom properties are not written to the .blend [V], and the mirror is rebuilt on every change
and emptied on file load. ``registry.state.plate_issues`` is kept equal to the merged list for older readers.

The list belongs to one scene (the one last sliced or checked); other scenes show an empty list.
"""
from __future__ import annotations

import bpy
from bpy.props import CollectionProperty, IntProperty, StringProperty

from ..core import issues as core_issues
from ..names import PACKAGE_ID
from . import registry

REQUIRES_ENGINE = True

_book = core_issues.IssueBook()
_scene: list[str | None] = [None]          # name of the scene the list belongs to


class SLICEWRIGHT_PG_Issue(bpy.types.PropertyGroup):
    """One row of the Issues UIList: the display copy of an ``Issue`` dict (04 section 2.6)."""
    level: StringProperty()
    code: StringProperty()
    message: StringProperty()
    opt_key: StringProperty()
    object_name: StringProperty()
    detail: StringProperty()            # longer text some rows carry (the keys behind a collapsed summary)


classes = (SLICEWRIGHT_PG_Issue,)
WM_LIST, WM_INDEX = f"{PACKAGE_ID}_issues", f"{PACKAGE_ID}_issues_index"


# -- reading -----------------------------------------------------------------------------------------------

def current() -> list[dict]:
    """The merged list (do not mutate)."""
    return registry.state.plate_issues


def scene_name() -> str | None:
    return _scene[0]


def for_scene(scene) -> list[dict]:
    """The merged list if it belongs to ``scene``, else empty."""
    return current() if scene is not None and scene.name == _scene[0] else []


# -- writing -----------------------------------------------------------------------------------------------

def set_source(scene, source: str, issues) -> None:
    """Replace one source's issues (``slice`` or ``check``) and refresh the list. A different scene than the
    list's current one starts the list afresh."""
    if scene is not None and _scene[0] != scene.name:
        _book.clear()
        _scene[0] = scene.name
    _book.set(source, issues)
    _publish()


def clear(*sources: str) -> None:
    """Drop ``sources`` (all when none is named) and refresh the list."""
    _book.clear(*sources)
    if not sources:
        _scene[0] = None
    _publish()


def _publish() -> None:
    merged = _book.merged()
    registry.state.plate_issues[:] = merged
    wm = _wm()
    if wm is None or not hasattr(wm, WM_LIST):
        return
    items = getattr(wm, WM_LIST)
    items.clear()
    for i in merged:
        item = items.add()
        item.level, item.code, item.message = i.get("level", ""), i.get("code", ""), i.get("message", "")
        item.opt_key, item.object_name = i.get("opt_key") or "", i.get("object_name") or ""
        item.detail = i.get("detail") or ""
    setattr(wm, WM_INDEX, min(max(getattr(wm, WM_INDEX), 0), max(len(merged) - 1, 0)))
    _tag_redraw()


def _wm():
    try:
        return bpy.context.window_manager
    except AttributeError:               # restricted context while the add-on registers
        return None


def _tag_redraw() -> None:
    wm = _wm()
    for win in (wm.windows if wm is not None else ()):
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


# -- lifecycle ---------------------------------------------------------------------------------------------

def _on_file_load() -> None:
    clear()


def register() -> None:
    registry.register_classes(classes)
    setattr(bpy.types.WindowManager, WM_LIST, CollectionProperty(type=SLICEWRIGHT_PG_Issue))
    setattr(bpy.types.WindowManager, WM_INDEX, IntProperty(options={"SKIP_SAVE"}))
    for hooks in (registry.state.load_pre_hooks, registry.state.load_post_hooks):
        if _on_file_load not in hooks:
            hooks.append(_on_file_load)
    clear()


def unregister() -> None:
    for hooks in (registry.state.load_pre_hooks, registry.state.load_post_hooks):
        while _on_file_load in hooks:
            hooks.remove(_on_file_load)
    _book.clear()
    _scene[0] = None
    for name in (WM_LIST, WM_INDEX):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    registry.unregister_classes(classes)
    registry.state.plate_issues.clear()
