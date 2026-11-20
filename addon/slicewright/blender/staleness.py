# SPDX-License-Identifier: GPL-3.0-or-later
"""Is the preview still the plate's slice? (03 section 6.3)

While a slice result exists, anything that could change the slice's input key sets ``Scene.slicewright.stale``
when the key of the *current* inputs differs from ``last_cache_key``. Nothing is hashed in an event: events only
mark a scene dirty, and one timer, throttled to ``INTERVAL_S``, recomputes the key and compares.

What marks a scene dirty:

* the ``depsgraph_update_post`` handler, for plate objects (transform, geometry, modifiers, per-object
  properties), the meshes they use, collection changes, and a changed plate signature (members, visibility,
  unit scale). Objects of the scene outside the plate are ignored;
* property ``update`` callbacks (:func:`inputs_changed`) on everything that feeds the key: the generated config
  groups (printer and process edit buffers, slot and object overrides), the preset ids, the filament slots, the
  per-object properties and the plate collection. Writing a custom property does not tag the depsgraph [V],
  and ``stale``, ``job_*``, ``last_cache_key``, the preview settings and the view settings have no such callback,
  so the add-on's own writes never mark anything;
* ``undo_post`` / ``redo_post`` (undo restores properties without calling their callbacks);
* the callers of ``property_unset`` and slot add/remove, through :func:`mark`.

A check re-extracts the plate only when an object-level event happened (the per-object keys are cached); a
settings-only event recomposes the config and hashes it with the cached object keys. Checks are skipped while a
slice runs; its completion starts a full one (:func:`after_slice`), so edits made meanwhile are caught.

Auto re-slice (preference, off by default, 03 section 6.3): every edit that marks a scene restarts a 1.5 s
debounce timer; when it fires, a scene that is stale, active, with no slice running, is sliced again.
"""
from __future__ import annotations

import time

import bpy

from ..core import logs
from ..core.checks import Throttle
from ..engine import jobs
from . import registry

REQUIRES_ENGINE = True
INTERVAL_S = 0.3             # a check runs at most this often
MIN_DELAY_S = 0.1            # and not before this long after the first event: a drag becomes one check, not many
AUTO_DEBOUNCE_S = 1.5        # auto re-slice waits this long after the last edit


class _Runtime:
    def __init__(self) -> None:
        self.active = False
        self.pending: dict[str, bool] = {}      # scene name -> the plate objects must be re-extracted
        self.object_keys: dict[str, list[str]] = {}   # scene name -> cached per-object keys
        self.plate: dict[str, tuple] = {}       # scene name -> (object names, data names, signature)


_rt = _Runtime()
throttle = Throttle(INTERVAL_S, time.monotonic)
# Counters for tests: events seen, checks run, plate extractions, key computations.
stats = {"handler_calls": 0, "checks": 0, "extractions": 0, "key_computes": 0}
log = logs.get_logger("staleness")


def reset_stats() -> None:
    for k in stats:
        stats[k] = 0


# -- when a check is wanted -------------------------------------------------------------------------------

def has_result(scene) -> bool:
    """A slice result is showing (or at least held) for ``scene``: there is something to be stale."""
    from .preview import runtime
    sw = getattr(scene, "slicewright", None)
    return (sw is not None and bool(sw.last_cache_key)
            and (sw.job_state == jobs.DONE or runtime.get(scene) is not None))


def _watching(scene) -> bool:
    """Events matter only with a result to compare against and no slice running (its end starts a full check)."""
    sw = getattr(scene, "slicewright", None)
    if not _rt.active or sw is None or sw.job_state in jobs.ACTIVE_STATES or not has_result(scene):
        if scene is not None:
            _rt.object_keys.pop(scene.name, None)
            _rt.plate.pop(scene.name, None)
            _rt.pending.pop(scene.name, None)
        return False
    return True


