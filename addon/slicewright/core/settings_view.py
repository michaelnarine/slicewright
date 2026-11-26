# SPDX-License-Identifier: GPL-3.0-or-later
"""How the Settings panel is organised and sized, as plain data (03 section 2.4). No ``bpy`` here.

The Blender side (``blender/ui/settings.py``) only draws what this module decides:

* **Sections**: the engine's pages are collapsible sections and the groups of a page are collapsible groups inside it.
  Which are open is a set of ids (``section_id``, ``group_id``) kept as text by the caller (a ``WindowManager``
  property: per session, never in saved preferences).
* **Favourites**: setting keys the user pinned, kept as text too (a scene property, so they travel with the file).
* **Plans**: for one page, the groups with the keys that pass the detail level and the search words, and the counts
  (settings, modified) the collapsed header shows as its summary line.
* **Row style and columns**: how one setting row is laid out for the width the region gives (``row_style``,
  ``columns_for``), reusing ``choice_layout.width_chars`` for the width in characters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from . import choice_layout, descriptions
from .settings_layout import Group, Page, mode_allows

FAVOURITES_ID = "favourites"
SEP = "\n"                                   # ids and keys never contain a newline

# -- open sections and pinned keys: ordered sets kept as text -------------------------------------------------------


def parse_set(text: str) -> list[str]:
    """The items of a newline-separated ordered set (blank lines and repeats dropped, order kept)."""
    seen: dict[str, None] = {}
    for item in text.split(SEP):
        item = item.strip()
        if item:
            seen.setdefault(item, None)
    return list(seen)


def join_set(items: Iterable[str]) -> str:
    return SEP.join(items)


def contains(text: str, item: str) -> bool:
    return item in parse_set(text)


def toggled(text: str, item: str) -> str:
    """``text`` with ``item`` added at the end, or removed when it was already in."""
    items = parse_set(text)
    if item in items:
        items.remove(item)
    else:
        items.append(item)
    return join_set(items)


def added(text: str, items: Iterable[str]) -> str:
    have = parse_set(text)
    have.extend(i for i in items if i not in have)
    return join_set(have)


def section_id(role: str, page: str) -> str:
    return f"{role}/{page}"


def group_id(role: str, page: str, index: int) -> str:
    """A group is named by its position, not its title: two groups of a page may share a title."""
    return f"{role}/{page}/{index}"


def pinned(text: str, schema: dict[str, dict]) -> list[str]:
    """The pinned keys the current engine still knows, in the order they were pinned."""
    return [k for k in parse_set(text) if k in schema]


# -- plans: what a page shows --------------------------------------------------------------------------------------


@dataclass
class GroupPlan:
    index: int
    group: Group
    keys: list[str]
    modified: int = 0

    @property
    def count(self) -> int:
        return len(self.keys)


@dataclass
class PagePlan:
    page: Page
    groups: list[GroupPlan] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(g.count for g in self.groups)

    @property
    def modified(self) -> int:
        return sum(g.modified for g in self.groups)

    @property
    def flat(self) -> bool:
        """One group that repeats the page's name (or has none): the page needs no group header inside it."""
        return len(self.page.groups) == 1 and self.page.groups[0].title in ("", self.page.name)


def visible_keys(page: Page, schema: dict[str, dict], level: str, show_develop: bool = False
                 ) -> list[tuple[int, Group, list[str]]]:
    """``(group index, group, keys)`` of ``page`` at detail ``level`` (no search). Empty groups are kept only when they
    have a custom drawer. The structure depends only on the schema, the level and ``show_develop``, so the caller may
    cache it."""
    out = []
    for i, group in enumerate(page.groups):
        keys = [k for k in group.keys if mode_allows(schema[k].get("mode", "simple"), level, show_develop)]
        if keys or group.custom:
            out.append((i, group, keys))
    return out


