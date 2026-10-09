# SPDX-License-Identifier: GPL-3.0-or-later
"""Read access to the engine's profile archive (03 sections 3.1 and 3.3).

The archive is the single zip returned by ``sc.profiles_archive()``: JSON only, one vendor header
``<Vendor>.json`` per vendor and the presets under ``<Vendor>/{machine,process,filament}/``. A
single leading ``profiles/`` directory is tolerated and stripped, because the exact root of the
real archive is not pinned by 04. No ``bpy`` here.
"""
from __future__ import annotations

import json
import os
import zipfile
from typing import Any

PRESET_DIRS = ("machine", "process", "filament")


class ProfileError(Exception):
    """A profile could not be read, found or resolved."""


class ProfileSource:
    """A read-only view of a profiles zip. Paths are relative to the archive root."""

    def __init__(self, archive_path: str) -> None:
        self.archive_path = archive_path
        self._zip: zipfile.ZipFile | None = None
        self._names: dict[str, str] | None = None     # relative path -> zip member name

    def _open(self) -> zipfile.ZipFile:
        if self._zip is None:
            try:
                self._zip = zipfile.ZipFile(self.archive_path)
            except (OSError, zipfile.BadZipFile) as exc:
                raise ProfileError(f"cannot open profile archive {self.archive_path}: {exc}") from exc
        return self._zip

    def _members(self) -> dict[str, str]:
        if self._names is None:
            names = [n for n in self._open().namelist() if n.endswith(".json")]
            prefix = "profiles/" if any(n.startswith("profiles/") for n in names) else ""
            self._names = {n[len(prefix):]: n for n in names if n.startswith(prefix)}
        return self._names

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> "ProfileSource":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def size(self) -> int:
        """Compressed size in bytes of the archive, a cheap cache-validity check."""
        try:
            return os.path.getsize(self.archive_path)
        except OSError as exc:
            raise ProfileError(f"cannot stat profile archive: {exc}") from exc

    def paths(self) -> list[str]:
        """Every JSON path in the archive, sorted."""
        return sorted(self._members())

    def vendor_headers(self) -> list[str]:
        """Paths of the vendor header files (``<Vendor>.json``)."""
        return [p for p in self.paths() if "/" not in p]

    def preset_paths(self) -> list[str]:
        """Paths of ``<Vendor>/<machine|process|filament>/<name>.json`` files, sorted."""
        out = []
        for p in self.paths():
            parts = p.split("/")
            if len(parts) == 3 and parts[1] in PRESET_DIRS:
                out.append(p)
        return out

    def read_json(self, path: str) -> dict[str, Any]:
        member = self._members().get(path)
        if member is None:
            raise ProfileError(f"no such profile file: {path}")
        try:
            data = json.loads(self._open().read(member).decode("utf-8-sig"))
        except (ValueError, zipfile.BadZipFile, OSError) as exc:
            raise ProfileError(f"cannot read {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ProfileError(f"{path}: expected a JSON object")
        return data