def mark(scene=None, *, objects: bool = False) -> None:
    """Ask for a check of ``scene`` (default: the active scene); ``objects`` re-extracts the plate."""
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None or not _watching(scene):
        return
    _rt.pending[scene.name] = _rt.pending.get(scene.name, False) or objects
    _arm()
    _arm_auto()


def inputs_changed(self, context) -> None:
    """``update=`` callback for every property that feeds the slice key. Marks only; never computes."""
    mark(objects=isinstance(getattr(self, "id_data", None), bpy.types.Object))


def plate_changed(self, context) -> None:
    """``update=`` callback for the plate collection pointer: which objects are sliced changed."""
    mark(objects=True)


def after_slice(scene) -> None:
    """A slice finished: forget the cached object keys and confirm that its inputs are still the plate's."""
    _rt.object_keys.pop(scene.name, None)
    _rt.plate.pop(scene.name, None)
    mark(scene, objects=True)


def forget(scene) -> None:
    """The scene's preview is gone: nothing is stale."""
    for table in (_rt.object_keys, _rt.plate, _rt.pending):
        table.pop(scene.name, None)
    sw = getattr(scene, "slicewright", None)
    if sw is not None and sw.stale:
        sw.stale = False


def _arm() -> None:
    registry.register_timer(_tick, first_interval=max(throttle.remaining(), MIN_DELAY_S))


def _auto_enabled() -> bool:
    from .. import prefs
    return bool(prefs.get().auto_reslice)


def _arm_auto() -> None:
    """(Re)start the auto re-slice debounce; nothing is armed while the preference is off."""
    if not _auto_enabled():
        return
    if bpy.app.timers.is_registered(_auto_tick):
        bpy.app.timers.unregister(_auto_tick)            # a newer edit restarts the wait
    registry.register_timer(_auto_tick, first_interval=AUTO_DEBOUNCE_S)


def _auto_tick() -> None:
    """The debounce fired: slice again if the active scene's preview is stale and no job runs. Never while one
    does (the end of that slice re-checks, and re-arms this if the preview is stale again)."""
    from . import slicing
    scene = getattr(bpy.context, "scene", None)
    sw = getattr(scene, "slicewright", None)
    if (not _rt.active or sw is None or not _auto_enabled() or slicing.is_live()
            or sw.job_state in jobs.ACTIVE_STATES):
        return None
    if _rt.pending.get(scene.name) is not None:
        check_now(scene, objects=_rt.pending.pop(scene.name))      # an edit the throttle has not checked yet
    if sw.stale and has_result(scene):
        log.info("auto re-slice")
        slicing.start(bpy.context)
    return None


def settle(scene) -> bool:
    """Run the check an edit has queued but the throttle has not yet run, so ``stale`` is what the plate says *now*
    (an export decides on it). Returns ``stale``."""
    pending = _rt.pending.pop(scene.name, None)
    if pending is not None:
        check_now(scene, objects=pending)
    sw = getattr(scene, "slicewright", None)
    return bool(sw is not None and sw.stale)


def _tick() -> None:
    """The timer: run the pending checks once. Always stops itself; a new event arms it again."""
    pending, _rt.pending = _rt.pending, {}
    throttle.mark()
    for name, objects in pending.items():
        scene = bpy.data.scenes.get(name)
        if scene is None or scene != getattr(bpy.context, "scene", None):
            continue                     # a scene nobody looks at is rechecked when it next changes
        try:
            check_now(scene, objects=objects)
        except Exception:  # noqa: BLE001 - a check must never break editing; the flag just stays as it was
            log.exception("staleness check failed")
    return None


# -- the check ----------------------------------------------------------------------------------------------

