# SPDX-License-Identifier: GPL-3.0-or-later
"""Exporting the last slice (03 section 8.1): where its G-code comes from, its default name, and the write.

Which result is exported is the one the preview shows (``Scene.slicewright.last_cache_key``):

* ``live``: the engine's own ``SliceResult`` (``rt.result``), straight after a slice. It has ``write_gcode`` and
  ``write_gcode_3mf``;
* ``cached``: a :class:`~slicewright.engine.cache.CachedResult` after a cache hit or a reloaded file. It has
  ``write_gcode`` (a copy of the cached ``out.gcode``) but no ``write_gcode_3mf``, which needs the live result;
* ``entry``: the cache's on-disk entry for the key, when the preview itself is not loaded (it failed to load, or has not
  yet). Same capabilities as ``cached``.

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
from ..core.export import ExportError
from ..names import PACKAGE_ID
from . import registry, slicing, timers
from .preview import runtime

REQUIRES_ENGINE = True
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


class _Runtime:
    def __init__(self) -> None:
        self.messages: dict[str, tuple[str, str]] = {}      # scene name -> (level, text) of the last export


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
    if slicing.is_live():
        return False, "Wait for the slice to finish"
    if any(t.name.startswith("cache-load:") for t in timers.runner.pending()):
        return False, "Loading the cached slice…"
    source = source_for(scene)
    if source is None:
        return False, "Slice the plate to export it"
    return True, ""


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


# -- lifecycle ----------------------------------------------------------------------------------------------------

def _on_load_pre() -> None:
    _rt.messages.clear()


def register() -> None:
    if _on_load_pre not in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.append(_on_load_pre)


def unregister() -> None:
    while _on_load_pre in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.remove(_on_load_pre)
    _rt.messages.clear()
