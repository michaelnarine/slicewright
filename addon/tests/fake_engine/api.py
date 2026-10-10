# SPDX-License-Identifier: GPL-3.0-or-later
"""Module-level functions of the engine API (04 section 3) for the fake."""
from __future__ import annotations

import os
import platform
import tempfile

from .schema_data import TAB_LAYOUT, build_schema

API_VERSION: tuple[int, int] = (1, 0)

MOVE_TYPES = {
    "Noop": 0, "Retract": 1, "Unretract": 2, "Seam": 3, "Tool_change": 4, "Color_change": 5,
    "Pause_print": 6, "Custom_gcode": 7, "Travel": 8, "Wipe": 9, "Extrude": 10,
}
ROLES = {name: i for i, name in enumerate([
    "None", "Perimeter", "ExternalPerimeter", "OverhangPerimeter", "InternalInfill",
    "SolidInfill", "TopSolidInfill", "BottomSurface", "Ironing", "BridgeInfill",
    "InternalBridgeInfill", "GapFill", "Skirt", "Brim", "SupportMaterial",
    "SupportMaterialInterface", "SupportTransition", "WipeTower", "Custom", "Mixed"])}

_log = {"level": 1, "path": None}
_tmp: dict[str, str] = {}


def _platform_tag() -> str:
    system = platform.system()
    if system == "Darwin":
        return "macos-arm64"
    if system == "Windows":
        return "windows-x64"
    return "linux-x64"


def temp_dir() -> str:
    """``<user temp>/slicewright_engine_fake/<pid>/`` (created on first use)."""
    if "dir" not in _tmp:
        path = os.path.join(tempfile.gettempdir(), "slicewright_engine_fake", str(os.getpid()))
        os.makedirs(path, exist_ok=True)
        _tmp["dir"] = path
    return _tmp["dir"]


def version() -> dict:
    return {
        "version": "1.0.0.dev0+fake",
        "api": API_VERSION,
        "orca_tag": "v0.0.0-fake",
        "orca_commit": "0" * 40,
        "patches": [],
        "build": {"compiler": "python", "platform": _platform_tag(), "date": "2026-10-09",
                  "deps": {}},
        "source_url": "https://github.com/michaelnarine/slicewright/releases/tag/engine-v1.0.0",
        "fake": True,
    }


def enums() -> dict[str, dict[str, int]]:
    return {"move_type": dict(MOVE_TYPES), "role": dict(ROLES)}


def config_schema() -> dict[str, dict]:
    return build_schema()


def tab_layout() -> dict[str, list[dict]]:
    import copy
    return copy.deepcopy(TAB_LAYOUT)


def profiles_archive() -> str:
    """A small hand-made, CC0 profile library (``profiles_fixture``), written once per process."""
    from .profiles_fixture import write_zip
    path = os.path.join(temp_dir(), "profiles.zip")
    if not os.path.exists(path):
        part = path + ".part"
        write_zip(part)
        os.replace(part, path)
    return path


def resources_dir() -> str:
    path = os.path.join(temp_dir(), "resources")
    os.makedirs(path, exist_ok=True)
    return path


def licenses() -> dict[str, str]:
    return {
        "LICENSE": "Fake engine. Not a real licence text.",
        "THIRD_PARTY_LICENSES": "None (fake engine).",
        "NOTICE": "Test double for slicewright_engine.",
        "SOURCE": "https://github.com/michaelnarine/slicewright",
    }


def set_log(level: int, path: str | None = None) -> None:
    if isinstance(level, bool) or not isinstance(level, int):
        raise TypeError("level must be int")
    if not 0 <= level <= 5:
        raise ValueError("level must be 0..5")
    if path is not None and not isinstance(path, str):
        raise TypeError("path must be str or None")
    _log["level"], _log["path"] = level, path


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


__all__ = ["API_VERSION", "CancelToken", "config_schema", "enums", "licenses",
           "profiles_archive", "resources_dir", "set_log", "tab_layout", "version"]
