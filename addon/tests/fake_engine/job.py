# SPDX-License-Identifier: GPL-3.0-or-later
"""SliceJob for the fake: the 04 section 8 state machine, advanced by ``poll()`` (no threads)."""
from __future__ import annotations

import weakref

import numpy as np

from . import slicer
from .api import API_VERSION, CancelToken, temp_dir  # noqa: F401  (re-exported for callers)
from .config import SCHEMA, canon
from .errors import (
    Busy, Cancelled, ConfigError, EngineError, SliceError, StateError, ValidationError,
)
from .slicer import ObjectData

TERMINAL = ("done", "failed", "cancelled")
_ACTIVE = ("validating", "running", "cancelling")

# Stages of the synthetic slice, each one a tick of poll()/result().
_STAGES = (
    (10.0, "Processing triangle mesh"), (25.0, "Slicing mesh"), (45.0, "Generating walls"),
    (65.0, "Generating infill"), (80.0, "Generating support"), (92.0, "Generating G-code"),
)
_WAIT_STEP_S = 0.01   # virtual time charged per tick while result(timeout) waits

_engine_lock: dict = {"holder": None}   # weakref to the job that owns the engine lock


def _lock_holder():
    ref = _engine_lock["holder"]
    job = ref() if ref else None
    return job if job is not None and job._holds_lock else None


def _acquire(job: "SliceJob") -> None:
    holder = _lock_holder()
    if holder is not None and holder is not job:
        raise Busy("another slice or arrange holds the engine lock")
    _engine_lock["holder"] = weakref.ref(job)
    job._holds_lock = True


