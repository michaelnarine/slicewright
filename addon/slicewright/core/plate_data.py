# SPDX-License-Identifier: GPL-3.0-or-later
"""One plate object as plain arrays, ready for ``SliceJob.add_object`` (03 section 4.1). No ``bpy``.

``blender/extract.py`` builds these from the evaluated depsgraph; the job runner
(``engine/jobs.py``) consumes them without knowing where they came from.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import hashing


@dataclass
class ExtractedObject:
    name: str                              # the engine-side name ("Bolt#3" for instances)
    owner: str                             # name_full of the plate object it came from
    vertices: np.ndarray                   # (n, 3) float32, world mm
    triangles: np.ndarray                  # (m, 3) int32
    extruder: int = 0
    config_overrides: dict = field(default_factory=dict)
    face_extruder: np.ndarray | None = None    # (m,) uint8 or None
    face_support: np.ndarray | None = None
    face_seam: np.ndarray | None = None
    key: str = ""

    def add_object_kwargs(self) -> dict:
        """Keyword arguments for ``SliceJob.add_object`` after the name, vertices and triangles (04 section 4.1)."""
        return dict(extruder=self.extruder, config_overrides=self.config_overrides or None,
                    face_extruder=self.face_extruder, face_support=self.face_support,
                    face_seam=self.face_seam, ensure_on_bed=False)

    def compute_key(self) -> str:
        """Hash of the mesh, per-face arrays, overrides and extruder (03 section 4.4); stored in ``key``."""
        self.key = hashing.object_key(self.vertices, self.triangles, self.face_extruder, self.face_support,
                                      self.face_seam, self.config_overrides, self.extruder)
        return self.key
