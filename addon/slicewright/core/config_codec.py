# SPDX-License-Identifier: GPL-3.0-or-later
"""Schema entry -> Blender property spec, and value <-> FlatConfig text (03 section 2.3, 04 section 2.5).

The engine's ``config_schema()`` entry for each key decides how the generated ``ConfigPG`` stores it:

=========================== ============================== =============
schema ``type``              property                       serialised
=========================== ============================== =============
bool                         Bool                           ``"1"`` / ``"0"``
int                          Int (soft min/max)             ``%d``
float                        Float (soft min/max)           ``%g``
percent                      Float, subtype PERCENTAGE      ``"15%"``
enum, closed                 Enum                           the value
everything else              String (raw FlatConfig text)   as typed
=========================== ============================== =============

"Everything else" is ``floatOrPercent``, open enums, strings, points, G-code and every per-extruder
vector (``floats``, ``ints``, ``strings``, ...): the property holds Orca's serialised text and the UI
draws per-element widgets. No ``bpy`` here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BOOL, INT, FLOAT, PERCENT, ENUM, STRING = "bool", "int", "float", "percent", "enum", "string"
UNBOUNDED = 1.0e9          # Blender needs finite hard limits for the schema's "no limit"


@dataclass
class Spec:
    key: str
    kind: str
    label: str
    description: str
    default: Any                                # Python value for the property default
    soft_min: float | None = None
    soft_max: float | None = None
    enum_items: list[tuple[str, str, str]] = field(default_factory=list)
    is_code: bool = False
    schema_type: str = ""


def _number(text: str, as_int: bool) -> int | float:
    value = float(str(text).strip().rstrip("%"))
    return int(value) if as_int else value


def spec_for(key: str, entry: dict) -> Spec:
    """The property spec for one schema entry. Never raises on odd defaults (they fall back to zero/empty)."""
    stype = entry.get("type", "string")
    label = entry.get("label") or key
    tooltip = entry.get("tooltip") or ""
    side = entry.get("sidetext") or ""
    description = tooltip + (f" ({side})" if side and side not in tooltip else "")
    raw_default = str(entry.get("default", ""))
    spec = Spec(key, STRING, label, description, raw_default, schema_type=stype,
                is_code=bool(entry.get("is_code")))
    lo, hi = entry.get("min"), entry.get("max")
    try:
        if stype == "bool":
            spec.kind, spec.default = BOOL, raw_default.strip().lower() in ("1", "true")
        elif stype in ("int", "float", "percent"):
            spec.kind = {"int": INT, "float": FLOAT, "percent": PERCENT}[stype]
            spec.default = _number(raw_default or "0", stype == "int")
            spec.soft_min = None if lo is None else float(lo)
            spec.soft_max = None if hi is None else float(hi)
        elif stype == "enum" and not entry.get("enum_open") and entry.get("enum"):
            spec.kind = ENUM
            spec.enum_items = [(e["value"], e.get("label") or e["value"], "") for e in entry["enum"]]
            values = [i[0] for i in spec.enum_items]
            spec.default = raw_default if raw_default in values else values[0]
    except ValueError:
        spec.kind, spec.default = STRING, raw_default
    return spec


def format_value(spec: Spec, value: Any) -> str:
    """A property value as FlatConfig text."""
    if spec.kind == BOOL:
        return "1" if value else "0"
    if spec.kind == INT:
        return str(int(value))
    if spec.kind == FLOAT:
        return f"{float(value):g}"
    if spec.kind == PERCENT:
        return f"{float(value):g}%"
    return str(value)


def parse_value(spec: Spec, text: str) -> Any:
    """FlatConfig text as a property value. Raises ``ValueError`` if it does not fit the type."""
    text = str(text).strip()
    if spec.kind == BOOL:
        if text.lower() in ("1", "true"):
            return True
        if text.lower() in ("0", "false"):
            return False
        raise ValueError(f"{spec.key}: not a boolean: {text!r}")
    if spec.kind in (INT, FLOAT, PERCENT):
        try:
            return _number(text, spec.kind == INT)
        except ValueError:
            raise ValueError(f"{spec.key}: not a number: {text!r}") from None
    if spec.kind == ENUM:
        if text not in [i[0] for i in spec.enum_items]:
            raise ValueError(f"{spec.key}: {text!r} is not one of {[i[0] for i in spec.enum_items]}")
        return text
    return text


def flatten(schema_type: str, value: Any) -> str:
    """A PresetDict value (``str`` or ``list[str]``) as FlatConfig text: vectors are joined the way
    Orca serialises them (``;`` with quotes for strings, ``,`` otherwise)."""
    if isinstance(value, list):
        if schema_type == "strings":
            return ";".join(f'"{e}"' for e in value)
        return ",".join(value)
    return str(value)


def split_vector(schema_type: str, text: str) -> list[str]:
    """The elements of a serialised vector (for per-element sub-UIs)."""
    text = text.strip()
    if not text:
        return []
    if schema_type == "strings":
        return [p.strip().strip('"') for p in text.split(";")]
    return [p.strip() for p in text.split(",")]
