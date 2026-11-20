# SPDX-License-Identifier: GPL-3.0-or-later
"""Slice orchestration on the Blender side (03 section 6.1): one ``JobRunner``, one poll timer.

``start`` gathers the request from the scene, hands it to the runner and arms ``poll_job``, a persistent
``bpy.app.timers`` callback that polls every ``POLL_S`` while a job lives and then stops itself. Every status
the runner returns is applied to ``Scene.slicewright`` (``job_*``), the status bar and the window manager's
progress; a finished slice flows into ``preview.handler.show_result``. Timers never fire in ``-b``, so tests call
``poll_job`` directly (03 section 7.9a). A file load (``load_pre``) cancels the live job and drops its outcome.

This module is a registration stage: it owns the load_pre hook, the keymap, and the runner's lifetime.

The result cache (03 section 6.4, ``engine/cache.py``) hangs off it: a finished slice is written to disk one array per
timer tick; a slice whose key the cache holds never reaches the engine (the runner reports ``done`` with the entry and
the preview loads from disk, again one array per tick); and ``load_post`` reconnects the saved ``last_cache_key`` to
its entry so a reopened file shows its preview without slicing.
"""
from __future__ import annotations

import logging

import bpy

from ..core import logs
from ..engine import adapter, jobs
from ..engine.cache import MB, ResultCache
from ..names import PACKAGE_ID
from . import issues, paths, recovery, registry, staleness, timers

REQUIRES_ENGINE = True
POLL_S = 0.1
log = logging.getLogger("slicewright.slicing")


class _Runtime:
    def __init__(self) -> None:
        self.runner: jobs.JobRunner | None = None
        self.progress_open = False              # WindowManager.progress_begin is in effect
        self.last: jobs.JobStatus = jobs.JobStatus()   # the status last applied (for change detection)
        self.cache: ResultCache | None = None   # the result cache for the current root (see ``result_cache``)
        self.notes: dict[str, str] = {}         # scene name -> a gentle one-line note for the Slice panel


_rt = _Runtime()
_keymaps: list = []


def runner() -> jobs.JobRunner:
    """The process-wide runner (one job per process, 03 section 6.1), created on first use."""
    if _rt.runner is None:
        _rt.runner = jobs.JobRunner(registry.state.status.module)
    return _rt.runner


def is_live() -> bool:
    return _rt.runner is not None and _rt.runner.live


# -- the result cache ---------------------------------------------------------------------------------------

def result_cache() -> ResultCache | None:
    """The slice result cache under the user's cache directory, capped by the preference; ``None`` when the
    preference is 0 or the directory is unavailable (slicing works without it)."""
    from .. import prefs
    cap = int(prefs.get().cache_cap_mb) * MB
    if cap <= 0:
        return None
    root = paths.user_dir("cache/slices", create=False)
    if root is None:
        return None
    if _rt.cache is None or _rt.cache.root != root:
        _rt.cache = ResultCache(root, cap)
    _rt.cache.cap_bytes = cap
    return _rt.cache


def note(scene) -> str:
    """The gentle note for ``scene`` ("the cached slice is gone"), or an empty string."""
    return _rt.notes.get(scene.name, "")


def _set_note(scene, text: str | None) -> None:
    if text:
        _rt.notes[scene.name] = text
    else:
        _rt.notes.pop(scene.name, None)
    _tag_redraw()


def _store_name(key: str) -> str:
    return f"cache-write:{key}"


def _load_name(scene) -> str:
    return f"cache-load:{scene.name}"


def _cache_write(key: str, result) -> None:
    """Write ``result`` to the cache, one array per tick. Failures are logged: the slice itself succeeded."""
    cache = result_cache()
    if cache is None or getattr(result, "from_cache", False):
        return

    def failed(exc: BaseException) -> None:
        log.warning("could not cache the slice %s: %s", key, exc)

    timers.runner.submit(_store_name(key), cache.store(key, result), on_error=failed, replace=True)