class SearchIndex:
    """Lower-case search text per key (label, key, corrected tooltip), built lazily once per schema."""

    def __init__(self, schema: dict[str, dict]):
        self.schema = schema
        self._text: dict[str, str] = {}

    def haystack(self, key: str) -> str:
        found = self._text.get(key)
        if found is None:
            entry = self.schema[key]
            found = f"{entry.get('label', '')} {key} {descriptions.corrected(key, entry.get('tooltip', ''))}".lower()
            self._text[key] = found
        return found

    def match(self, key: str, words: list[str]) -> bool:
        hay = self.haystack(key)
        return all(w in hay for w in words)


def words_of(text: str) -> list[str]:
    return text.lower().split()


def plan_page(page: Page, visible: list[tuple[int, Group, list[str]]], modified: set[str],
              index: SearchIndex | None = None, text: str = "") -> PagePlan:
    """The plan of ``page`` from its cached ``visible`` structure: keys narrowed by the search ``text`` (every word must
    occur) and the number of ``modified`` keys per group. Empty groups drop out while searching."""
    words = words_of(text)
    plan = PagePlan(page)
    for i, group, keys in visible:
        if words and index is not None:
            keys = [k for k in keys if index.match(k, words)]
            if not keys:
                continue
        plan.groups.append(GroupPlan(i, group, keys, sum(1 for k in keys if k in modified)))
    return plan


def summary(count: int, modified: int, searching: bool = False) -> str:
    """The short line in a collapsed header: how many are modified, else how many settings it holds."""
    if modified:
        return f"{modified} modified"
    if searching:
        return f"{count} found"
    return str(count)


# -- width: columns and row styles ---------------------------------------------------------------------------------

WIDE_CHARS = 76          # at least this many characters across: two columns of rows
WIDER_CHARS = 150        # and this many: three
CHROME_CHARS = 9         # per cell: box margin, the star, the reset button and the scroll bar
NATIVE_LABEL_SHARE = 0.4   # Blender's own label column (``use_property_split``) takes this share of the row
NATIVE_MIN_CELL = 36     # narrower than this (the sidebar) every label is left-aligned by an explicit split, so rows line up
MAX_LABEL_SHARE = 0.62   # a label that needs more than this of the cell goes above its field instead

INLINE, NATIVE, SPLIT, STACKED = "inline", "native", "split", "stacked"


def columns_for(width_chars: float) -> int:
    """How many columns of setting rows fit ``width_chars`` (1 in the sidebar, 2 in a wide area)."""
    if width_chars >= WIDER_CHARS:
        return 3
    return 2 if width_chars >= WIDE_CHARS else 1


def cell_chars(width_chars: float, columns: int) -> float:
    return max(width_chars / max(columns, 1) - CHROME_CHARS, 8.0)


@dataclass(frozen=True)
class RowStyle:
    kind: str                 # INLINE, NATIVE, SPLIT or STACKED
    factor: float = 0.0       # SPLIT: the label's share of the row


def row_style(label: str, boolean: bool, cell: float) -> RowStyle:
    """Label-left, value-right when the label fits, the label above its field when it does not.

    * ``NATIVE``: Blender's own ``use_property_split`` (label column 40 %), when the label fits that column.
    * ``SPLIT``: an explicit split giving the label just the share it needs, up to ``MAX_LABEL_SHARE``.
    * ``STACKED``: the label (wrapped) above a full-width field; a checkbox keeps its own label when it fits.
    """
    need = len(label) + 2
    if cell >= NATIVE_MIN_CELL and need <= cell * NATIVE_LABEL_SHARE:
        return RowStyle(NATIVE)
    if need <= cell * MAX_LABEL_SHARE:
        return RowStyle(SPLIT, round(need / cell, 3))
    if boolean and len(label) + 3 <= cell:
        return RowStyle(INLINE)
    return RowStyle(STACKED)


def choice_width(width_chars: float, columns: int) -> float:
    """The width ``choice_layout.choose`` should assume for one cell."""
    return max(width_chars / max(columns, 1) - (0 if columns <= 1 else 6), 0.0)


def region_chars(region_px: float | None, ui_scale: float = 1.0) -> float:
    return choice_layout.width_chars(region_px, ui_scale)
