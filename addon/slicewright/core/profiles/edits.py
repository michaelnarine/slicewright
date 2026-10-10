# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit buffers as presets: which keys belong to a role, diffing against the parent, file form.

An edit buffer (``ConfigPG``) holds *every* schema key as FlatConfig text. A preset only owns the keys of
its role (``schema[key]["preset"]``), so a diff or an embed first narrows the buffer to that role. Values
leave this module in the form Orca writes into preset files: a string, or a list of strings for the
per-extruder vector types. No ``bpy`` here.
"""
from __future__ import annotations

from typing import Any

from ..config_codec import flatten, format_value, parse_value, spec_for, split_vector
from .resolve import SCHEMA_PRESET
from .user import diff_config

VECTOR_TYPES = ("floats", "ints", "strings", "points", "percents", "floatsOrPercents", "bools", "enums")


def role_of_kind(kind: str) -> str:
    return SCHEMA_PRESET[kind]


def role_flat(schema: dict[str, dict], kind: str, flat: dict[str, str]) -> dict[str, str]:
    """The keys of ``flat`` that belong to presets of ``kind`` (unknown keys are kept: they may be orphans)."""
    role = role_of_kind(kind)
    return {k: v for k, v in flat.items() if k not in schema or schema[k].get("preset") == role}


def canonical(schema: dict[str, dict], key: str, text: str) -> str:
    """``text`` as the edit buffer would hold it, so ``0.20`` and ``0.2`` are not a difference. Values
    that do not parse as their type are returned unchanged."""
    if key not in schema:
        return text
    spec = spec_for(key, schema[key])
    try:
        return format_value(spec, parse_value(spec, text))
    except ValueError:
        return text


def parent_flat(schema: dict[str, dict], config: dict[str, Any]) -> dict[str, str]:
    """A resolved preset config (values ``str`` or ``list[str]``) as canonical FlatConfig text."""
    return {k: canonical(schema, k, flatten(schema[k]["type"] if k in schema else "", v))
            for k, v in config.items()}


def file_value(schema: dict[str, dict], key: str, text: str) -> str | list[str]:
    """FlatConfig text as the value Orca writes into a preset file (vectors as lists)."""
    stype = schema.get(key, {}).get("type", "")
    return split_vector(stype, text) if stype in VECTOR_TYPES else text


def diff_flat(schema: dict[str, dict], kind: str, parent_config: dict[str, Any],
              edited_flat: dict[str, str]) -> dict[str, str]:
    """Keys (of the role) whose edited text differs from the parent's, as FlatConfig text."""
    return diff_config(parent_flat(schema, parent_config), role_flat(schema, kind, edited_flat))


def diff_for_file(schema: dict[str, dict], kind: str, parent_config: dict[str, Any],
                  edited_flat: dict[str, str]) -> dict[str, str | list[str]]:
    """``diff_flat`` with each value in preset-file form."""
    return {k: file_value(schema, k, v) for k, v in diff_flat(schema, kind, parent_config, edited_flat).items()}


def rows(schema: dict[str, dict], kind: str, parent_config: dict[str, Any],
         edited_flat: dict[str, str]) -> list[tuple[str, str, str, str]]:
    """``(key, label, parent text, edited text)`` per change, sorted by label, for the diff popup."""
    parent = parent_flat(schema, parent_config)
    out = []
    for key, text in diff_flat(schema, kind, parent_config, edited_flat).items():
        label = schema.get(key, {}).get("label") or key
        out.append((key, label, parent.get(key, ""), text))
    return sorted(out, key=lambda r: (r[1].lower(), r[0]))