def current_key(scene, *, objects: bool) -> str | None:
    """The slice key of the scene's current inputs, or ``None`` if they cannot be sliced (a preset is missing,
    a value is invalid). ``objects`` forces a re-extraction; otherwise cached object keys are used."""
    from . import extract, slice_inputs
    sw = scene.slicewright
    keys = _rt.object_keys.get(scene.name)
    if keys is None or objects:
        stats["extractions"] += 1
        keys = [o.key for o in extract.extract_plate(bpy.context).objects]
        _rt.object_keys[scene.name] = keys
        _rt.plate[scene.name] = _plate_info(scene)
    try:
        printer, process, filaments = slice_inputs.gather_presets(sw)
    except slice_inputs.InputError:
        return None
    sc = registry.state.status.module
    config = jobs.normalized_config(sc, printer, process, filaments, slice_inputs.project_overrides(printer, filaments))
    stats["key_computes"] += 1
    return None if config is None else jobs.slice_key(sc, config, keys)


def check_now(scene, *, objects: bool = False) -> bool | None:
    """Compare the current inputs' key with ``last_cache_key`` now and set ``stale`` accordingly. Returns the new
    value, or ``None`` when there is no result to compare against."""
    if not _watching(scene):
        return None
    stats["checks"] += 1
    sw = scene.slicewright
    stale = current_key(scene, objects=objects) != sw.last_cache_key
    if sw.stale != stale:
        sw.stale = stale
        from .preview import runtime
        runtime.tag_redraw()
    return stale


# -- the depsgraph handler ----------------------------------------------------------------------------------

def _plate_info(scene) -> tuple:
    """``(plate object names, their data names, signature)``: the signature changes when the plate's members,
    their visibility or the unit scale change (all of which change what a slice would extract)."""
    from . import plate_collection
    obs = plate_collection.plate_objects(scene)
    view_layer = getattr(bpy.context, "view_layer", None)
    names = {o.name_full for o in obs}
    data = {o.data.name_full for o in obs if o.data is not None}
    flags = tuple(sorted((o.name_full, o.hide_viewport, bool(view_layer and o.hide_get(view_layer=view_layer)))
                         for o in obs))
    return names, data, (flags, scene.unit_settings.scale_length)


def on_depsgraph_update(scene, depsgraph) -> None:
    """Classify the update; mark the scene dirty if it touched the plate. No hashing, no extraction."""
    stats["handler_calls"] += 1
    if not _watching(scene):
        return
    info = _rt.plate.get(scene.name)
    if info is None:
        info = _rt.plate[scene.name] = _plate_info(scene)
    objects = check_plate = False
    for update in depsgraph.updates:
        id_ = update.id
        if isinstance(id_, bpy.types.Object):
            objects = objects or id_.original.name_full in info[0]
        elif isinstance(id_, bpy.types.Collection):
            objects = True                           # membership or visibility of a collection changed
        elif isinstance(id_, bpy.types.Scene):
            check_plate = True
        elif id_.id_type in ("MESH", "CURVE", "CURVES", "SURFACE", "META", "FONT"):
            objects = objects or id_.original.name_full in info[1]
    if check_plate and not objects:
        objects = _plate_info(scene)[2] != info[2]
    if objects:
        mark(scene, objects=True)


def on_undo_redo(*_args) -> None:
    """Undo and redo restore properties and objects without update callbacks: check everything."""
    scene = getattr(bpy.context, "scene", None)
    if scene is not None:
        _rt.object_keys.pop(scene.name, None)
        mark(scene, objects=True)


# -- lifecycle ----------------------------------------------------------------------------------------------

def _drop_everything() -> None:
    _rt.pending.clear()
    _rt.object_keys.clear()
    _rt.plate.clear()


def _on_file_load() -> None:
    """The cached keys belong to the old file; the scenes' ``stale`` flags are reset by ``props.reset_runtime``."""
    _drop_everything()
    for fn in (_tick, _auto_tick):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)


def register() -> None:
    _rt.active = True
    reset_stats()
    for hooks in (registry.state.load_pre_hooks, registry.state.load_post_hooks):
        if _on_file_load not in hooks:
            hooks.append(_on_file_load)


def unregister() -> None:
    _rt.active = False
    for hooks in (registry.state.load_pre_hooks, registry.state.load_post_hooks):
        while _on_file_load in hooks:
            hooks.remove(_on_file_load)
    _drop_everything()
    for fn in (_tick, _auto_tick):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
