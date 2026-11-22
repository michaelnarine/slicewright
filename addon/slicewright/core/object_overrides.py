# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-object setting overrides as plain data (03 sections 1.2, 2.2 and 4.1). No ``bpy``.

An object may override the keys whose schema ``scope`` is ``object`` or ``region`` (04 sections 2.5 and 6.4); the
engine refuses any other key in ``add_object(config_overrides=...)`` with a ``ConfigError`` that fails the whole slice.
:func:`sanitize` is what stands between a property group (which can hold anything a script or an older engine put
there) and the engine: it keeps the valid keys and turns the rest into Issues, so a stale or wrong override costs a
warning rather than the slice. :func:`offerable` is the list the "add an override" picker shows.
"""
from __future__ import annotations

from . import settings_layout as sl

PER_OBJECT_SCOPES = ("object", "region")
UNKNOWN_KEY = "override_unknown_key"            # our own Issue codes (the engine never sees these keys)
NOT_PER_OBJECT = "override_not_per_object"


def is_per_object(schema: dict[str, dict], key: str) -> bool:
    entry = schema.get(key)
    return entry is not None and entry.get("scope") in PER_OBJECT_SCOPES


def _issue(level: str, code: str, message: str, object_name: str | None, key: str) -> dict:
    return {"level": level, "code": code, "message": message, "opt_key": key, "object_name": object_name}


def sanitize(schema: dict[str, dict], flat: dict[str, str], object_name: str | None = None
             ) -> tuple[dict[str, str], list[dict]]:
    """``(overrides, issues)``: the keys of ``flat`` the engine accepts per object, and a warning Issue for every key
    that is dropped (unknown to the engine's schema, or valid only for the whole plate)."""
    kept: dict[str, str] = {}
    issues: list[dict] = []
    who = object_name or "An object"
    for key, value in flat.items():
        entry = schema.get(key)
        if entry is None:
            issues.append(_issue("warning", UNKNOWN_KEY,
                                 f"{who}: the override {key} is not a setting of this engine and is ignored",
                                 object_name, key))
        elif entry.get("scope") not in PER_OBJECT_SCOPES:
            issues.append(_issue("warning", NOT_PER_OBJECT,
                                 f"{who}: {entry.get('label') or key} ({key}) can only be set for the whole plate, "
                                 "so the override is ignored", object_name, key))
        else:
            kept[key] = value
    return kept, issues


def offerable(schema: dict[str, dict], level: str, show_develop: bool = False, exclude=()) -> list[str]:
    """Keys a user may add as an override at settings detail ``level``: per-object scope, allowed by the detail
    mode, not already in ``exclude``; sorted by label."""
    skip = set(exclude)
    keys = [k for k, e in schema.items()
            if e.get("scope") in PER_OBJECT_SCOPES and k not in skip
            and sl.mode_allows(e.get("mode", "simple"), level, show_develop)]
    keys.sort(key=lambda k: (schema[k].get("label") or k).lower())
    return keys


def pick_label(schema: dict[str, dict], key: str) -> str:
    """The picker's line for ``key``: the label and the key (the key is how people know ``zaa_enabled``)."""
    label = schema.get(key, {}).get("label") or key
    return f"{label}  ({key})"
