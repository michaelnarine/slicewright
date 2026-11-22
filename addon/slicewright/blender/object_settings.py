# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-object setting overrides: what the Object panel can offer, add and remove (03 sections 1.2 and 2.2).

The overrides live in ``Object.slicewright.overrides`` (a ``ConfigPG``: a key is overridden iff it is set). Only keys
whose schema scope is ``object`` or ``region`` may be offered (``core.object_overrides``); how they reach the engine and
the slice key is ``extract.object_overrides``. An override starts from the value the plate would use now, so adding one
changes nothing until it is edited.
"""
from __future__ import annotations

from ..core import config_codec as cc
from ..core import object_overrides as core
from . import config_pg, staleness


def overridden_keys(ob) -> list[str]:
    """Keys ``ob`` overrides, in schema order (those the add-on's property group knows)."""
    ov = ob.slicewright.overrides
    return [k for k in config_pg.specs() if ov.is_property_set(k)]


def orphan_keys(ob) -> list[str]:
    """Override keys the property group does not know (left behind by an older engine or a script)."""
    ov = ob.slicewright.overrides
    return sorted(k for k in ov.keys() if k not in config_pg.specs())


def offer(ob, level: str, show_develop: bool) -> list[str]:
    """The keys the picker offers for ``ob`` at settings detail ``level`` (the ones it does not override yet)."""
    schema = config_pg.schema()
    return [k for k in core.offerable(schema, level, show_develop, exclude=overridden_keys(ob)) if k in config_pg.specs()]


def start_value(scene_pg, key: str):
    """The value a new override of ``key`` starts from: the process setting the plate uses now, else the default."""
    spec = config_pg.specs()[key]
    if config_pg.schema().get(key, {}).get("preset") == "process" and hasattr(scene_pg.process_edits, key):
        try:
            return cc.parse_value(spec, cc.format_value(spec, getattr(scene_pg.process_edits, key)))
        except ValueError:
            pass
    return spec.default


def add_override(ob, scene_pg, key: str) -> bool:
    """Override ``key`` on ``ob``. False if the key is not offerable for an object."""
    if key not in config_pg.specs() or not core.is_per_object(config_pg.schema(), key):
        return False
    setattr(ob.slicewright.overrides, key, start_value(scene_pg, key))      # setting a property is what overrides it
    return True


def remove_override(ob, key: str | None = None) -> None:
    """Drop one override, or with ``key=None`` all of them (orphans too)."""
    ov = ob.slicewright.overrides
    if key is None:
        config_pg.clear(ov)
    elif key in config_pg.specs():
        config_pg.clear(ov, key)
    elif key in ov.keys():
        del ov[key]
        staleness.mark(objects=True)