def _begin_load(scene, entry, *, restored: bool) -> None:
    """Load the cache ``entry`` into the preview of ``scene``, one array per tick. ``restored`` is a file reload
    (it also reports it in ``job_*``); otherwise a Slice that hit the cache. A failing load slices instead (Slice)
    or leaves a note (reload)."""
    name, key = scene.name, entry.key
    _set_status_text("Loading the cached slice…")

    def done(result) -> None:
        _set_status_text(None)
        target = bpy.data.scenes.get(name)
        sw = getattr(target, PACKAGE_ID, None) if target is not None else None
        if sw is None or sw.last_cache_key != key:      # another slice or file replaced it while it loaded
            return
        from .preview import handler
        try:
            handler.show_result(target, result)
        except Exception as exc:  # noqa: BLE001 - say what failed instead of losing the slice
            log.exception("loading the cached preview failed")
            sw.job_message = f"Loaded from cache, but the preview failed: {exc}"
            return
        if restored:
            _set(sw, "job_state", jobs.DONE)
            _set(sw, "job_progress", 1.0)
            _set(sw, "job_message", "Restored from cache")
        _set_note(target, None)
        staleness.after_slice(target)               # inputs edited since: the restored preview is stale at once
        log.info("%s preview of %s from the cache", "restored" if restored else "loaded", key)

    def failed(exc: BaseException) -> None:
        _set_status_text(None)
        log.warning("cached slice %s unreadable: %s", key, exc)
        cache = result_cache()
        if cache is not None:
            cache.discard(key)                        # so the next slice replaces it instead of failing on it again
        target = bpy.data.scenes.get(name)
        if target is None:
            return
        if restored:
            _set_note(target, "The cached slice could not be read. Slice again to see the preview")
        else:
            start(bpy.context, use_cache=False)       # the entry is bad: do the work

    timers.runner.submit(_load_name(scene), entry.load_steps(), on_done=done, on_error=failed, replace=True)


def _on_load_post() -> None:
    """03 section 6.4 reconnect: the saved ``last_cache_key`` brings the preview back from the cache, without
    slicing. A missing entry leaves the preview empty and a note on the panel. (Runs after ``props`` has reset
    the job state and the stale flag.)"""
    _rt.notes.clear()
    cache = result_cache()
    for scene in bpy.data.scenes:
        sw = getattr(scene, PACKAGE_ID, None)
        key = sw.last_cache_key if sw is not None else ""
        if not key:
            continue
        entry = cache.lookup(key) if cache is not None else None
        if entry is None:
            log.info("no cached slice for %s", key)
            _set_note(scene, "The cached slice for this file is gone. Slice again to see the preview")
            continue
        _begin_load(scene, entry, restored=True)


# -- starting, polling, cancelling -------------------------------------------------------------------------

def start(context, *, use_cache: bool = True) -> jobs.JobStatus:
    """Slice the plate of ``context.scene``: gather, hand to the runner, arm the timer. A new slice cancels a
    running one. A key the result cache holds is not sliced: the status is ``done`` at once and the preview loads
    from disk. Never raises for slicing problems: they are in the returned (and applied) status."""
    from . import slice_inputs
    from .. import prefs
    scene = context.scene
    issues.clear()                                    # the Issues list belongs to one slice: it starts empty
    timers.runner.cancel(_load_name(scene))           # a cached preview still loading is superseded
    _set_note(scene, None)
    try:
        request = slice_inputs.build_request(context, threads=int(prefs.get().threads))
        request.use_cache = use_cache
    except slice_inputs.InputError as exc:
        status = jobs.JobStatus(jobs.FAILED, message=str(exc), finished=True, tag=scene.name,
                                error=adapter.ErrorReport("InputError", "ERROR", str(exc), "none"))
        apply_status(status)
        return status
    recovery.maybe_save()                             # 03 section 6.1: insurance before the engine runs, throttled
    runner().cache = result_cache()
    status = runner().start(request)
    apply_status(status)
    if is_live():
        registry.register_timer(poll_job, first_interval=POLL_S)
    return status


def poll_job() -> float | None:
    """The timer callback: poll the runner, apply the status. Returns the next delay, or ``None`` to stop."""
    try:
        r = _rt.runner
        if r is None:
            return None
        apply_status(r.poll())
        return POLL_S if r.live else None
    except Exception:  # noqa: BLE001 - an exception would silently unregister the timer and strand the UI
        log.exception("slice poll failed")
        _rt.runner = None
        _end_progress()
        return None


def cancel() -> bool:
    """Cancel the running job (the outcome arrives through ``poll_job``). False if nothing runs."""
    if _rt.runner is None or not _rt.runner.cancel():
        return False
    apply_status(_rt.runner.status)
    return True


def _on_load_pre() -> None:
    """03 section 6.1: a file load cancels any live job; its outcome belongs to the old file and is dropped. A
    cached preview still loading for the old file is dropped too (a cache write carries on: it is keyed by content)."""
    if _rt.runner is not None:
        _rt.runner.cancel(discard=True)
    for task in timers.runner.pending():
        if task.name.startswith("cache-load:"):
            timers.runner.cancel(task)
    _end_progress()
    _set_status_text(None)


