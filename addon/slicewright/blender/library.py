# SPDX-License-Identifier: GPL-3.0-or-later
"""The loaded profile library: index, resolver and compatibility, built on the tick timer (03 section 3.4).

Runtime objects live here in a module registry, never in RNA (03 section 2.1). ``request()`` starts
the chunked index build once; the UI shows ``status()`` while it runs ("Loading printer library... 43%").
"""
from __future__ import annotations

from typing import Any

import bpy

from ..core import logs
from ..core.profiles import models
from ..core.profiles.compat import Compat, Subject, subject_from_entry
from ..core.profiles.index import Entry, ProfileIndex, load_or_build
from ..core.profiles.resolve import Resolver
from ..core.profiles.source import ProfileError, ProfileSource
from . import registry, timers

REQUIRES_ENGINE = True
TASK = "profile-index"


class Library:
    """The index plus everything derived from it."""

    def __init__(self, sc: Any, source: ProfileSource, index: ProfileIndex,
                 schema: dict[str, dict]) -> None:
        self.sc, self.source, self.index, self.schema = sc, source, index, schema
        self.resolver = Resolver(index, source, schema)
        self.compat = Compat(sc, index, self.resolver)
        self._printers: list[Entry] | None = None

    def printers(self) -> list[Entry]:
        if self._printers is None:
            self._printers = sorted(self.index.of_kind("machine", selectable_only=True),
                                    key=lambda e: (e.vendor.lower(), e.name.lower()))
        return self._printers

    def subject(self, kind: str, preset_id: str) -> Subject | None:
        """The compatibility subject of a system preset id, or ``None`` if unknown or unresolvable."""
        entry = self.index.get_or_renamed(kind, preset_id)
        if entry is None:
            return None
        try:
            return subject_from_entry(self.resolver, kind, entry)
        except ProfileError:
            logs.get_logger("profiles").warning("cannot resolve %s", preset_id, exc_info=True)
            return None

    def model_of(self, printer: Entry) -> Entry | None:
        return models.model_of(self.index, printer)

    def close(self) -> None:
        self.source.close()


class _State:
    def __init__(self) -> None:
        self.library: Library | None = None
        self.state = "idle"             # idle | loading | ready | failed
        self.progress = 0.0
        self.message = ""
        self.error: str | None = None
        self.generation = 0             # bumped on reset so a late callback of a cancelled load is ignored

    def reset(self) -> None:
        if self.library is not None:
            self.library.close()
        generation = self.generation + 1
        self.__init__()                 # type: ignore[misc]
        self.generation = generation


_s = _State()


def get() -> Library | None:
    return _s.library


def status() -> tuple[str, float, str]:
    """``(state, fraction, message)``: state is idle, loading, ready or failed."""
    return _s.state, _s.progress, _s.error if _s.state == "failed" else _s.message


def _cache_dir() -> str | None:
    try:
        return bpy.utils.extension_path_user(__package__.split(".")[0], path="cache", create=True)
    except Exception:  # noqa: BLE001 - not installed as an extension (source checkout, tests)
        return None


def _tag_redraw() -> None:
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception:  # noqa: BLE001 - background mode or no window manager yet
        pass


def request(force: bool = False) -> None:
    """Start loading the library unless it is loading or loaded. ``force`` retries after a failure."""
    status_ = registry.state.status
    if status_ is None or not status_.ok:
        return
    if _s.state in ("loading", "ready") or (_s.state == "failed" and not force):
        return
    sc = status_.module
    generation = _s.generation
    _s.state, _s.progress, _s.message, _s.error = "loading", 0.0, "Loading printer library…", None
    try:
        source = ProfileSource(sc.profiles_archive())
        commit = str(sc.version().get("orca_commit", ""))
        schema = sc.config_schema()
    except Exception as exc:  # noqa: BLE001 - surface any engine problem in the panel
        _s.state, _s.error = "failed", f"{type(exc).__name__}: {exc}"
        return

    def work():
        task = load_or_build(source, _cache_dir(), commit)
        try:
            while True:
                fraction, message = next(task)
                _s.progress, _s.message = fraction, message
                _tag_redraw()
                yield fraction, message
        except StopIteration as stop:
            return stop.value

    def done(index: ProfileIndex) -> None:
        if generation != _s.generation:
            source.close()
            return
        _s.library = Library(sc, source, index, schema)
        _s.state, _s.progress, _s.message = "ready", 1.0, ""
        _tag_redraw()

    def failed(exc: BaseException) -> None:
        source.close()
        if generation == _s.generation:
            _s.state, _s.error = "failed", f"{type(exc).__name__}: {exc}"
            _tag_redraw()

    timers.runner.submit(TASK, work(), on_done=done, on_error=failed,
                         on_cancel=source.close, replace=True)


def register() -> None:
    pass


def unregister() -> None:
    timers.runner.cancel(TASK)
    _s.reset()
