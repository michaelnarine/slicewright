# SPDX-License-Identifier: GPL-3.0-or-later
"""Input keys for staleness and the slice cache (03 section 4.4). No ``bpy``."""
from __future__ import annotations

import hashlib
import json

import numpy as np


def _feed(h, arr) -> None:
    if arr is None:
        h.update(b"\x00none")
        return
    a = np.ascontiguousarray(arr)
    h.update(str(a.dtype).encode())
    h.update(str(a.shape).encode())
    h.update(a.data)


def object_key(vertices, triangles, face_extruder=None, face_support=None, face_seam=None,
               overrides: dict | None = None, extruder: int = 0) -> str:
    """blake2b over the mesh, per-face arrays, overrides JSON and extruder (~10 ms per 1M triangles)."""
    h = hashlib.blake2b(digest_size=16)
    for arr in (vertices, triangles, face_extruder, face_support, face_seam):
        _feed(h, arr)
    h.update(json.dumps(overrides or {}, sort_keys=True, separators=(",", ":")).encode())
    h.update(b"|extruder=%d" % int(extruder))
    return h.hexdigest()


def scene_key(engine_version: str, config: dict, object_keys) -> str:
    """Key over the engine version, the composed config and the sorted object keys."""
    h = hashlib.blake2b(digest_size=16)
    h.update(engine_version.encode())
    h.update(json.dumps(config, sort_keys=True, separators=(",", ":")).encode())
    for k in sorted(object_keys):
        h.update(k.encode())
    return h.hexdigest()
