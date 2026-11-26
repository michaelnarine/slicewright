# SPDX-License-Identifier: GPL-3.0-or-later
"""Engine adapter: import, the 04 section 10 API check, and exception mapping (04 section 7).

No ``bpy`` here. The adapter returns an :class:`EngineStatus`; if the engine
cannot be imported or its API is incompatible, the add-on registers only a
diagnostic panel showing both versions and the import error. It never half-works.

Development hook: the environment variable ``SLICEWRIGHT_ENGINE_MODULE`` may name
``fake_engine`` (the test double in ``addon/tests``) instead of the real module.
Only the two names in ``ALLOWED_MODULES`` are accepted.

The real module is looked up on ``sys.path`` like any other. Blender does not read ``PYTHONPATH``, so
``SLICEWRIGHT_ENGINE_PATH`` (one or more directories, ``os.pathsep``-separated, each holding the
``slicewright_engine`` package, e.g. ``engine/build/engine/python``) is put in front of ``sys.path`` first. A symlink
of the package into Blender's user ``scripts/modules`` directory works as well (``engine/README.md``).

When the add-on runs from a source checkout (its folder is a symlink into ``<repo>/addon``, see
``addon/tools/install_dev.sh``) and the variable is not set (an empty value turns this off), the checkout's own build directory
``<repo>/engine/build/engine/python`` is used, so the add-on works when Blender is simply opened.
"""
from __future__ import annotations

import importlib
import os
import platform
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from ..names import ENGINE_MODULE, PRODUCT_NAME

REQUIRED_API = (1, 0)
ENV_VAR = "SLICEWRIGHT_ENGINE_MODULE"
PATH_ENV_VAR = "SLICEWRIGHT_ENGINE_PATH"
ALLOWED_MODULES = (ENGINE_MODULE, "fake_engine")


def api_compatible(found: tuple, required: tuple = REQUIRED_API) -> bool:
    """04 section 10: same major version, minor at least the required one."""
    found = tuple(found)
    return found[0] == required[0] and found[1] >= required[1]


@dataclass(frozen=True)
class EngineStatus:
    ok: bool
    module_name: str
    module: Any = None                       # the imported engine module when ``ok``
    required_api: tuple = REQUIRED_API
    found_api: tuple | None = None
    info: dict = field(default_factory=dict)  # sc.version() when it could be read
    error: str | None = None

    @property
    def summary(self) -> str:
        if self.ok:
            return f"engine {self.info.get('version', '?')} (API {fmt_api(self.found_api)})"
        return self.error or "engine unavailable"


def fmt_api(api: tuple | None) -> str:
    return "?" if api is None else ".".join(str(p) for p in api)


def module_name_from_env(environ: dict | None = None) -> str:
    name = (environ if environ is not None else os.environ).get(ENV_VAR) or ENGINE_MODULE
    return name if name in ALLOWED_MODULES else ENGINE_MODULE


def checkout_engine_dir(module_file: str | None = None) -> str | None:
    """``<repo>/engine/build/engine/python`` when this file lives (after resolving symlinks) in a source checkout
    that holds a built engine, else ``None``. An installed or packaged add-on has no such directory."""
    here = os.path.realpath(module_file or __file__)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))   # <repo>/addon/slicewright/engine/x.py
    candidate = os.path.join(root, "engine", "build", "engine", "python")
    if os.path.isdir(os.path.join(candidate, ENGINE_MODULE)) and os.path.isdir(os.path.join(root, "addon", "slicewright")):
        return candidate
    return None


def add_engine_paths(environ: dict | None = None, path: list | None = None) -> list[str]:
    """Put the directories of ``SLICEWRIGHT_ENGINE_PATH`` in front of ``sys.path`` (the source checkout's build
    directory when the variable is not set at all). Returns those it added."""
    env = environ if environ is not None else os.environ
    raw = env.get(PATH_ENV_VAR, "")
    if PATH_ENV_VAR not in env and environ is None:      # set but empty means "no extra directories", no fallback
        raw = checkout_engine_dir() or ""
    target = sys.path if path is None else path
    added = []
    for entry in reversed([p for p in raw.split(os.pathsep) if p]):
        entry = os.path.abspath(os.path.expanduser(entry))
        if os.path.isdir(entry) and entry not in target:
            target.insert(0, entry)
            added.append(entry)
    return added[::-1]


