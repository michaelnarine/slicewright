# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep the plate's source objects out of the viewport while the Preview is shown (Preview option "Model").

The rules are in ``core/preview_model.py``: Hidden and Ghost switch off the 3D views' per-type object visibility
filters (``show_object_viewport_mesh`` and friends) for the types the plate holds, and write nothing to any object,
collection or view layer, so slicing, staleness, Arrange, selection and undo are unaffected. The filters are UI
state saved in the .blend, so what we switched is **recorded** (a runtime set, which survives undo, and a string on
each scene's preview settings, which survives a crash) and put back

* when the scene leaves Preview, the option changes, or the preview is cleared (``sync``),
* before a file is saved and re-applied after (``save_pre`` / ``save_post``): a saved file never holds our state,
* when the add-on is unregistered (``release``),
* after a file load (``load_post``), for a file that holds a stale record (a crash recovery, an autosave).

Only filters we switched off are switched back on: a filter the user turned off stays off. A 3D view opened while
the Preview is shown starts with the filters of the view it was split from; the preview draw handler notices a view in
the wrong state and asks for a ``sync`` on a timer (writing UI state from inside a draw callback is not safe).
"""
from __future__ import annotations

import bpy

from ..core import logs
from ..core import preview_model as pm
from . import plate_collection, registry
from .preview import runtime

log = logs.get_logger("model_visibility")

_record: set[str] = set()                    # keys we switched off (survives undo; the scene string does not)
_applied: dict[str, tuple] = {}              # scene name -> (hide, props) last applied, for the cheap draw check
_dirty = False                               # plate types may have changed: re-plan at the next chance
_scheduled = False


# -- reading the state -------------------------------------------------------------------------------------------

def _spaces():
    """Every 3D view space of every screen: ``(screen name, area index, space index, space)``."""
    for screen in bpy.data.screens:
        for ai, area in enumerate(screen.areas):
            if area.type != "VIEW_3D":
                continue
            for si, space in enumerate(area.spaces):
                if space.type == "VIEW_3D":
                    yield screen.name, ai, si, space


def _state() -> dict[str, bool]:
    out = {}
    for sname, ai, si, space in _spaces():
        for prop in pm.TYPE_FILTERS.values():
            out[pm.space_key(sname, ai, si, prop)] = bool(getattr(space, prop, True))
    return out


def _space_by_key() -> dict:
    return {(s, a, i): sp for s, a, i, sp in _spaces()}


def _set(key: str, value: bool) -> None:
    screen, area, space, prop = key.split("|")
    target = _space_by_key().get((screen, int(area), int(space)))
    if target is not None:
        setattr(target, prop, value)


def _scene_groups():
    for scene in bpy.data.scenes:
        sw = getattr(scene, "slicewright", None)
        if sw is not None:
            yield scene, sw


def _stored_record() -> set[str]:
    keys: set[str] = set()
    for _scene, sw in _scene_groups():
        keys |= pm.decode_record(getattr(sw.preview, "model_record", ""))
    return keys


def _store_record(keys: set[str]) -> None:
    text = pm.encode_record(keys) if keys else ""
    for _scene, sw in _scene_groups():
        if sw.preview.model_record != text:
            sw.preview.model_record = text


def desired(scene) -> tuple[bool, tuple[str, ...]]:
    """``(hide, filter props)`` for ``scene``: whether its views keep the model out, and which types."""
    sw = getattr(scene, "slicewright", None)
    if sw is None:
        return False, ()
    hide = pm.wants_hide(sw.preview.model_display, sw.mode, runtime.get(scene) is not None)
    if not hide:
        return False, ()
    return True, pm.filter_props(o.type for o in plate_collection.plate_objects(scene))


# -- applying ----------------------------------------------------------------------------------------------------

def sync() -> None:
    """Bring every 3D view in line with what its scene wants, switching back only what we switched off."""
    global _dirty
    _dirty = False
    try:
        wm = bpy.context.window_manager
        windows = list(wm.windows) if wm is not None else []
    except Exception:  # noqa: BLE001 - no window manager (a script running before one exists)
        windows = []
    eligible: set[str] = set()
    wanted: set[str] = set()
    applied: dict[str, tuple] = {}
    for win in windows:
        hide, props = desired(win.scene)
        applied[win.scene.name] = (hide, props)
        if hide:
            eligible.add(win.screen.name)
            wanted.update(props)
    _applied.clear()
    _applied.update(applied)
    record = _record | _stored_record()
    state = _state()
    off, on, keep = pm.plan(state, eligible, wanted, bool(eligible), record)
    for key in off:
        _set(key, False)
    for key in on:
        _set(key, True)
    _record.clear()
    _record.update(keep)
    _store_record(keep)
    if off or on:
        runtime.tag_redraw()


def restore_all() -> None:
    """Put back every filter we switched off, in every view. Idempotent."""
    global _dirty
    record = _record | _stored_record()
    state = _state()
    _off, on, _keep = pm.plan(state, (), (), False, record)
    for key in on:
        _set(key, True)
    _record.clear()
    _applied.clear()
    _store_record(set())
    _dirty = True                                      # a later sync() must re-plan
    if on:
        runtime.tag_redraw()


def safe_sync() -> None:
    try:
        sync()
    except Exception:  # noqa: BLE001 - never break a mode switch, a draw or a handler
        log.exception("model visibility sync failed")


def safe_restore() -> None:
    try:
        restore_all()
    except Exception:  # noqa: BLE001
        log.exception("model visibility restore failed")


# -- the hooks ---------------------------------------------------------------------------------------------------

def on_save_pre() -> None:
    """A saved file never holds our state."""
    safe_restore()


def on_save_post() -> None:
    safe_sync()


def on_depsgraph_update(scene, depsgraph) -> None:
    """The plate may have gained a type (a text object, a curve): re-plan on a timer. Only while hiding."""
    global _dirty
    if _record and depsgraph is not None and depsgraph.id_type_updated("OBJECT"):
        _dirty = True
        _schedule()


def _on_load_pre() -> None:
    from .preview import ghost
    _record.clear()
    _applied.clear()
    ghost.release()


def _on_load_post() -> None:
    """The new file's screens may hold a stale record (a crash, an autosave): put it back, then apply the scene's
    own state (a file saved in Preview comes back in Preview while its slice loads)."""
    safe_restore()
    safe_sync()


def _schedule() -> None:
    global _scheduled
    if _scheduled or bpy.app.background:
        return
    _scheduled = True
    bpy.app.timers.register(_deferred, first_interval=0.0)


def _deferred():
    global _scheduled
    _scheduled = False
    safe_sync()
    return None


def check_draw(context) -> None:
    """Called at the top of the preview draw handler: a 3D view in the wrong state (split from a hidden one, a workspace
    switched to, a slice finished) asks for a sync on a timer. Cheap: no object walk."""
    scene = context.scene
    sw = getattr(scene, "slicewright", None)
    if sw is None:
        return
    hide = pm.wants_hide(sw.preview.model_display, sw.mode, runtime.get(scene) is not None)
    was_hide, props = _applied.get(scene.name, (False, ()))
    space = context.space_data
    wrong = hide != was_hide or _dirty
    if not wrong and hide and space is not None:
        wrong = any(getattr(space, p, False) for p in props)
    if not wrong and not hide and _record and space is not None:
        wrong = any(not getattr(space, p, True) for p in pm.TYPE_FILTERS.values())
    if wrong:
        _schedule()


def ghost_wanted(scene) -> bool:
    sw = getattr(scene, "slicewright", None)
    return (sw is not None and sw.mode == "PREVIEW" and sw.preview.model_display == pm.GHOST)


def register() -> None:
    global _dirty
    _dirty = False
    if _on_load_pre not in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.append(_on_load_pre)
    if _on_load_post not in registry.state.load_post_hooks:
        registry.state.load_post_hooks.append(_on_load_post)


def unregister() -> None:
    """Add-on removal: the viewports are exactly as the user had them."""
    global _scheduled
    from .preview import ghost
    while _on_load_pre in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.remove(_on_load_pre)
    while _on_load_post in registry.state.load_post_hooks:
        registry.state.load_post_hooks.remove(_on_load_post)
    if _scheduled and bpy.app.timers.is_registered(_deferred):
        bpy.app.timers.unregister(_deferred)
    _scheduled = False
    safe_restore()
    ghost.release()
