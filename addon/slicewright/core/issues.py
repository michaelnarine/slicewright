# SPDX-License-Identifier: GPL-3.0-or-later
"""The merged Issues list (03 section 6.5): ordering, de-duplication, counts and click targets. No ``bpy``.

Issues are the ``Issue`` dicts of 04 section 2.6 (``level``, ``code``, ``message``, ``opt_key``,
``object_name``). They come from several sources: the engine's warnings and a failed slice's validation
errors (source ``slice``) and our own plate checks (source ``check``, from Check plate). :class:`IssueBook`
holds one list per source and merges them on demand; the Blender side mirrors the result into a UIList.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

ERROR, WARNING, INFO = "error", "warning", "info"
LEVEL_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}
# UI icons (Blender icon identifiers): a red cross, a warning triangle, an information mark.
LEVEL_ICON = {ERROR: "CANCEL", WARNING: "ERROR", INFO: "INFO"}
# Our checks and the engine's validation both report these per object; the pair (code, object) is one problem.
_PER_OBJECT_CODES = ("object_outside_bed", "object_too_tall")

SLICE, CHECK = "slice", "check"
SOURCE_ORDER = (SLICE, CHECK)           # on a duplicate the earlier source's wording wins (the engine's own)


def merge(*lists: Iterable[dict] | None) -> list[dict]:
    """Merge Issue lists: the first of two issues about the same thing wins; errors, then warnings, then info,
    each level keeping its input order. Object-level codes match on ``(code, object_name)``, others on the
    whole issue."""
    seen: set = set()
    merged: list[dict] = []
    for issues in lists:
        for i in issues or ():
            if i.get("code") in _PER_OBJECT_CODES:
                k: tuple = (i["code"], i.get("object_name"))
            else:
                k = (i.get("level"), i.get("code"), i.get("message"), i.get("opt_key"), i.get("object_name"))
            if k not in seen:
                seen.add(k)
                merged.append(i)
    merged.sort(key=lambda i: LEVEL_ORDER.get(i.get("level"), 3))     # stable: input order within a level
    return merged


UNKNOWN_KEY = "unknown_key"                 # the engine's code (04 section 2.6), a warning
PRESET_KEYS_UNKNOWN = "preset_keys_unknown"   # ours: the summary row for the unknown keys a preset brought along
QUIET_CODES = frozenset({PRESET_KEYS_UNKNOWN})   # informational rows the panel keeps out of the list and the counts


def is_quiet(issue: dict) -> bool:
    """A row that is information for the curious, not a problem: kept out of the Issues list and its counts."""
    return issue.get("code") in QUIET_CODES


def visible(issues: Iterable[dict]) -> list[dict]:
    """The rows the Issues list shows (everything but the quiet ones), in order."""
    return [i for i in issues or () if not is_quiet(i)]


def ignored_keys(issues: Iterable[dict]) -> list[str]:
    """The profile keys the engine does not know (the ``detail`` of the ``preset_keys_unknown`` row), sorted."""
    for i in issues or ():
        if i.get("code") == PRESET_KEYS_UNKNOWN and i.get("detail"):
            return [k for k in (p.strip() for p in i["detail"].split(",")) if k]
    return []


def ignored_label(n: int) -> str:
    return f"{n} profile settings ignored" if n != 1 else "1 profile setting ignored"


def collapse_preset_unknown_keys(issues: Iterable[dict], preset_keys) -> list[dict]:
    """Demote the engine's ``unknown_key`` warnings about keys that came unchanged from a selected preset.

    Bambu Studio presets carry dozens of keys Orca 2.4.2 does not know (the engine drops them and says so, 04
    section 6.2). Their being there is not the user's doing and not actionable, so those warnings become ONE
    ``info`` row, ``preset_keys_unknown``, "N profile settings ignored", whose ``detail`` lists the keys (the panel keeps
    it out of the list and the counts and shows it behind a toggle, 03 section 6.5). An ``unknown_key`` warning for
    a key that is not in ``preset_keys`` (one the user's edits or overrides put there) stays a warning, and every
    other issue is untouched. The summary takes the place of the first demoted warning."""
    keys = frozenset(preset_keys or ())
    out: list[dict] = []
    demoted: list[str] = []
    slot = -1
    for i in issues or ():
        if i.get("code") == UNKNOWN_KEY and i.get("opt_key") in keys:
            if i["opt_key"] not in demoted:
                demoted.append(i["opt_key"])
            if slot < 0:
                slot = len(out)
                out.append({})                  # placeholder: the summary goes where the first one was
            continue
        out.append(i)
    if slot >= 0:
        n = len(demoted)
        message = ignored_label(n)
        out[slot] = {"level": INFO, "code": PRESET_KEYS_UNKNOWN, "message": message, "opt_key": None,
                     "object_name": None, "detail": ", ".join(sorted(demoted))}
    return out


@dataclass(frozen=True)
class Counts:
    errors: int = 0
    warnings: int = 0
    infos: int = 0

    @property
    def total(self) -> int:
        return self.errors + self.warnings + self.infos


def counts(issues: Iterable[dict]) -> Counts:
    """Errors, warnings and notes of the rows the list shows: quiet rows (the ignored profile keys) do not count."""
    n = {ERROR: 0, WARNING: 0, INFO: 0}
    for i in issues:
        if i.get("level") in n and not is_quiet(i):
            n[i["level"]] += 1
    return Counts(n[ERROR], n[WARNING], n[INFO])


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def summary(c: Counts) -> str:
    """``2 errors, 1 warning`` for the panel; ``No issues`` when empty."""
    parts = [_plural(n, w) for n, w in ((c.errors, "error"), (c.warnings, "warning"), (c.infos, "note")) if n]
    return ", ".join(parts) or "No issues"


def icon(level: str) -> str:
    return LEVEL_ICON.get(level, "INFO")


_INSTANCE_SUFFIX = re.compile(r"#\d+$")


def base_object_name(name: str) -> str:
    """The Blender object name behind an engine-side name: ``Bolt#3`` (an instance) is ``Bolt``; the
    ``Name (extra)`` form some messages use loses its parenthesis."""
    return _INSTANCE_SUFFIX.sub("", name.split(" (")[0])


def targets(issue: dict) -> tuple[str | None, str | None]:
    """``(object_name, opt_key)`` a click on ``issue`` acts on; each is ``None`` when the issue has none."""
    return (issue.get("object_name") or None, issue.get("opt_key") or None)


class IssueBook:
    """Per-source issue lists and their merge. A source is replaced as a whole, never appended to."""

    def __init__(self) -> None:
        self._sources: dict[str, list[dict]] = {}

    def set(self, source: str, issues: Iterable[dict]) -> list[dict]:
        self._sources[source] = [dict(i) for i in issues]
        return self.merged()

    def clear(self, *sources: str) -> list[dict]:
        """Drop ``sources`` (all of them when none is named)."""
        if sources:
            for s in sources:
                self._sources.pop(s, None)
        else:
            self._sources.clear()
        return self.merged()

    def merged(self) -> list[dict]:
        ordered = [s for s in SOURCE_ORDER if s in self._sources] + [s for s in self._sources if s not in SOURCE_ORDER]
        return merge(*(self._sources[s] for s in ordered))
