# SPDX-License-Identifier: GPL-3.0-or-later
"""Slicing job orchestration without Blender (03 section 6.1).

No ``bpy`` here: everything runs in plain Python against ``sc`` (the engine module, real or fake),
so the state machine is unit-tested without Blender. The Blender side (``blender/slicing.py``) gathers
a :class:`SliceRequest` (preset dicts from the scene, plate objects from the depsgraph), hands it to
the :class:`JobRunner` and calls ``poll()`` from a timer.

Flow (03 section 6.1): ``start`` composes and normalises the config (a bad value blocks the slice),
hashes the inputs into the key that drives staleness and the cache, builds an ``sc.SliceJob`` and starts it
(it returns at once, state ``validating`` in 04 section 8). ``poll`` maps the engine states onto the five
the UI shows (``validating``, ``slicing``, ``done``, ``failed``, ``cancelled``; ``cancelling`` is a flag on
``slicing``) and calls ``result()`` only after a terminal state, as 04 section 4.4 requires. There is one job per
process: a new ``start`` while a job lives cancels it and launches the new request once the old one has stopped
(the engine lock is held until then, so starting earlier would raise ``Busy``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from ..core import hashing
from ..core import issues as issues_core
from . import adapter

log = logging.getLogger("slicewright.jobs")

IDLE, VALIDATING, SLICING, DONE, FAILED, CANCELLED = (
    "idle", "validating", "slicing", "done", "failed", "cancelled")

# (state, label) in the order the Scene enum lists them.
JOB_STATES = (
    (IDLE, "Idle"), (VALIDATING, "Validating"), (SLICING, "Slicing"),
    (DONE, "Done"), (FAILED, "Failed"), (CANCELLED, "Cancelled"))
ACTIVE_STATES = (VALIDATING, SLICING)
TERMINAL_STATES = (DONE, FAILED, CANCELLED)

_ENGINE_STATE = {"validating": VALIDATING, "running": SLICING, "cancelling": SLICING}


@dataclass
class SliceRequest:
    """Everything one slice needs, already gathered on the Blender side."""
    printer: dict                                 # PresetDict (04 section 2.5), resolved, edits applied
    process: dict
    filaments: list                               # PresetDicts in slot order, slot overrides and colour applied
    objects: list                                 # ``core.plate_data.ExtractedObject``
    project: dict | None = None                   # FlatConfig overrides applied last (wipe tower position, ...)
    threads: int = 0                              # 0: the engine's default (cores - 1)
    thumbnails: list | None = None                # uint8 (H, W, 4) arrays for ``set_thumbnails``
    issues: list = field(default_factory=list)    # pre-checks (extraction, plate checks), 04 section 2.6 Issues
    use_cache: bool = True                        # False: slice even when the result cache holds this key
    tag: Any = None                               # opaque to the runner, returned in every status (e.g. a scene name)


@dataclass(frozen=True)
class JobStatus:
    """A snapshot of the job for the UI. ``finished`` is True only on the snapshot that reached a terminal state."""
    state: str = IDLE
    progress: float = 0.0                         # 0..1, non-decreasing within a job
    message: str = ""
    cancelling: bool = False
    key: str = ""                                 # input key of the job (03 section 4.4); empty before it is computed
    error: adapter.ErrorReport | None = None
    issues: tuple = ()                            # merged Issue dicts (03 section 6.5)
    result: Any = None                            # the SliceResult in ``done``
    cached: Any = None                            # the ``cache.Entry`` of a ``done`` served from the cache (no result)
    finished: bool = False
    tag: Any = None

    @property
    def from_cache(self) -> bool:
        return self.cached is not None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def text(self) -> str:
        return status_text(self)


# -- user-facing text --------------------------------------------------------------------------------------

def error_message(report: adapter.ErrorReport) -> str:
    """One line for the panel and the header for an engine error (04 section 7)."""
    kind, msg = report.kind, report.message
    if kind == "Cancelled":
        return "Cancelled"
    if kind == "ValidationError":
        errors = [i for i in report.issues if i.get("level") == "error"]
        first = errors[0]["message"] if errors else msg
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        return f"Validation failed: {first}{more}"
    if kind == "ConfigError":
        key = f" [{report.opt_key}]" if report.opt_key and report.opt_key not in msg else ""
        return f"Settings error: {msg}{key}"
    if kind == "SliceError":
        where = f" ({report.object_name})" if report.object_name and report.object_name not in msg else ""
        return f"Slicing failed: {msg}{where}"
    if kind == "EngineError":
        return f"Engine error: {msg}. Use Copy diagnostics in the preferences"
    if kind == "MemoryError":
        return "Out of memory. Try fewer threads or a simpler plate"
    return msg


def status_text(status: JobStatus) -> str:
    """``Slicing 42% · Generating supports`` (03 section 6.1 header text); the whole state in one line."""
    if status.state == IDLE:
        return ""
    if status.cancelling and status.active:
        return "Cancelling…"
    if status.state == VALIDATING:
        return "Validating…"
    if status.state == SLICING:
        text = f"Slicing {round(status.progress * 100)}%"
        return f"{text} · {status.message}" if status.message else text
    if status.state == DONE:
        return "Done (from cache)" if status.from_cache else "Done"
    return status.message or status.state.title()


merge_issues = issues_core.merge      # kept under its old name: the runner and its tests use it


def _issue(level: str, code: str, message: str, opt_key: str | None = None, object_name: str | None = None) -> dict:
    return {"level": level, "code": code, "message": message, "opt_key": opt_key, "object_name": object_name}


def slice_key(sc: Any, config: dict, object_keys) -> str:
    """The input key of a slice (03 section 4.4): the engine version, the normalised config and the object keys.
    Threads and thumbnails are not part of it. The runner and the staleness check both call this, so a preview
    is stale exactly when the key of the current inputs differs from the key it was sliced with."""
    return hashing.scene_key(str(sc.version().get("version", "")), config, list(object_keys))


def normalized_config(sc: Any, printer: dict, process: dict, filaments: list, project: dict | None = None) -> dict | None:
    """The config ``set_config`` receives for these presets, or ``None`` when it cannot be composed or has an
    invalid value (the runner reports that as a failed status; a staleness check just calls it changed)."""
    try:
        report = sc.normalize_config(sc.compose_config(printer, process, filaments, project))
    except Exception:  # noqa: BLE001 - any engine error means "no usable config"
        return None
    return None if report.get("errors") else report["config"]


# -- the runner --------------------------------------------------------------------------------------------

class JobRunner:
    """One slicing job at a time, advanced by ``poll()`` from a timer (or a test loop). Main thread only."""

    def __init__(self, sc: Any, cache: Any = None) -> None:
        self.sc = sc
        self.cache = cache                        # a ``cache.ResultCache``: a key it holds is served without a job
        self._job: Any = None                     # the live ``sc.SliceJob``
        self._pending: SliceRequest | None = None  # launched once the live job has stopped
        self._discard = False                     # the live job's outcome is dropped (a file load happened)
        self._status = JobStatus()
        self._issues: list[dict] = []             # pre-checks and normalize issues of the live job
        self._progress = 0.0

    # -- queries -------------------------------------------------------------------------------------------

    @property
    def status(self) -> JobStatus:
        return replace(self._status, finished=False)

    @property
    def live(self) -> bool:
        """An engine job is running or stopping (the engine lock is held), or a request waits for it."""
        return self._job is not None

    # -- starting and cancelling --------------------------------------------------------------------------

    def start(self, request: SliceRequest) -> JobStatus:
        """Launch ``request``. While a job lives it is cancelled and ``request`` starts when it has stopped
        (the returned status is then still the old job's, ``cancelling``); a newer request replaces an older
        waiting one. Errors do not raise: the returned status is ``failed`` with the mapped ``error``."""
        if self._job is not None:
            self._pending = request
            self._discard = False
            self.cancel()
            return self.status
        return self._launch(request)

    def cancel(self, *, discard: bool = False) -> bool:
        """Ask the live job to stop. ``discard`` also drops its outcome (and any waiting request): the file it
        belonged to is gone. Returns False when nothing was live."""
        if discard:
            self._pending = None
        if self._job is None:
            if discard:
                self._status = JobStatus(tag=self._status.tag)
            return False
        self._discard = self._discard or discard
        try:
            self._job.cancel()
        except Exception:  # noqa: BLE001 - cancel must never raise into the UI
            log.exception("job.cancel failed")
        if self._status.active:
            self._status = replace(self._status, cancelling=True)
        return True

    # -- polling -------------------------------------------------------------------------------------------

    def poll(self) -> JobStatus:
        """Advance the state machine one step. Cheap: one ``job.poll()``. The returned snapshot has
        ``finished=True`` exactly once per job, when it reached a terminal state."""
        if self._job is None:
            return self.status
        try:
            engine_state, percent, message = self._job.poll()
        except Exception as exc:  # noqa: BLE001 - mapped like every engine error
            return self._conclude_error(exc)
        if engine_state in ("done", "failed", "cancelled"):
            return self._conclude(engine_state)
        state = _ENGINE_STATE.get(engine_state, self._status.state)
        self._progress = max(self._progress, min(1.0, max(0.0, percent / 100.0)))
        if state == VALIDATING:
            message = ""
        elif state == SLICING:
            message = message or "Slicing"
        self._status = replace(self._status, state=state, progress=self._progress, message=message,
                               cancelling=engine_state == "cancelling" or self._status.cancelling)
        return self.status

    # -- internals -----------------------------------------------------------------------------------------

    def _launch(self, request: SliceRequest) -> JobStatus:
        sc = self.sc
        self._progress = 0.0
        self._issues = list(request.issues)
        self._status = JobStatus(VALIDATING, message="", tag=request.tag)
        if not request.objects:
            return self._fail(adapter.ErrorReport("ValidationError", "ERROR", "The plate has no objects to slice",
                                                  "jump_to_issue"),
                              [_issue("error", "empty_plate", "The Print plate has no objects")])
        try:
            composed = sc.compose_config(request.printer, request.process, request.filaments, request.project)
            report = sc.normalize_config(composed)
        except Exception as exc:  # noqa: BLE001
            return self._fail_exc(exc)
        self._issues += list(report.get("issues", ()))
        errors = report.get("errors") or {}
        if errors:                                # 04 section 6.2: invalid values are reported, never raised
            key, text = next(iter(errors.items()))
            issues = [_issue("error", "validation", msg, k) for k, msg in errors.items()]
            return self._fail(adapter.ErrorReport("ConfigError", "ERROR", text, "highlight_key", opt_key=key),
                              issues)
        config = report["config"]
        for obj in request.objects:
            if not obj.key:
                obj.compute_key()
        key = slice_key(sc, config, [o.key for o in request.objects])
        hit = self._lookup(key) if request.use_cache else None
        if hit is not None:                       # 03 section 6.1: a cache hit short-circuits the engine job
            self._progress = 1.0
            self._status = replace(self._status, state=DONE, progress=1.0, message="Done", key=key, cancelling=False,
                                   error=None, result=None, cached=hit,
                                   issues=tuple(merge_issues(self._issues, list(hit.warnings))))
            return self._finished()
        try:
            job = sc.SliceJob()
            job.set_config(config)
            job.set_threads(int(request.threads))
            for obj in request.objects:
                job.add_object(obj.name, obj.vertices, obj.triangles, **obj.add_object_kwargs())
            if request.thumbnails:
                job.set_thumbnails(request.thumbnails)
            job.start()                           # returns at once; may raise Busy
        except Exception as exc:  # noqa: BLE001
            self._status = replace(self._status, key=key)
            return self._fail_exc(exc)
        self._job = job
        self._status = replace(self._status, key=key)
        return self.status

    def _lookup(self, key: str) -> Any:
        if self.cache is None:
            return None
        try:
            return self.cache.lookup(key)
        except Exception:  # noqa: BLE001 - a broken cache means slicing, never a failed slice
            log.exception("result cache lookup failed")
            return None

    def _fail_exc(self, exc: BaseException) -> JobStatus:
        report = adapter.map_exception(self.sc, exc)
        log.log(logging.INFO if report.severity == "INFO" else logging.WARNING,
                "slice %s: %s", report.kind, report.message)
        return self._fail(report, list(report.issues))

    def _fail(self, report: adapter.ErrorReport, issues: list[dict]) -> JobStatus:
        self._job = None
        self._pending = None
        state = CANCELLED if report.kind == "Cancelled" else FAILED
        self._status = replace(self._status, state=state, cancelling=False, message=error_message(report),
                               error=report, issues=tuple(merge_issues(issues, self._issues)), result=None)
        return self._finished()

    def _finished(self) -> JobStatus:
        """The terminal status, flagged ``finished``; discarded outcomes (file load) become idle."""
        if self._discard:
            self._discard = False
            self._status = JobStatus(tag=self._status.tag)
            return self.status
        return replace(self._status, finished=True)

    def _conclude_error(self, exc: BaseException) -> JobStatus:
        self._job = None
        return self._after(lambda: self._fail_exc(exc))

    def _conclude(self, engine_state: str) -> JobStatus:
        job, self._job = self._job, None
        try:
            result = job.result()                 # terminal already: never blocks (04 section 4.4)
        except Exception as exc:  # noqa: BLE001 - failed and cancelled jobs raise their stored error
            self._job = None
            return self._after(lambda: self._fail_exc(exc))
        warnings = list(getattr(result, "warnings", ()) or ())

        def done() -> JobStatus:
            self._progress = 1.0
            self._status = replace(self._status, state=DONE, progress=1.0, message="Done", cancelling=False,
                                   error=None, result=result, issues=tuple(merge_issues(self._issues, warnings)))
            return self._finished()
        return self._after(done)

    def _after(self, conclude) -> JobStatus:
        """Run ``conclude`` (which records the old job's outcome) unless a newer request is waiting: then that
        outcome is dropped and the request launches now that the engine lock is free."""
        pending, self._pending = self._pending, None
        if pending is not None:
            self._discard = False
            return self._launch(pending)
        return conclude()
