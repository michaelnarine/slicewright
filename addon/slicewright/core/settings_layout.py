# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings pages, modes and the filter, as plain data (03 section 2.4). No ``bpy`` here.

Pages come from ``sc.tab_layout()[role]`` (``role`` is ``printer``, ``process`` or ``filament``). Keys of
that role that the layout does not mention fall back to the schema ``category`` (which covers only part
of the options), so nothing is unreachable. Modes hide options by their schema ``mode``; the filter is a
substring match over label, key and tooltip.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ROLES = ("printer", "process", "filament")
MODES = ("simple", "advanced", "expert")        # increasing detail; "develop" is hidden unless asked for
FALLBACK_PAGE = "Other"


@dataclass
class Group:
    title: str
    keys: list[str]
    custom: str | None = None                   # name of a hand-written drawer (bed_shape, extruder_count, ...)
    wiki: dict[str, str] = field(default_factory=dict)


@dataclass
class Page:
    name: str
    icon: str
    groups: list[Group]

    def keys(self) -> list[str]:
        return [k for g in self.groups for k in g.keys]


def build_pages(role: str, layout: dict[str, list[dict]], schema: dict[str, dict]) -> list[Page]:
    """The pages of ``role``: the engine layout first (keys the schema no longer has are dropped), then
    one fallback group per schema category for the role's keys the layout missed."""
    pages: list[Page] = []
    seen: set[str] = set()
    for raw in layout.get(role, []):
        groups = []
        for g in raw.get("groups", []):
            keys = [k for k in g.get("keys", []) if k in schema]
            seen.update(keys)
            custom = g.get("custom")
            if keys or custom:
                groups.append(Group(g.get("title", ""), keys, custom, dict(g.get("wiki") or {})))
        if groups:
            pages.append(Page(raw.get("page", ""), raw.get("icon", ""), groups))
    by_name = {p.name: p for p in pages}
    leftovers: dict[str, list[str]] = {}
    for key, entry in schema.items():
        if entry.get("preset") == role and key not in seen:
            leftovers.setdefault(entry.get("category") or FALLBACK_PAGE, []).append(key)
    for category, keys in leftovers.items():
        keys.sort(key=lambda k: schema[k].get("label", k).lower())
        if category in by_name:                              # the layout has this page: add a trailing group
            by_name[category].groups.append(Group("More", keys))
        else:
            page = Page(category, "", [Group(category, keys)])
            pages.append(page)
            by_name[category] = page
    return pages


def mode_allows(option_mode: str, level: str, show_develop: bool = False) -> bool:
    """Is an option of ``option_mode`` visible at ``level``? ``develop`` options need ``show_develop``."""
    if option_mode == "develop":
        return show_develop
    if option_mode not in MODES:                            # an unknown mode is shown rather than lost
        return True
    return MODES.index(option_mode) <= MODES.index(level)


def matches(key: str, entry: dict, text: str) -> bool:
    """Every word of ``text`` occurs in the label, key or tooltip (case-insensitive)."""
    haystack = f"{entry.get('label', '')} {key} {entry.get('tooltip', '')}".lower()
    return all(word in haystack for word in text.lower().split())


def visible_groups(page: Page, schema: dict[str, dict], level: str, text: str = "",
                   show_develop: bool = False) -> list[tuple[Group, list[str]]]:
    """The groups of ``page`` with the keys that pass mode and filter; empty groups are dropped, except a
    custom-drawer group when nothing is being filtered."""
    out = []
    for group in page.groups:
        keys = [k for k in group.keys
                if mode_allows(schema[k].get("mode", "simple"), level, show_develop)
                and (not text.strip() or matches(k, schema[k], text))]
        if keys or (group.custom and not text.strip()):
            out.append((group, keys))
    return out


def find_page(pages: list[Page], name: str) -> Page | None:
    for page in pages:
        if page.name == name:
            return page
    return pages[0] if pages else None


def bed_bounds(points: str) -> tuple[float, float] | None:
    """Width and height of the bounding box of a ``0x0,220x0,...`` point list, or ``None`` if unparsable."""
    xs, ys = [], []
    try:
        for part in points.split(","):
            if part.strip():
                x, y = part.lower().split("x")
                xs.append(float(x))
                ys.append(float(y))
    except ValueError:
        return None
    return (max(xs) - min(xs), max(ys) - min(ys)) if xs else None
