# SPDX-License-Identifier: AGPL-3.0-only
"""Resolve real Bambu Lab presets from the profiles.zip in the built package (inherits applied, the way the add-on
does it, 03 section 3.5), so the tests can compose and slice with an actual multi-nozzle, multi-variant printer."""
from __future__ import annotations

import json
import zipfile
from functools import lru_cache

import slicewright_engine as sc

_META = {"inherits", "include", "from", "instantiation", "setting_id", "filament_id", "type", "version", "is_custom_defined"}


@lru_cache(maxsize=1)
def _index():
    z = zipfile.ZipFile(sc.profiles_archive())
    by_name = {}
    for n in z.namelist():
        if n.startswith("profiles/BBL/") and n.endswith(".json") and n.count("/") >= 3:
            kind = n.split("/")[2]
            if kind in ("machine", "process", "filament"):
                d = json.loads(z.read(n))
                if "name" in d:
                    by_name[(kind, d["name"])] = d
    return by_name


def resolve(kind: str, name: str) -> dict:
    idx = _index()

    def merged(k, n):
        d = idx[(k, n)]
        base = merged(k, d["inherits"]) if d.get("inherits") else {}
        out = dict(base)
        out.update({key: v for key, v in d.items() if key != "inherits"})
        return out

    d = merged(kind, name)
    return {k: v for k, v in d.items() if k not in _META or k == "name"}
