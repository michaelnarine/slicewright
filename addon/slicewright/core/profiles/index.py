# SPDX-License-Identifier: GPL-3.0-or-later
"""The searchable profile index, built in chunks without threads (03 section 3.4).

``build_index`` is a generator for ``core.ticking.TickRunner``: each ``yield`` ends one step of at
most ``step_files`` files or ``step_budget_s`` seconds, whichever comes first, so a tick never
overruns the 25 ms budget by more than one file. Entries keep only the header fields needed for
picking, inheritance and compatibility; presets are resolved lazily (``resolve.py``). The index is
cached as JSON per engine ``orca_commit``. No ``bpy`` here.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterator

from .source import ProfileError, ProfileSource

log = logging.getLogger("slicewright.profiles")

CACHE_FORMAT = 1
LIBRARY_VENDOR = "OrcaFilamentLibrary"     # the shared vendor every vendor may inherit from
KINDS = ("machine", "process", "filament", "model")
_KIND_OF_TYPE = {"machine": "machine", "process": "process", "filament": "filament",
                 "machine_model": "model"}
_KIND_OF_DIR = {"machine": "machine", "process": "process", "filament": "filament"}
# Header fields a preset may or may not carry; ``None`` means "absent", which matters because an
# absent field is inherited while an explicitly empty one is not (compat.py).
_LIST_FIELDS = ("compatible_printers", "compatible_prints")
_COND_FIELDS = ("compatible_printers_condition", "compatible_prints_condition")
_INFO_FIELDS = ("nozzle_diameter", "default_materials", "family", "default_print_profile")


def as_list(value: Any) -> list[str]:
    """A preset list value: a JSON list, or a ``;``-separated string."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [p.strip() for p in str(value).split(";") if p.strip()]


@dataclass
class Entry:
    kind: str                                   # machine | process | filament | model
    vendor: str
    name: str
    path: str                                   # path inside the archive
    instantiation: bool = False                 # selectable by the user (not a base or template)
    inherits: str = ""
    include: list[str] = field(default_factory=list)
    compatible_printers: list[str] | None = None
    compatible_printers_condition: str | None = None
    compatible_prints: list[str] | None = None
    compatible_prints_condition: str | None = None
    printer_model: str = ""
    printer_variant: str = ""
    renamed_from: list[str] = field(default_factory=list)
    info: dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"sys:{self.vendor}/{self.name}"

    @property
    def alias(self) -> str:
        """The name without its ``@printer`` suffix, e.g. ``Generic PLA @Acme`` -> ``Generic PLA``."""
        return self.name.split(" @")[0].strip()

    @property
    def is_template(self) -> bool:
        """An ``include`` target: not selectable, and "gcode" in the name (03 section 3.5)."""
        return not self.instantiation and "gcode" in self.name.lower()


def make_entry(kind: str, vendor: str, path: str, data: dict[str, Any]) -> Entry:
    """Reduce a preset JSON object to an index entry."""
    name = str(data.get("name") or os.path.splitext(os.path.basename(path))[0])
    entry = Entry(kind, vendor, name, path)
    entry.instantiation = str(data.get("instantiation", "false")).lower() in ("true", "1")
    entry.inherits = str(data.get("inherits") or "")
    entry.include = as_list(data.get("include"))
    for key in _LIST_FIELDS:
        if key in data:
            setattr(entry, key, as_list(data[key]))
    for key in _COND_FIELDS:
        if key in data:
            setattr(entry, key, str(data[key]).strip())
    entry.printer_model = str(data.get("printer_model") or "")
    entry.printer_variant = str(data.get("printer_variant") or "")
    entry.renamed_from = as_list(data.get("renamed_from"))
    for key in _INFO_FIELDS:
        value = data.get(key)
        if value is not None:
            entry.info[key] = ";".join(as_list(value)) if isinstance(value, list) else str(value)
    return entry


