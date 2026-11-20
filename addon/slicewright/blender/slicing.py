# SPDX-License-Identifier: GPL-3.0-or-later
"""Slice orchestration on the Blender side (03 section 6.1): one ``JobRunner``, one poll timer.

``start`` gathers the request from the scene, hands it to the runner and arms ``poll_job``, a persistent
``bpy.app.timers`` callback that polls every ``POLL_S`` while a job lives and then stops itself. Every status
the runner returns is applied to ``Scene.slicewright`` (``job_*``), the status bar and the window manager's
progress; a finished slice flows into ``preview.handler.show_result``. Timers never fire in ``-b``, so tests call
``poll_job`` directly (03 section 7.9a). A file load (``load_pre``) cancels the live job and drops its outcome.

This module is a registration stage: it owns the load_pre hook, the keymap, and the runner's lifetime.
"""
from __future__ import annotations

import logging

import bpy

from ..core import logs
from ..engine import adapter, jobs
from ..names import PACKAGE_ID
from . import issues, registry, staleness

REQUIRES_ENGINE = True
POLL_S = 0.1
log = logging.getLogger("slicewright.slicing")


class _Runtime:
    def __init__(self) -> None:
        self.runner: jobs.JobRunner | None = None
        self.progress_open = False              # WindowManager.progress_begin is in effect
        self.last: jobs.JobStatus = jobs.JobStatus()   # the status last applied (for change detection)


_rt = _Runtime()
_keymaps: list = []


def runner() -> jobs.JobRunner:
    """The process-wide runner (one job per process, 03 section 6.1), created on first use."""
    if _rt.runner is None:
        _rt.runner = jobs.JobRunner(registry.state.status.module)
    return _rt.runner


def is_live() -> bool:
    return _rt.runner is not None and _rt.runner.live


# -- starting, polling, cancelling -------------------------------------------------------------------------

def start(context) -> jobs.JobStatus:
    """Slice the plate of ``context.scene``: gather, hand to the runner, arm the timer. A new slice cancels a
    running one. Never raises for slicing problems: they are in the returned (and applied) status."""
    from . import slice_inputs
    from .. import prefs
    scene = context.scene
    issues.clear()                                    # the Issues list belongs to one slice: it starts empty
    try:
        request = slice_inputs.build_request(context, threads=int(prefs.get().threads))
    except slice_inputs.InputError as exc:
        status = jobs.JobStatus(jobs.FAILED, message=str(exc), finished=True, tag=scene.name,
                                error=adapter.ErrorReport("InputError", "ERROR", str(exc), "none"))
        apply_status(status)
        return status
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
    """03 section 6.1: a file load cancels any live job; its outcome belongs to the old file and is dropped."""
    if _rt.runner is not None:
        _rt.runner.cancel(discard=True)
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
        try:
            handler.show_result(scene, status.result)
        except Exception as exc:  # noqa: BLE001 - the slice itself succeeded; say what failed instead of losing it
            log.exception("loading the preview failed")
            sw.job_message = f"Sliced, but the preview failed: {exc}"
        staleness.after_slice(scene)            # edits made while it ran make the new preview stale at once
        log.info("sliced %s (%d issue(s))", status.key, len(status.issues))
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
    _register_keymap()


def unregister() -> None:
    _unregister_keymap()
    while _on_load_pre in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.remove(_on_load_pre)
    if bpy.app.timers.is_registered(poll_job):
        bpy.app.timers.unregister(poll_job)
    if _rt.runner is not None:
        _rt.runner.cancel(discard=True)       # dropping the runner drops the job, which frees the engine lock
    _rt.runner = None
    _rt.last = jobs.JobStatus()
    _end_progress()
    _set_status_text(None)
