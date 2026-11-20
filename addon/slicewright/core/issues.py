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


@dataclass(frozen=True)
class Counts:
    errors: int = 0
    warnings: int = 0
    infos: int = 0

    @property
    def total(self) -> int:
        return self.errors + self.warnings + self.infos


def counts(issues: Iterable[dict]) -> Counts:
    n = {ERROR: 0, WARNING: 0, INFO: 0}
    for i in issues:
        if i.get("level") in n:
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