class ProfileIndex:
    """All presets by kind, vendor and name, with the lookups resolution and compatibility need."""

    def __init__(self, entries: list[Entry], vendors: dict[str, dict[str, str]],
                 commit: str = "", archive_size: int = 0, skipped: int = 0) -> None:
        self.entries = entries
        self.vendors = vendors
        self.commit = commit
        self.archive_size = archive_size
        self.skipped = skipped                  # unreadable files left out of the index
        self._by_key = {(e.kind, e.vendor, e.name): e for e in entries}
        self._by_id = {(e.kind, e.id): e for e in entries}

    def __len__(self) -> int:
        return len(self.entries)

    def of_kind(self, kind: str, *, selectable_only: bool = False) -> list[Entry]:
        return [e for e in self.entries
                if e.kind == kind and (e.instantiation or not selectable_only)]

    def get(self, kind: str, preset_id: str) -> Entry | None:
        """Look up a ``sys:<vendor>/<name>`` id."""
        return self._by_id.get((kind, preset_id))

    def get_or_renamed(self, kind: str, preset_id: str) -> Entry | None:
        """``get``, falling back to the preset that lists this name in ``renamed_from`` (03 section 2.5)."""
        found = self.get(kind, preset_id)
        if found is not None or not preset_id.startswith("sys:") or "/" not in preset_id:
            return found
        vendor, _, old_name = preset_id[len("sys:"):].partition("/")
        for entry in self.entries:
            if entry.kind == kind and entry.vendor == vendor and old_name in entry.renamed_from:
                return entry
        return None

    def lookup(self, kind: str, name: str, vendor: str) -> Entry | None:
        """A preset by name: in ``vendor`` first, then in the shared library (03 section 3.5)."""
        return self._by_key.get((kind, vendor, name)) or self._by_key.get((kind, LIBRARY_VENDOR, name))

    def parent(self, entry: Entry) -> Entry | None:
        """The ``inherits`` target; ``None`` when the preset has none. Raises if it is unknown."""
        if not entry.inherits:
            return None
        found = self.lookup(entry.kind, entry.inherits, entry.vendor)
        if found is None:
            raise ProfileError(f"{entry.id}: parent {entry.inherits!r} not found "
                               f"in {entry.vendor} or {LIBRARY_VENDOR}")
        return found

    def chain(self, entry: Entry) -> list[Entry]:
        """The preset followed by its ancestors, nearest first. Raises on a cycle or missing parent."""
        out, seen = [entry], {entry.id}
        while True:
            parent = self.parent(out[-1])
            if parent is None:
                return out
            if parent.id in seen:
                raise ProfileError(f"inheritance cycle at {parent.id}")
            seen.add(parent.id)
            out.append(parent)

    # -- cache ---------------------------------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps({"format": CACHE_FORMAT, "commit": self.commit,
                           "archive_size": self.archive_size, "skipped": self.skipped,
                           "vendors": self.vendors, "entries": [asdict(e) for e in self.entries]},
                          separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "ProfileIndex":
        data = json.loads(text)
        if data.get("format") != CACHE_FORMAT:
            raise ValueError("index cache format changed")
        entries = [Entry(**e) for e in data["entries"]]
        return cls(entries, data["vendors"], data["commit"], data["archive_size"], data["skipped"])


def cache_path(cache_dir: str, orca_commit: str) -> str:
    """``profile-index-<orca_commit>.json`` under the extension's cache directory (03 section 2.5)."""
    safe = "".join(c for c in orca_commit if c.isalnum()) or "unknown"
    return os.path.join(cache_dir, f"profile-index-{safe}.json")


def load_cache(path: str, orca_commit: str, archive_size: int) -> ProfileIndex | None:
    """The cached index, or ``None`` if it is missing, stale or unreadable (never raises)."""
    try:
        with open(path, encoding="utf-8") as fh:
            index = ProfileIndex.from_json(fh.read())
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if index.commit != orca_commit or index.archive_size != archive_size:
        return None
    return index


def save_cache(index: ProfileIndex, path: str) -> bool:
    """Write the cache atomically. A failure is logged and reported, not raised."""
    part = path + ".part"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(part, "w", encoding="utf-8") as fh:
            fh.write(index.to_json())
        os.replace(part, path)
        return True
    except OSError:
        log.warning("could not write the profile index cache %s", path, exc_info=True)
        return False


def load_or_build(source: ProfileSource, cache_dir: str | None, commit: str,
                  **build_kwargs: Any) -> Iterator[tuple[float, str]]:
    """Generator task: the cached index when valid (about one step), else build and cache it."""
    path = cache_path(cache_dir, commit) if cache_dir else None
    if path is not None:
        cached = load_cache(path, commit, source.size())
        if cached is not None:
            yield 1.0, "Printer library loaded"
            return cached
    index = yield from build_index(source, commit=commit, **build_kwargs)
    if path is not None:
        save_cache(index, path)
    return index


def build_index(source: ProfileSource, *, commit: str = "", step_files: int = 300,
                step_budget_s: float = 0.010,
                clock: Callable[[], float] = time.perf_counter
                ) -> Iterator[tuple[float, str]]:
    """Generator task: parse every preset header, yielding ``(fraction, message)`` per step.

    Returns the finished :class:`ProfileIndex`. Unreadable files are skipped and counted.
    """
    if step_files < 1:
        raise ValueError("step_files must be at least 1")
    vendors: dict[str, dict[str, str]] = {}
    for path in source.vendor_headers():
        try:
            header = source.read_json(path)
        except ProfileError as exc:
            log.warning("skipping vendor header: %s", exc)
            continue
        name = str(header.get("name") or path[:-len(".json")])
        vendors[name] = {"version": str(header.get("version", "")), "path": path}
    paths = source.preset_paths()
    entries: list[Entry] = []
    skipped = 0
    total = max(1, len(paths))
    done = 0
    while done < len(paths):
        started, count = clock(), 0
        while done < len(paths) and count < step_files and (count == 0 or clock() - started < step_budget_s):
            path = paths[done]
            done += 1
            count += 1
            vendor, folder, _ = path.split("/")
            try:
                data = source.read_json(path)
            except ProfileError as exc:
                log.warning("skipping preset: %s", exc)
                skipped += 1
                continue
            kind = _KIND_OF_TYPE.get(str(data.get("type", "")), _KIND_OF_DIR[folder])
            entries.append(make_entry(kind, vendor, path, data))
            vendors.setdefault(vendor, {"version": "", "path": ""})
        yield done / total, f"Loading printer library… {int(100 * done / total)}%"
    return ProfileIndex(entries, vendors, commit, source.size(), skipped)
