# SPDX-License-Identifier: GPL-3.0-or-later
"""Preset resolution: defaults, then ``inherits``, then ``include``, then the preset's own keys.

Written from the behaviour in 03 section 3.5 and pinned by ``tests/unit/test_profiles_resolve.py``.
Everything after this step (composing printer, process and filaments, legacy keys, validation) is
the engine's. Resolution is lazy: a preset is resolved when first asked for and kept in a small LRU.
No ``bpy`` here.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .index import Entry, ProfileIndex, as_list
from .source import ProfileError, ProfileSource

log = logging.getLogger("slicewright.profiles")

# Keys that describe a preset file itself. They are never inherited or included: a child is only
# "instantiation: true" if its own file says so, and its name is its own.
IDENTITY_KEYS = frozenset({
    "name", "inherits", "include", "from", "instantiation", "type", "setting_id", "filament_id",
    "renamed_from", "version", "base_id", "is_custom_defined", "description",
})
# The schema's ``preset`` field for each preset kind (04 section 6.4).
SCHEMA_PRESET = {"machine": "printer", "process": "process", "filament": "filament"}
MAX_INCLUDE_DEPTH = 8


@dataclass
class ResolvedPreset:
    id: str
    kind: str
    name: str
    config: dict[str, Any]          # key -> str | list[str]; shared with the cache, do not mutate
    chain: list[str]                # ids of the preset and its ancestors, nearest first
    includes: list[str] = field(default_factory=list)       # names of the includes that were applied
    warnings: list[str] = field(default_factory=list)


def normalise_value(value: Any) -> Any:
    """A preset JSON value as the engine's ``PresetDict`` wants it: ``str`` or ``list[str]``."""
    if isinstance(value, list):
        return [v if isinstance(v, str) else str(v) for v in value]
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return value if isinstance(value, str) else None


def own_keys(data: dict[str, Any]) -> dict[str, Any]:
    """The config keys written in a preset file: everything except identity keys."""
    out = {}
    for key, value in data.items():
        if key in IDENTITY_KEYS:
            continue
        norm = normalise_value(value)
        if norm is not None:
            out[key] = norm
    return out


def schema_defaults(schema: dict[str, dict], kind: str) -> dict[str, str]:
    """Step 1: defaults of the schema keys whose ``preset`` matches ``kind``."""
    wanted = SCHEMA_PRESET[kind]
    return {k: v["default"] for k, v in schema.items() if v.get("preset") == wanted}


class Resolver:
    def __init__(self, index: ProfileIndex, source: ProfileSource, schema: dict[str, dict],
                 cache_size: int = 128) -> None:
        self.index, self.source = index, source
        self._defaults = {kind: schema_defaults(schema, kind) for kind in SCHEMA_PRESET}
        self._cache: OrderedDict[tuple[str, str], ResolvedPreset] = OrderedDict()
        self._cache_size = cache_size
        self.hits = self.misses = 0

    def resolve(self, kind: str, preset_id: str) -> ResolvedPreset:
        """Resolve ``sys:<vendor>/<name>``. Raises :class:`ProfileError` if it cannot be."""
        key = (kind, preset_id)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self.hits += 1
            return cached
        entry = self.index.get(kind, preset_id)
        if entry is None:
            raise ProfileError(f"unknown {kind} preset {preset_id!r}")
        self.misses += 1
        resolved = self.resolve_entry(entry)
        self._cache[key] = resolved
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return resolved

    def resolve_entry(self, entry: Entry) -> ResolvedPreset:
        if entry.kind not in SCHEMA_PRESET:
            raise ProfileError(f"{entry.id} is a {entry.kind}, which has no settings to resolve")
        chain = self.index.chain(entry)                  # raises on a cycle or a missing parent
        config: dict[str, Any] = dict(self._defaults[entry.kind])            # 1. defaults
        applied: list[str] = []
        warnings: list[str] = []
        for link in reversed(chain):                                         # 2. root ancestor first
            data = self.source.read_json(link.path)
            for name in as_list(data.get("include")):                        # 3. includes in order
                keys = self._include_keys(link, name, warnings, depth=0, seen={link.id})
                if keys is not None:
                    config.update(keys)
                    applied.append(name)
            config.update(own_keys(data))                                    # 4. own keys
        return ResolvedPreset(entry.id, entry.kind, entry.name, config,
                              [e.id for e in chain], applied, warnings)

    def _include_keys(self, owner: Entry, name: str, warnings: list[str], depth: int,
                      seen: set[str]) -> dict[str, Any] | None:
        """Keys contributed by the include ``name`` of ``owner``, or ``None`` if it is skipped."""
        target = self.index.lookup(owner.kind, name, owner.vendor)
        if target is None:
            warnings.append(f"{owner.id}: include {name!r} not found")
            return None
        if not target.is_template:
            warnings.append(f"{owner.id}: include {name!r} is not a G-code template; skipped")
            return None
        if target.id in seen or depth >= MAX_INCLUDE_DEPTH:
            warnings.append(f"{owner.id}: include {name!r} is circular or too deep; skipped")
            return None
        keys: dict[str, Any] = {}
        try:
            chain = self.index.chain(target)
        except ProfileError as exc:
            warnings.append(str(exc))
            return None
        for link in reversed(chain):          # a template may itself inherit and include
            data = self.source.read_json(link.path)
            for inner in as_list(data.get("include")):
                nested = self._include_keys(link, inner, warnings, depth + 1, seen | {target.id})
                if nested is not None:
                    keys.update(nested)
            keys.update(own_keys(data))
        return keys