# -- applying a status to Blender ---------------------------------------------------------------------------

def apply_status(status: jobs.JobStatus) -> None:
    """Mirror ``status`` into the scene properties, the status bar and the progress cursor; on the snapshot where a
    job finished, hand over the result (``done``) or record the issues."""
    scene = bpy.data.scenes.get(status.tag) if status.tag else None
    sw = getattr(scene, PACKAGE_ID, None) if scene is not None else None
    if sw is not None:
        _set(sw, "job_state", status.state)
        _set(sw, "job_progress", round(status.progress, 2))
        _set(sw, "job_message", status.text)
    if status.active:
        _update_progress(status)
        _set_status_text(status.text)
    else:
        _end_progress()
        _set_status_text(None)
    if status.finished and scene is not None:
        _finish(scene, sw, status)
    _rt.last = status
    _tag_redraw()


def _set(sw, name: str, value) -> None:
    """Write only on change: a write redraws the panels (and fires the property's update callback, if it has one)."""
    if getattr(sw, name) != value:
        setattr(sw, name, value)


def _finish(scene, sw, status: jobs.JobStatus) -> None:
    issues.clear("check")                       # the slice ran its own pre-checks; they are in status.issues
    issues.set_source(scene, "slice", status.issues)
    if status.state == jobs.DONE:
        from .preview import handler
        sw.last_cache_key = status.key
        sw.stale = False
        sw.mode = "PREVIEW"
        if status.from_cache:
            _begin_load(scene, status.cached, restored=False)      # the preview arrives array by array
        else:
            try:
                handler.show_result(scene, status.result)
            except Exception as exc:  # noqa: BLE001 - the slice itself succeeded; say what failed instead of losing it
                log.exception("loading the preview failed")
                sw.job_message = f"Sliced, but the preview failed: {exc}"
            _cache_write(status.key, status.result)
        staleness.after_slice(scene)            # edits made while it ran make the new preview stale at once
        log.info("%s %s (%d issue(s))", "cache hit" if status.from_cache else "sliced", status.key,
                 len(status.issues))
    elif status.state == jobs.FAILED:
        logs.get_logger("slicing").warning("slice failed: %s", status.message)


def _update_progress(status: jobs.JobStatus) -> None:
    wm = bpy.context.window_manager
    if wm is None:
        return
    if not _rt.progress_open:
        wm.progress_begin(0, 100)
        _rt.progress_open = True
    wm.progress_update(int(status.progress * 100))


def _end_progress() -> None:
    if _rt.progress_open:
        _rt.progress_open = False
        wm = bpy.context.window_manager
        if wm is not None:
            wm.progress_end()


def _set_status_text(text: str | None) -> None:
    workspace = getattr(bpy.context, "workspace", None)
    if workspace is not None:
        workspace.status_text_set(text or None)


def _tag_redraw() -> None:
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:  # noqa: BLE001 - background mode, or no window manager yet
        pass


# -- registration ------------------------------------------------------------------------------------------

def _register_keymap() -> None:
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:                       # background mode has no add-on keyconfig
        return
    km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
    _keymaps.append((km, km.keymap_items.new(f"{PACKAGE_ID}.slice", "R", "PRESS", ctrl=True, shift=True)))


def _unregister_keymap() -> None:
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (RuntimeError, ReferenceError):
            pass
    _keymaps.clear()


def register() -> None:
    if _on_load_pre not in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.append(_on_load_pre)
    if _on_load_post not in registry.state.load_post_hooks:
        registry.state.load_post_hooks.append(_on_load_post)
    _register_keymap()


def unregister() -> None:
    _unregister_keymap()
    while _on_load_pre in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.remove(_on_load_pre)
    while _on_load_post in registry.state.load_post_hooks:
        registry.state.load_post_hooks.remove(_on_load_post)
    _rt.cache = None
    _rt.notes.clear()
    recovery.reset()
    if bpy.app.timers.is_registered(poll_job):
        bpy.app.timers.unregister(poll_job)
    if _rt.runner is not None:
        _rt.runner.cancel(discard=True)       # dropping the runner drops the job, which frees the engine lock
    _rt.runner = None
    _rt.last = jobs.JobStatus()
    _end_progress()
    _set_status_text(None)
