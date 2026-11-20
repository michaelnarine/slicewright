# SPDX-License-Identifier: GPL-3.0-or-later
"""Exporting the last slice (03 section 8.1): where its G-code comes from, its default name, and the write.

Which result is exported is the one the preview shows (``Scene.slicewright.last_cache_key``):

* ``live``: the engine's own ``SliceResult`` (``rt.result``), straight after a slice. It has ``write_gcode`` and
  ``write_gcode_3mf``;
* ``cached``: a :class:`~slicewright.engine.cache.CachedResult` after a cache hit or a reloaded file. It has
  ``write_gcode`` (a copy of the cached ``out.gcode``) but no ``write_gcode_3mf``, which needs the live result;
* ``entry``: the cache's on-disk entry for the key, when the preview itself is not loaded (it failed to load, or has not
  yet). Same capabilities as ``cached``.

A ``.gcode.3mf`` needs the live result, so after a cache hit or a reload the plate is **sliced again** (same inputs, so the
same key and the same G-code; its own :class:`~slicewright.engine.jobs.JobRunner` and poll timer, never the preview's
runner) and the file is written from that result. Nothing of the displayed slice changes: no preview reload, no stale or
cache state, only a status line and a progress bar in the Export panel. A failed re-slice lands in the Issues list.

Files are written to a hidden sibling and renamed (``core/export.py``), so a failure never leaves a half-written file or
damages one being replaced. This module owns the runtime state (the last message per scene) and is a registration
stage so the message is dropped when a file is loaded.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import bpy

from .. import prefs
from ..core import export as core_export
from ..engine import adapter, jobs
from ..core.export import ExportError
from ..names import PACKAGE_ID
from . import issues, registry, slicing, thumbnails, timers
from .preview import runtime

REQUIRES_ENGINE = True
POLL_S = 0.1
ISSUES_SOURCE = "export"          # the Issues list's source for the errors of a failed re-slice
log = logging.getLogger("slicewright.export")

GCODE, THREEMF = "gcode", "3mf"
KINDS = (GCODE, THREEMF)
LABEL = {GCODE: "G-code", THREEMF: ".gcode.3mf"}


@dataclass
class Source:
    """What an export reads: ``kind`` is ``live``, ``cached`` or ``entry`` (see the module docstring)."""
    kind: str
    result: Any
    key: str

    @property
    def can_3mf(self) -> bool:
        return hasattr(self.result, "write_gcode_3mf")


@dataclass
class _Reslice:
    """A re-slice that will write a ``.gcode.3mf`` when it is done."""
    runner: Any
    scene: str
    dest: str
    overwrite: bool
    key: str                                  # the key of the slice the user is looking at: the re-slice must equal it
    plate_name: str
    progress: float = 0.0
    text: str = ""


class _Runtime:
    def __init__(self) -> None:
        self.messages: dict[str, tuple[str, str]] = {}      # scene name -> (level, text) of the last export
        self.job: _Reslice | None = None                    # the re-slice in flight, if any


_rt = _Runtime()


# -- messages ----------------------------------------------------------------------------------------------------

def message(scene) -> tuple[str, str] | None:
    """``(level, text)`` of the last export of ``scene`` (``level`` is ``INFO`` or ``ERROR``), or ``None``."""
    return _rt.messages.get(scene.name)


def set_message(scene, level: str | None, text: str = "") -> None:
    if level:
        _rt.messages[scene.name] = (level, text)
    else:
        _rt.messages.pop(scene.name, None)
    runtime.tag_redraw()


# -- the source and whether an export is possible -----------------------------------------------------------------

def source_for(scene) -> Source | None:
    """The result an export would write, or ``None`` when there is none (never sliced, a reload whose cache entry is
    gone, or the cached preview still loading)."""
    sw = getattr(scene, PACKAGE_ID, None)
    if sw is None or not sw.last_cache_key:
        return None
    key = sw.last_cache_key
    if any(t.name.startswith("cache-load:") for t in timers.runner.pending()):
        return None                                  # rt still shows the previous slice until the load lands
    rt = runtime.get(scene)
    if rt is not None and rt.result is not None:
        return Source("cached" if getattr(rt.result, "from_cache", False) else "live", rt.result, key)
    cache = slicing.result_cache()
    entry = cache.lookup(key, touch=False) if cache is not None else None
    return Source("entry", entry, key) if entry is not None else None


def availability(scene, kind: str) -> tuple[bool, str]:
    """``(ok, why not)``: whether the ``kind`` export can start now, and the reason shown in the panel when it cannot."""
    sw = getattr(scene, PACKAGE_ID, None)
    if sw is None:
        return False, ""
    if _rt.job is not None:
        return False, "Wait for the export to finish"
    if slicing.is_live():
        return False, "Wait for the slice to finish"
    if any(t.name.startswith("cache-load:") for t in timers.runner.pending()):
        return False, "Loading the cached slice…"
    source = source_for(scene)
    if source is None:
        return False, "Slice the plate to export it"
    if kind == THREEMF and not source.can_3mf and sw.stale:
        # the engine's result is gone and the inputs of the slice on screen cannot be reproduced any more
        return False, "Out of date: slice again, then export the .gcode.3mf"
    return True, ""


def is_live() -> bool:
    """A re-slice for a ``.gcode.3mf`` is running (it holds the engine, so Slice must wait)."""
    return _rt.job is not None


def progress(scene) -> tuple[float, str] | None:
    """``(fraction, text)`` of the re-slice running for ``scene``, or ``None``."""
    job = _rt.job
    return (job.progress, job.text) if job is not None and job.scene == scene.name else None


# -- names and folders --------------------------------------------------------------------------------------------

def basename() -> str:
    return core_export.input_basename(bpy.data.filepath)


def engine_name(source: Source) -> str:
    """The engine's ``output_filename`` for this slice, or ``<basename>.gcode`` when the format cannot be evaluated."""
    base = basename()
    try:
        name = source.result.output_filename(base)
    except Exception as exc:  # noqa: BLE001 - a broken filename_format must not stop an export
        log.warning("output_filename failed (%s); using %s.gcode", exc, base)
        return f"{base}.gcode"
    return name or f"{base}.gcode"


