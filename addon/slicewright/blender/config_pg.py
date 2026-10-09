# SPDX-License-Identifier: GPL-3.0-or-later
"""The generated typed config PropertyGroup (03 section 2.3).

At register the add-on reads ``sc.config_schema()`` and builds ``SLICEWRIGHT_PG_Config`` with one
property per schema key. One class serves three roles:

* the preset **edit buffer** (``printer_edits``, ``process_edits``): every key is set;
* **per-object overrides** (``Object.slicewright.overrides``): a key is overridden iff
  ``is_property_set(key)``; Reset calls ``property_unset``;
* **per-filament-slot overrides** (``FilamentSlot.overrides``): the same rule.

Keys that vanish in a newer engine survive as orphan ID properties (and travel to the engine, which
reports them as ``unknown_key`` from ``normalize_config``). If the engine cannot be imported nothing
here registers. No schema snapshot ships: labels and tooltips are engine data read at runtime.
"""
import logging
from typing import Any

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from ..core import config_codec as cc
from . import registry

REQUIRES_ENGINE = True
CLASS_NAME = "SLICEWRIGHT_PG_Config"
INT_LIMIT = 2 ** 31 - 1
log = logging.getLogger("slicewright.config_pg")


class _Built:
    cls: type | None = None
    specs: dict[str, cc.Spec] = {}
    skipped: list[str] = []


_b = _Built()


def _clamp_int(value: float | None) -> int | None:
    return None if value is None else int(max(-INT_LIMIT, min(INT_LIMIT, value)))


def _property(spec: cc.Spec):
    base = {"name": spec.label, "description": spec.description}
    if spec.kind == cc.BOOL:
        return BoolProperty(default=bool(spec.default), **base)
    if spec.kind == cc.INT:
        extra = {k: v for k, v in (("soft_min", _clamp_int(spec.soft_min)),
                                   ("soft_max", _clamp_int(spec.soft_max))) if v is not None}
        return IntProperty(default=_clamp_int(spec.default), **extra, **base)
    if spec.kind in (cc.FLOAT, cc.PERCENT):
        extra = {k: v for k, v in (("soft_min", spec.soft_min), ("soft_max", spec.soft_max))
                 if v is not None}
        if spec.kind == cc.PERCENT:
            extra["subtype"] = "PERCENTAGE"
        return FloatProperty(default=spec.default, precision=3, **extra, **base)
    if spec.kind == cc.ENUM:
        return EnumProperty(items=spec.enum_items, default=spec.default, **base)
    return StringProperty(default=str(spec.default), **base)


def build_class(schema: dict[str, dict]) -> tuple[type, dict[str, cc.Spec], list[str]]:
    """Make (not register) the PropertyGroup class for ``schema``. Keys Blender cannot hold are skipped."""
    specs: dict[str, cc.Spec] = {}
    annotations: dict[str, Any] = {}
    skipped: list[str] = []
    for key, entry in schema.items():
        if (not key.isidentifier() or key.startswith("_") or hasattr(bpy.types.PropertyGroup, key)
                or key in bpy.types.PropertyGroup.bl_rna.properties):
            skipped.append(key)
            continue
        specs[key] = cc.spec_for(key, entry)
        annotations[key] = _property(specs[key])
    cls = type(CLASS_NAME, (bpy.types.PropertyGroup,), {"__annotations__": annotations})
    return cls, specs, skipped


def config_class() -> type:
    """The registered class, for ``PointerProperty(type=...)``."""
    assert _b.cls is not None, "config_pg is not registered"
    return _b.cls


def specs() -> dict[str, cc.Spec]:
    return _b.specs


# -- reading and writing a ConfigPG ---------------------------------------------------------------

def to_flat(pg, *, only_set: bool = False) -> dict[str, str]:
    """The group as FlatConfig text. ``only_set`` returns just the overridden keys (roles 2 and 3);
    orphan ID properties are included so the engine can report them."""
    out = {}
    for key, spec in _b.specs.items():
        if only_set and not pg.is_property_set(key):
            continue
        out[key] = cc.format_value(spec, getattr(pg, key))
    for key in pg.keys():
        if key not in _b.specs and isinstance(pg[key], str):
            out[key] = pg[key]
    return out


def overridden(pg) -> dict[str, str]:
    return to_flat(pg, only_set=True)


def load_flat(pg, flat: dict[str, Any], *, reset: bool = True) -> list[str]:
    """Set keys from a resolved preset (values ``str`` or ``list[str]``). With ``reset`` every other schema
    key takes its default, so the buffer always has every key set. Returns problem messages for values
    that did not fit their type (those keys keep their previous value)."""
    problems = []
    for key, spec in _b.specs.items():
        if key in flat:
            try:
                setattr(pg, key, cc.parse_value(spec, cc.flatten(spec.schema_type, flat[key])))
            except ValueError as exc:
                problems.append(str(exc))
        elif reset:
            setattr(pg, key, spec.default)
    for key, value in flat.items():
        if key not in _b.specs and isinstance(value, str):
            pg[key] = value                         # orphan: kept as an ID property
    return problems


def clear(pg, key: str | None = None) -> None:
    """Drop overrides (``property_unset``): one key, or all of them."""
    for k in ([key] if key else list(_b.specs)):
        pg.property_unset(k)
    if key is None:
        for k in [k for k in pg.keys() if k not in _b.specs]:
            del pg[k]


def register() -> None:
    sc = registry.state.status.module
    cls, specs_, skipped = build_class(sc.config_schema())
    if skipped:
        log.warning("config keys Blender cannot hold as properties: %s", ", ".join(skipped))
    registry.register_classes((cls,))
    _b.cls, _b.specs, _b.skipped = cls, specs_, skipped


def unregister() -> None:
    if _b.cls is not None:
        registry.unregister_classes((_b.cls,))
    _b.cls, _b.specs, _b.skipped = None, {}, []