def load(module_name: str | None = None,
         importer: Callable[[str], Any] = importlib.import_module) -> EngineStatus:
    """Import the engine and check its API version. Never raises."""
    name = module_name or module_name_from_env()
    if name == ENGINE_MODULE:
        add_engine_paths()
    try:
        sc = importer(name)
    except Exception as exc:  # noqa: BLE001 - any import failure becomes a diagnostic
        return EngineStatus(False, name, error=f"cannot import {name}: {type(exc).__name__}: {exc}")
    try:
        info = dict(sc.version())
        found = tuple(info["api"])
        if len(found) != 2 or not all(isinstance(p, int) and not isinstance(p, bool) for p in found):
            raise ValueError(f"'api' must be a (major, minor) pair of ints, got {info['api']!r}")
        compatible = api_compatible(found)
    except Exception as exc:  # noqa: BLE001 - a malformed version() must not crash register()
        return EngineStatus(False, name, error=f"{name}.version() failed: {type(exc).__name__}: {exc}")
    if not compatible:
        return EngineStatus(
            False, name, required_api=REQUIRED_API, found_api=found, info=info,
            error=(f"engine API {fmt_api(found)} is not compatible with this add-on, "
                   f"which needs {fmt_api(REQUIRED_API)} (same major version, minor at least)"))
    return EngineStatus(True, name, module=sc, found_api=found, info=info)


def diagnostics_text(status: EngineStatus, extras: dict[str, str] | None = None,
                     log_tail: str = "") -> str:
    """Plain text for "Copy diagnostics" (03 section 9.4)."""
    lines = [
        f"{PRODUCT_NAME} diagnostics",
        f"Python: {sys.version.split()[0]} ({platform.platform()})",
        f"Engine module: {status.module_name}",
        f"Required API: {fmt_api(status.required_api)}",
        f"Found API: {fmt_api(status.found_api)}",
        f"Engine OK: {status.ok}",
    ]
    if status.error:
        lines.append(f"Error: {status.error}")
    for key in ("version", "orca_tag", "orca_commit"):
        if key in status.info:
            lines.append(f"{key}: {status.info[key]}")
    build = status.info.get("build")
    if isinstance(build, dict):
        lines.append(f"build: {build.get('platform')} {build.get('compiler')} {build.get('date')}")
    for key, value in (extras or {}).items():
        lines.append(f"{key}: {value}")
    if log_tail:
        lines += ["", "Log tail:", log_tail.rstrip()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# exception mapping (04 section 7, 03 section 6.1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ErrorReport:
    """What the UI should do about an engine exception."""
    kind: str                       # exception class name
    severity: str                   # 'INFO' | 'WARNING' | 'ERROR'
    message: str
    action: str                     # see ACTIONS
    opt_key: str | None = None
    object_name: str | None = None
    object_names: tuple = ()
    issues: tuple = ()
    hint: str = ""


ACTIONS = ("none", "highlight_key", "jump_to_issue", "select_object", "report_objects",
           "copy_diagnostics", "report_bug", "retry_later")


def _message(exc: BaseException) -> str:
    return getattr(exc, "message", "") or str(exc) or type(exc).__name__


def map_exception(sc: Any, exc: BaseException) -> ErrorReport:
    """Classify ``exc`` (raised by engine ``sc``) per the 04 section 7 table."""
    def is_(name: str) -> bool:
        cls = getattr(sc, name, None)
        return isinstance(cls, type) and isinstance(exc, cls)

    msg = _message(exc)
    if is_("Cancelled"):
        return ErrorReport("Cancelled", "INFO", "Slice cancelled", "none")
    if is_("Busy"):
        return ErrorReport("Busy", "WARNING", "The engine is busy with another job", "retry_later")
    if is_("StateError"):
        return ErrorReport("StateError", "ERROR", f"Internal error: {msg}", "report_bug",
                           hint=f"job state: {getattr(exc, 'state', None)}")
    if is_("ConfigError"):
        return ErrorReport("ConfigError", "ERROR", msg, "highlight_key",
                           opt_key=getattr(exc, "key", None))
    if is_("ValidationError"):
        issues = tuple(getattr(exc, "issues", ()) or ())
        return ErrorReport("ValidationError", "ERROR", msg, "jump_to_issue",
                           opt_key=getattr(exc, "opt_key", None),
                           object_name=getattr(exc, "object_name", None), issues=issues)
    if is_("SliceError"):
        return ErrorReport("SliceError", "ERROR", msg, "select_object",
                           object_name=getattr(exc, "object_name", None))
    if is_("ArrangeError"):
        return ErrorReport("ArrangeError", "ERROR", msg, "report_objects",
                           object_names=tuple(getattr(exc, "object_names", ()) or ()))
    if is_("EngineError"):
        return ErrorReport("EngineError", "ERROR", msg, "copy_diagnostics",
                           hint=str(getattr(exc, "detail", "")))
    if isinstance(exc, MemoryError):
        return ErrorReport("MemoryError", "ERROR", "Out of memory", "none",
                           hint="Try fewer threads or a simpler plate")
    if isinstance(exc, TimeoutError):
        return ErrorReport("TimeoutError", "WARNING", "The engine did not respond in time", "none")
    if isinstance(exc, (TypeError, ValueError)):
        return ErrorReport(type(exc).__name__, "ERROR", f"Internal error: {msg}", "report_bug")
    if is_("Error"):
        return ErrorReport("Error", "ERROR", msg, "copy_diagnostics")
    return ErrorReport(type(exc).__name__, "ERROR", msg, "report_bug")