def default_filepath(scene, kind: str) -> str:
    """The file dialog's initial path: the .blend's folder (else the last export folder, else home) plus the engine's
    name for this slice."""
    source = source_for(scene)
    name = engine_name(source) if source is not None else f"{basename()}.gcode"
    folder = core_export.start_directory(bpy.data.filepath, prefs.get().last_export_dir, os.path.expanduser("~"))
    return core_export.default_path(folder, name, kind)


def _remember_folder(dest: str) -> None:
    try:
        prefs.get().last_export_dir = os.path.dirname(os.path.abspath(dest))
    except Exception:  # noqa: BLE001 - the preference is a convenience
        log.debug("could not store the last export folder", exc_info=True)


# -- writing ------------------------------------------------------------------------------------------------------

def _size_text(size: int) -> str:
    return f"{size / 1e6:.1f} MB" if size >= 1_000_000 else f"{max(1, round(size / 1e3))} kB"


def export_gcode(scene, dest: str, *, overwrite: bool = False) -> str:
    """Write the slice's G-code to ``dest`` (byte-identical to the engine's file) and return the success message.
    Raises :class:`ExportError`; the message is recorded for the panel either way."""
    source = source_for(scene)
    if source is None:
        raise ExportError("There is no slice to export")
    try:
        size = core_export.atomic_write(dest, source.result.write_gcode, overwrite=overwrite)
    except ExportError as exc:
        set_message(scene, "ERROR", f"G-code not exported: {exc}")
        raise
    except Exception as exc:  # noqa: BLE001 - an engine or copy failure: say it, do not lose the slice
        log.exception("write_gcode failed")
        set_message(scene, "ERROR", f"G-code not exported: {exc}")
        raise ExportError(f"Could not write the G-code: {exc}") from exc
    _remember_folder(dest)
    text = f"Exported {os.path.basename(dest)} ({_size_text(size)})"
    set_message(scene, "INFO", text)
    log.info("exported G-code of %s (%s) to %s", source.key, source.kind, dest)
    return text


def _engine_message(exc: BaseException) -> str:
    sc = registry.state.status.module if registry.state.status is not None else None
    report = adapter.map_exception(sc, exc)
    return jobs.error_message(report) if report.kind != "Busy" else "The engine is busy with another job. Try again in a moment"


def _write_3mf(scene, dest: str, result, plate_name: str, *, overwrite: bool) -> str:
    """Write ``result`` (an engine ``SliceResult``) to ``dest`` as a ``.gcode.3mf``, atomically. One engine call that
    cannot be chunked; it holds the engine lock and the main thread until it returns."""
    try:
        size = core_export.atomic_write(
            dest, lambda tmp: result.write_gcode_3mf(tmp, {"plate_name": plate_name}), overwrite=overwrite)
    except ExportError as exc:
        set_message(scene, "ERROR", f".gcode.3mf not exported: {exc}")
        raise
    except Exception as exc:  # noqa: BLE001 - Busy, EngineError (no plate), a full disk
        log.exception("write_gcode_3mf failed")
        text = _engine_message(exc)
        set_message(scene, "ERROR", f".gcode.3mf not exported: {text}")
        raise ExportError(f"Could not write the .gcode.3mf: {text}") from exc
    _remember_folder(dest)
    text = f"Exported {os.path.basename(dest)} ({_size_text(size)})"
    set_message(scene, "INFO", text)
    log.info("exported .gcode.3mf to %s", dest)
    return text


