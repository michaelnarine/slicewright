# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility filtering of filament and process presets (03 section 3.6).

Conditions are evaluated by the engine (``ConditionContext``); the list and library rules are ours.
For a candidate preset P and a subject M (the printer, or for a filament's ``compatible_prints`` the
process):

1. **Library exclusion.** A preset of ``OrcaFilamentLibrary`` with an empty ``compatible_printers`` is
   hidden when a printer-specific preset with the same alias lists the printer (or its ``inherits``).
2. **Condition.** With an empty list and a condition, evaluate it over M's resolved config plus
   ``printer_preset`` and ``num_extruders``. A ``ConfigError`` counts as compatible and is logged.
3. **List.** Otherwise P is compatible when its list is empty, names M, or M is a user preset whose
   ``inherits`` is listed.
4. **Process.** A filament must also pass 2 and 3 with its ``compatible_prints`` fields against the process.
5. **Vendor.** Nothing here looks at vendors, so cross-vendor matches are allowed.

A preset's list and condition fields are inherited like any other key (nearest ancestor that has the
field wins; an explicitly empty list does count as present). No ``bpy`` here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .index import LIBRARY_VENDOR, Entry, ProfileIndex
from .resolve import Resolver
from .source import ProfileError

log = logging.getLogger("slicewright.profiles")


@dataclass
class Subject:
    """What candidates are tested against: a printer or a process preset (system or user)."""
    key: str                        # cache key, e.g. ``sys:Acme/Acme Maker 1 0.4 nozzle`` or ``user:Mine``
    name: str
    config: dict[str, Any]          # the resolved config
    inherits: str = ""
    is_user: bool = False


def subject_from_entry(resolver: Resolver, kind: str, entry: Entry) -> Subject:
    resolved = resolver.resolve(kind, entry.id)
    return Subject(entry.id, entry.name, resolved.config, entry.inherits, False)


def _num_extruders(config: dict[str, Any]) -> int:
    value = config.get("nozzle_diameter", "")
    parts = value if isinstance(value, list) else str(value).split(",")
    return max(1, len([p for p in parts if str(p).strip()]))


class Compat:
    def __init__(self, sc: Any, index: ProfileIndex, resolver: Resolver) -> None:
        self.sc, self.index, self.resolver = sc, index, resolver
        self._contexts: dict[str, Any] = {}
        self._fields: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, dict[str, list[Entry]]] = {}
        self._results: dict[tuple, list[Entry]] = {}
        self.evaluations = 0

    def invalidate(self) -> None:
        """Drop every cached result (call after a user preset changed)."""
        self._contexts.clear()
        self._results.clear()

    # -- inherited header fields ---------------------------------------------------------------

    def _effective(self, entry: Entry) -> dict[str, Any]:
        cached = self._fields.get(entry.id)
        if cached is not None:
            return cached
        fields: dict[str, Any] = {}
        for link in self.index.chain(entry):                  # nearest first; raises if broken
            for name in ("compatible_printers", "compatible_printers_condition",
                         "compatible_prints", "compatible_prints_condition"):
                value = getattr(link, name)
                if value is not None and name not in fields:
                    fields[name] = value
        self._fields[entry.id] = fields
        return fields

    # -- the rules -----------------------------------------------------------------------------

    def _context(self, subject: Subject):
        ctx = self._contexts.get(subject.key)
        if ctx is None:
            config = dict(subject.config)
            config["printer_preset"] = subject.name
            config["num_extruders"] = str(_num_extruders(subject.config))
            ctx = self._contexts[subject.key] = self.sc.ConditionContext(config)
        return ctx

    def _list_or_condition(self, names: list[str], condition: str, subject: Subject,
                           who: str) -> bool:
        if not names and condition:
            self.evaluations += 1
            try:
                return bool(self._context(subject).eval(condition))
            except self.sc.ConfigError as exc:
                log.warning("%s: compatibility condition failed (%s); treating as compatible", who, exc)
                return True
        if not names:
            return True
        if subject.name in names:
            return True
        return subject.is_user and bool(subject.inherits) and subject.inherits in names

    def _hidden_by_library_rule(self, entry: Entry, printer: Subject) -> bool:
        if entry.vendor != LIBRARY_VENDOR or self._effective(entry).get("compatible_printers"):
            return False
        wanted = {printer.name, printer.inherits} - {""}
        for other in self._alias_map(entry.kind).get(entry.alias, ()):
            try:
                listed = self._effective(other).get("compatible_printers") or []
            except ProfileError:
                continue
            if wanted & set(listed):
                return True
        return False

    def _alias_map(self, kind: str) -> dict[str, list[Entry]]:
        found = self._aliases.get(kind)
        if found is None:
            found = {}
            for e in self.index.of_kind(kind, selectable_only=True):
                if e.vendor != LIBRARY_VENDOR:
                    found.setdefault(e.alias, []).append(e)
            self._aliases[kind] = found
        return found

    def printer_compatible(self, entry: Entry, printer: Subject) -> bool:
        """Is the process or filament ``entry`` compatible with ``printer``? (rules 1 to 3)."""
        try:
            fields = self._effective(entry)
        except ProfileError as exc:
            log.warning("hiding %s: %s", entry.id, exc)
            return False
        if self._hidden_by_library_rule(entry, printer):
            return False
        return self._list_or_condition(fields.get("compatible_printers") or [],
                                       fields.get("compatible_printers_condition") or "",
                                       printer, entry.id)

    def process_compatible(self, entry: Entry, process: Subject) -> bool:
        """Does the filament ``entry`` accept ``process``? (rule 4)."""
        try:
            fields = self._effective(entry)
        except ProfileError:
            return False
        return self._list_or_condition(fields.get("compatible_prints") or [],
                                       fields.get("compatible_prints_condition") or "",
                                       process, entry.id)

    # -- listings ------------------------------------------------------------------------------

    def compatible_processes(self, printer: Subject) -> list[Entry]:
        key = ("process", printer.key)
        if key not in self._results:
            self._results[key] = [e for e in self.index.of_kind("process", selectable_only=True)
                                  if self.printer_compatible(e, printer)]
        return self._results[key]

    def compatible_filaments(self, printer: Subject, process: Subject | None = None) -> list[Entry]:
        key = ("filament", printer.key, process.key if process else "")
        if key not in self._results:
            out = []
            for e in self.index.of_kind("filament", selectable_only=True):
                if self.printer_compatible(e, printer) and (
                        process is None or self.process_compatible(e, process)):
                    out.append(e)
            self._results[key] = out
        return self._results[key]
