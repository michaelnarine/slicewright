# SPDX-License-Identifier: GPL-3.0-or-later
"""Template batches the instanced preview draws repeat (03 section 7.3).

Each is a tiny vertex buffer holding one ``corner`` attribute that the vertex shader turns into
a position. The attribute is **U32** with fetch mode INT: a 1-byte U8 attribute aborts Blender
on Metal (vertex descriptor stride 1).
"""
from __future__ import annotations

import numpy as np

STRIP_CORNERS = 4       # TRI_STRIP ribbon: bit 1 = along (A or B end), bit 0 = side
LINE_CORNERS = 2        # LINES: 0 = A, 1 = B


def corner_values(count: int) -> np.ndarray:
    return np.arange(count, dtype=np.uint32)


def make_template(kind: str):
    """``(batch, vbo)`` for ``kind`` in ``strip`` (tubes, markers) or ``lines``. Keep ``vbo``
    alive as long as the batch."""
    import gpu
    count, prim = {"strip": (STRIP_CORNERS, 'TRI_STRIP'), "lines": (LINE_CORNERS, 'LINES')}[kind]
    fmt = gpu.types.GPUVertFormat()
    fmt.attr_add(id="corner", comp_type='U32', len=1, fetch_mode='INT')
    vbo = gpu.types.GPUVertBuf(fmt, count)
    vbo.attr_fill(id="corner", data=corner_values(count))
    return gpu.types.GPUBatch(type=prim, buf=vbo), vbo