def export_3mf(scene, dest: str, *, overwrite: bool = False) -> str:
    """Write the slice as a ``.gcode.3mf`` to ``dest`` and return the message. With the live engine result it is
    written now. Without one (a cache hit, a reloaded file) the plate is sliced again first: this returns at once with
    "Slicing again…" and the file is written by :func:`poll_export` when that slice is done. Raises
    :class:`ExportError`."""
    source = source_for(scene)
    if source is None:
        raise ExportError("There is no slice to export")
    core_export.check_destination(dest, overwrite=overwrite)
    issues.clear(ISSUES_SOURCE)
    if source.can_3mf:
        return _write_3mf(scene, dest, source.result, basename(), overwrite=overwrite)
    if getattr(scene, PACKAGE_ID).stale:
        raise ExportError("The plate changed since this slice: slice again, then export the .gcode.3mf")
    return _start_reslice(scene, dest, source.key, overwrite)


def _start_reslice(scene, dest: str, key: str, overwrite: bool) -> str:
    from . import slice_inputs
    try:
        request = slice_inputs.build_request(bpy.context, threads=int(prefs.get().threads))
    except slice_inputs.InputError as exc:
        set_message(scene, "ERROR", f".gcode.3mf not exported: {exc}")
        raise ExportError(str(exc)) from exc
    request.use_cache = False                                  # the point is a live result
    request.thumbnail_source = thumbnails.source_for(request)  # the plate thumbnail goes into the file, as on a first slice
    runner = jobs.JobRunner(registry.state.status.module)      # its own: the preview's runner and state stay untouched
    status = runner.start(request)
    job = _Reslice(runner, scene.name, dest, overwrite, key, basename())
    set_message(scene, None)
    if status.terminal:                                        # failed before an engine job (Busy, bad value)
        _conclude(job, status)
        raise ExportError(status.message or "The plate could not be sliced")
    _rt.job = job
    _update_job(job, status)
    registry.register_timer(poll_export, first_interval=POLL_S)
    return "Slicing again to write the .gcode.3mf…"


def _update_job(job: _Reslice, status) -> None:
    job.progress = status.progress
    job.text = f"Slicing again {round(status.progress * 100)}%" if status.state == jobs.SLICING else "Slicing again…"
    slicing._set_status_text(job.text)
    runtime.tag_redraw()


def poll_export() -> float | None:
    """The timer callback of a re-slice for a ``.gcode.3mf``: poll the job; when it is done write the file. Returns the
    next delay, or ``None`` to stop. Timers never fire in ``-b``: tests call it directly."""
    job = _rt.job
    if job is None:
        return None
    try:
        status = job.runner.poll()
        if not status.terminal:
            _update_job(job, status)
            return POLL_S
        _rt.job = None
        slicing._set_status_text(None)
        _conclude(job, status)
    except Exception:  # noqa: BLE001 - an exception would silently unregister the timer and strand the Export panel
        log.exception("export poll failed")
        _drop_job()
    return None


def _conclude(job: _Reslice, status) -> None:
    scene = bpy.data.scenes.get(job.scene)
    if scene is None:
        return
    if status.state == jobs.DONE:
        sw = getattr(scene, PACKAGE_ID)
        if status.key != job.key or sw.last_cache_key != job.key:
            set_message(scene, "ERROR", ".gcode.3mf not exported: the slice on screen changed while slicing again")
            return
        try:
            _write_3mf(scene, job.dest, status.result, job.plate_name, overwrite=job.overwrite)
        except ExportError:
            pass                                           # recorded as the message
        return
    if status.state == jobs.CANCELLED:
        set_message(scene, "ERROR", ".gcode.3mf not exported: cancelled")
        return
    issues.set_source(scene, ISSUES_SOURCE, status.issues)    # the typed error, where an error is acted on
    set_message(scene, "ERROR", f".gcode.3mf not exported: {status.message}")


def cancel() -> bool:
    """Cancel the re-slice in flight (its outcome arrives through :func:`poll_export`). False if none runs."""
    job = _rt.job
    return bool(job is not None and job.runner.cancel())


def _drop_job() -> None:
    job, _rt.job = _rt.job, None
    if job is not None:
        job.runner.cancel(discard=True)
    slicing._set_status_text(None)
    if bpy.app.timers.is_registered(poll_export):
        bpy.app.timers.unregister(poll_export)


# -- lifecycle ----------------------------------------------------------------------------------------------------

def _on_load_pre() -> None:
    """A file load drops a re-slice in flight (its outcome belongs to the old file) and the last messages."""
    _drop_job()
    _rt.messages.clear()


def register() -> None:
    if _on_load_pre not in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.append(_on_load_pre)


def unregister() -> None:
    while _on_load_pre in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.remove(_on_load_pre)
    _drop_job()
    _rt.messages.clear()
