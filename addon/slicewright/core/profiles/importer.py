# SPDX-License-Identifier: GPL-3.0-or-later
"""Import user presets from OrcaSlicer and BambuStudio (03 section 3.9).

Runs only when the user asks. ``scan`` reads the other app's user preset folders and never writes
to them; ``plan_import`` decides what each chosen file becomes; ``apply_import`` writes into our own
:class:`~.user.UserStore`. A preset whose parent is a system preset we know keeps ``inherits``;
otherwise it is imported as a standalone copy with a warning. No ``bpy`` here.
"""
from __future__ import annotations

import json
import logging
import ntpath
import os
import posixpath
from dataclasses import dataclass, field
from typing import Any

from .index import ProfileIndex
from .resolve import Resolver, own_keys
from .source import ProfileError
from .user import KINDS, UserStore, build_preset_json, diff_config, parent_id, strip_secrets

log = logging.getLogger("slicewright.profiles")

APPS = ("OrcaSlicer", "BambuStudio")
MAX_FILE_BYTES = 2 * 1024 * 1024
# Bookkeeping the other apps add that means nothing to us (identity keys are dropped separately).
IMPORT_META_KEYS = frozenset({
    "user_id", "updated_time", "sync_info", "base_id", "print_settings_id", "filament_settings_id",
    "printer_settings_id", "is_custom_defined",
})


def candidate_dirs(platform: str, home: str, env: dict[str, str]) -> list[tuple[str, str]]:
    """Where each app keeps its data, for ``sys.platform``-style ``platform``. Pure: no filesystem access.

    The macOS and Windows locations are well known. The Linux and Flatpak ones are **[U] unverified**.
    """
    if platform == "darwin":
        base = posixpath.join(home, "Library", "Application Support")
        return [(app, posixpath.join(base, app)) for app in APPS]
    if platform.startswith("win"):
        appdata = env.get("APPDATA") or ntpath.join(home, "AppData", "Roaming")
        return [(app, ntpath.join(appdata, app)) for app in APPS]
    config = env.get("XDG_CONFIG_HOME") or posixpath.join(home, ".config")
    flatpak = {"OrcaSlicer": "io.github.softfever.OrcaSlicer", "BambuStudio": "com.bambulab.BambuStudio"}
    out = [(app, posixpath.join(config, app)) for app in APPS]
    out += [(app, posixpath.join(home, ".var", "app", flatpak[app], "config", app)) for app in APPS]
    return out


@dataclass
class Candidate:
    app: str
    path: str
    kind: str                       # machine | filament | process
    name: str
    inherits: str
    data: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass
class ScanResult:
    candidates: list[Candidate] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)      # (path, reason)


def _inside(real_base: str, path: str) -> bool:
    real = os.path.realpath(path)
    return real == real_base or real.startswith(real_base + os.sep)


def scan(base_dir: str, app: str = "") -> ScanResult:
    """Find ``<base>/user/*/{machine,filament,process}/*.json``. Read-only."""
    result = ScanResult()
    user_root = os.path.join(base_dir, "user")
    if not os.path.isdir(user_root):
        return result
    real_base = os.path.realpath(base_dir)
    for uid in sorted(os.listdir(user_root)):
        for kind in KINDS:
            folder = os.path.join(user_root, uid, kind)
            if not os.path.isdir(folder) or not _inside(real_base, folder):
                continue
            for fn in sorted(os.listdir(folder)):
                path = os.path.join(folder, fn)
                if not fn.endswith(".json"):
                    continue
                if not _inside(real_base, path):
                    result.skipped.append((path, "symbolic link leaves the preset folder"))
                    continue
                try:
                    if os.path.getsize(path) > MAX_FILE_BYTES:
                        result.skipped.append((path, "file is larger than 2 MB"))
                        continue
                    with open(path, encoding="utf-8-sig") as fh:
                        data = json.load(fh)
                except (OSError, ValueError) as exc:
                    result.skipped.append((path, f"unreadable: {exc}"))
                    continue
                if not isinstance(data, dict):
                    result.skipped.append((path, "not a JSON object"))
                    continue
                name = str(data.get("name") or os.path.splitext(fn)[0])
                result.candidates.append(
                    Candidate(app, path, kind, name, str(data.get("inherits") or ""), data))
    return result


@dataclass
class ImportItem:
    candidate: Candidate
    name: str                       # the name it will be saved under
    parent: str | None              # kept system parent, or None for a standalone copy
    preset_json: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    item: ImportItem
    path: str | None = None
    error: str | None = None


def _free_name(store: UserStore, kind: str, name: str, taken: set[tuple[str, str]]) -> str:
    def busy(n: str) -> bool:
        return (kind, n) in taken or store.exists(kind, n)
    if not busy(name):
        return name
    n, suffix = 1, " (imported)"
    while busy(name + suffix):
        n += 1
        suffix = f" (imported {n})"
    return name + suffix


def plan_import(candidates: list[Candidate], index: ProfileIndex, resolver: Resolver,
                store: UserStore) -> list[ImportItem]:
    """Decide what each chosen candidate becomes. Writes nothing."""
    items: list[ImportItem] = []
    taken: set[tuple[str, str]] = set()
    for cand in candidates:
        warnings: list[str] = []
        keys = strip_secrets({k: v for k, v in own_keys(cand.data).items()
                              if k not in IMPORT_META_KEYS})
        parent: str | None = None
        if cand.inherits:
            pid, others = parent_id(index, cand.kind, cand.inherits)
            try:
                if pid is None:
                    raise ProfileError("not found")
                keys = diff_config(resolver.resolve(cand.kind, pid).config, keys)
                parent = cand.inherits
                if others:
                    warnings.append(f"parent {cand.inherits!r} exists in several vendors; "
                                    f"the first ({pid}) is used")
            except ProfileError:
                warnings.append(f"parent {cand.inherits!r} not found; imported without inheritance, "
                                "missing settings use defaults")
        name = _free_name(store, cand.kind, cand.name, taken)
        if name != cand.name:
            warnings.append(f"a preset named {cand.name!r} exists; saved as {name!r}")
        taken.add((cand.kind, name))
        version = str(cand.data.get("version") or "1.0.0.0")
        items.append(ImportItem(cand, name, parent,
                                build_preset_json(cand.kind, name, parent, keys, version), warnings))
    return items


def apply_import(items: list[ImportItem], store: UserStore) -> list[ImportResult]:
    """Write the planned presets. Never overwrites: a name taken meanwhile is reported as an error."""
    results = []
    for item in items:
        kind = item.candidate.kind
        if store.exists(kind, item.name):
            results.append(ImportResult(item, error=f"{item.name!r} already exists"))
            continue
        try:
            results.append(ImportResult(item, path=store.write(kind, item.name, item.preset_json)))
        except (OSError, ValueError) as exc:
            log.warning("import of %s failed: %s", item.candidate.path, exc)
            results.append(ImportResult(item, error=str(exc)))
    return results