class SliceJob:
    def __init__(self) -> None:
        self._state = "idle"
        self._holds_lock = False
        self._config: dict | None = None
        self._objects: list[ObjectData] = []
        self._thumbnails: list[np.ndarray] = []
        self._threads = 0
        self._issues: list[dict] | None = None   # cached validate() result
        self._percent = 0.0
        self._message = ""
        self._stage = 0
        self._polls = 0
        self._error: Exception | None = None
        self._result = None

    def __del__(self) -> None:
        self._holds_lock = False   # dropping a live job cancels it and frees the lock

    # -- building -----------------------------------------------------------------------

    def _require_idle(self) -> None:
        if self._state != "idle":
            raise StateError(f"not allowed in state {self._state!r}", self._state)

    def set_config(self, flat: dict[str, str]) -> None:
        self._require_idle()
        if self._config is not None:
            raise StateError("set_config may be called only once", self._state)
        if not isinstance(flat, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in flat.items()):
            raise TypeError("config must be dict[str, str]")
        config = {k: canon(k, e["default"]) for k, e in SCHEMA.items()}
        for key, value in flat.items():
            if key not in SCHEMA:
                raise ConfigError(f"unknown option {key!r}", key=key, value=value)
            try:
                config[key] = canon(key, value)
            except ValueError as exc:
                raise ConfigError(str(exc), key=key, value=value) from None
        self._config = config

    def set_threads(self, n: int) -> None:
        self._require_idle()
        if isinstance(n, bool) or not isinstance(n, int):
            raise TypeError("n must be int")
        self._threads = n

    def add_object(self, name: str, vertices, triangles, *, extruder: int = 0,
                   config_overrides: dict[str, str] | None = None, face_extruder=None,
                   face_support=None, face_seam=None, repair: bool = False,
                   ensure_on_bed: bool = False) -> int:
        self._require_idle()
        if self._config is None:
            raise StateError("set_config must be called before add_object", self._state)
        if not isinstance(name, str):
            raise TypeError("name must be str")
        v = _array(vertices, np.float32, "vertices", (None, 3))
        t = _array(triangles, np.int32, "triangles", (None, 3))
        if len(t) and (t.min() < 0 or t.max() >= len(v)):
            raise ValueError("triangle index out of range")
        if isinstance(extruder, bool) or not isinstance(extruder, int):
            raise TypeError("extruder must be int")
        if not 0 <= extruder <= 32:
            raise ValueError("extruder must be 0..32")
        faces = {}
        for key, arr, top in (("face_extruder", face_extruder, 16), ("face_support", face_support, 2),
                              ("face_seam", face_seam, 2)):
            if arr is None:
                faces[key] = None
                continue
            a = _array(arr, np.uint8, key, (len(t),))
            if len(a) and int(a.max()) > top:
                raise ValueError(f"{key} values must be 0..{top}")
            faces[key] = a.copy()
        overrides = {}
        for key, value in (config_overrides or {}).items():
            entry = SCHEMA.get(key)
            if entry is None or entry["scope"] == "global":
                raise ConfigError(f"{key!r} is not an object or region option", key=key, value=value)
            try:
                overrides[key] = canon(key, value)
            except ValueError as exc:
                raise ConfigError(str(exc), key=key, value=value) from None
        v = v.copy()
        moved = False
        if ensure_on_bed and len(v) and float(v[:, 2].min()) != 0.0:
            v[:, 2] -= v[:, 2].min()
            moved = True
        self._objects.append(ObjectData(name, v, t.copy(), extruder, overrides, moved_to_bed=moved,
                                        **faces))
        self._issues = None
        return len(self._objects) - 1

    def set_thumbnails(self, images: list) -> None:
        self._require_idle()
        checked = []
        for img in images:
            if not isinstance(img, np.ndarray) or img.dtype != np.uint8:
                raise TypeError("thumbnails must be uint8 arrays")
            if img.ndim != 3 or img.shape[2] != 4:
                raise ValueError("thumbnails must have shape (H, W, 4)")
            checked.append(img.copy())
        self._thumbnails = checked

    def validate(self) -> list[dict]:
        self._require_idle()
        return list(self._validated())

    def _validated(self) -> list[dict]:
        if self._config is None:
            raise StateError("set_config must be called first", self._state)
        if self._issues is None:
            self._issues = slicer.validate_objects(self._objects, self._config)
        return self._issues

    def arrange(self, spacing_mm: float | None = None, allow_rotation: bool = False) -> list[dict]:
        self._require_idle()
        if self._config is None:
            raise StateError("set_config must be called first", self._state)
        holder = _lock_holder()
        if holder is not None and holder is not self:
            raise Busy("the engine is busy")
        return slicer.arrange(self._objects, self._config, spacing_mm)

    # -- running ------------------------------------------------------------------------

    def start(self) -> None:
        self._require_idle()
        if self._config is None:
            raise StateError("set_config must be called first", self._state)
        _acquire(self)
        self._state, self._message, self._percent, self._polls = "validating", "", 0.0, 0

    def poll(self) -> tuple[str, float, str]:
        self._polls += 1
        if self._polls > 1:   # the first poll after start() reports "validating" as it is
            self._advance()
        if self._state == "done":
            return "done", 100.0, ""
        return self._state, self._percent, self._message

    def cancel(self) -> None:
        if self._state in ("validating", "running"):
            self._state, self._message = "cancelling", "Cancelling"

    def _finish(self, state: str, error: Exception | None = None) -> None:
        self._state, self._error = state, error
        self._holds_lock = False

    def _advance(self) -> None:
        if self._state == "validating":
            issues = self._validated()
            if any(i["level"] == "error" for i in issues):
                self._finish("failed", ValidationError(
                    next(i["message"] for i in issues if i["level"] == "error"), issues))
            else:
                self._state, self._message = "running", _STAGES[0][1]
        elif self._state == "running":
            if self._stage < len(_STAGES):
                self._percent, self._message = _STAGES[self._stage]
                self._stage += 1
                return
            try:
                self._result = slicer.synthesize(self._objects, self._config, self._thumbnails,
                                                 temp_dir())
                self._result.warnings[:0] = [i for i in self._validated() if i["level"] != "error"]
                self._finish("done")
            except SliceError as exc:
                self._finish("failed", exc)
            except Exception as exc:  # noqa: BLE001 - mapped like the real engine thread does
                self._finish("failed", EngineError(str(exc), type(exc).__name__))
        elif self._state == "cancelling":
            self._finish("cancelled", Cancelled("the job was cancelled"))

    def result(self, timeout: float | None = None):
        if self._state == "idle":
            raise StateError("result() on a job that was never started", self._state)
        waited = 0.0
        while self._state in _ACTIVE:
            if timeout is not None and waited + _WAIT_STEP_S > timeout:
                raise TimeoutError("the job did not finish in time")
            self._advance()
            waited += _WAIT_STEP_S
        if self._state == "done":
            return self._result
        raise self._error

    def run(self, progress=None, cancel: CancelToken | None = None):
        self.start()
        while True:
            state, pct, msg = self.poll()
            if progress is not None:
                progress(pct, msg)
            if state in TERMINAL:
                break
            if cancel is not None and cancel.cancelled:
                self.cancel()
        return self.result()


def _array(value, dtype, what: str, shape: tuple) -> np.ndarray:
    if not isinstance(value, np.ndarray):
        raise TypeError(f"{what} must be a numpy array")
    if value.dtype != dtype:
        raise TypeError(f"{what} must have dtype {np.dtype(dtype)}, got {value.dtype}")
    if value.ndim != len(shape) or any(s is not None and s != d for s, d in zip(shape, value.shape)):
        raise ValueError(f"{what} has shape {value.shape}, expected {shape}")
    if not value.flags.c_contiguous:
        raise ValueError(f"{what} must be C-contiguous")
    return value
