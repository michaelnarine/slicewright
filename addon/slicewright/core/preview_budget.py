# SPDX-License-Identifier: GPL-3.0-or-later
"""VRAM budget for the preview textures (03 section 7.8), pure Python.

Defaults are conservative on integrated GPUs: 384 MB when the device is Intel, or when it is
Apple Silicon with 8 GB of unified memory or less (or the memory cannot be read); 1 GB otherwise.
The user can override it in the preferences (``preview_vram_mb``, 0 = automatic).

Over budget, the plan degrades in two steps and reports what it did (the UI shows the messages):

1. drop the ``t_val`` textures (4 of 28 bytes per move): only the feature-type view stays;
2. upload fewer chunks: the preview is truncated at the last chunk that fits, so the print can
   still be scrubbed up to that move.

(03 7.8 also names dropping travel from the textures and forcing the lines LOD; neither lowers
the footprint of this chunk layout without a move re-index, so they are not budget steps here.)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Sequence

from . import preview_data as pd

MB = 1024 * 1024
SMALL_BUDGET_MB = 384
LARGE_BUDGET_MB = 1024
SMALL_UNIFIED_GB = 8.0


def default_budget_mb(device_type: str, system_ram_gb: float | None, apple: bool = False) -> int:
    """03 7.8: 384 for Intel, or Apple Silicon with <= 8 GB (unknown counts as small)."""
    dev = (device_type or "").upper()
    if dev == "INTEL":
        return SMALL_BUDGET_MB
    if apple or dev == "APPLE":
        return SMALL_BUDGET_MB if system_ram_gb is None or system_ram_gb <= SMALL_UNIFIED_GB else LARGE_BUDGET_MB
    return LARGE_BUDGET_MB


def system_ram_gb() -> float | None:
    """Physical memory in GB, or None where the OS does not say (e.g. Windows)."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError, AttributeError):
        return None


def chunk_bytes(b: pd.ChunkBounds, with_values: bool = True) -> int:
    """Texture bytes of one chunk including row padding (16 B pos + 8 B meta + 4 B values)."""
    per_texel = pd.BYTES_PER_MOVE if with_values else pd.BYTES_PER_MOVE - 4
    return b.rows * pd.TEX_W * per_texel


@dataclass
class BudgetPlan:
    with_values: bool = True
    n_chunks: int = 0                 # chunks to upload (all of them unless truncated)
    truncated: bool = False
    bytes_planned: int = 0
    bytes_full: int = 0
    budget: int = 0
    messages: list = field(default_factory=list)
    bounds: list = field(default_factory=list, repr=False)

    @property
    def last_move(self) -> int | None:
        """Last move that will be drawable, or None when nothing is uploaded."""
        return self.bounds[self.n_chunks - 1].e if self.n_chunks else None


def plan_budget(bounds: Sequence[pd.ChunkBounds], budget_bytes: int) -> BudgetPlan:
    full = sum(chunk_bytes(b) for b in bounds)
    plan = BudgetPlan(n_chunks=len(bounds), bytes_planned=full, bytes_full=full, budget=budget_bytes,
                      bounds=list(bounds))
    if full <= budget_bytes:
        return plan
    plan.with_values = False
    plan.bytes_planned = sum(chunk_bytes(b, False) for b in bounds)
    plan.messages.append(
        f"Over the {budget_bytes // MB} MB preview budget ({full // MB} MB needed): "
        "only the Feature type view is available")
    if plan.bytes_planned <= budget_bytes:
        return plan
    used, n = 0, 0
    for b in bounds:
        if used + chunk_bytes(b, False) > budget_bytes:
            break
        used += chunk_bytes(b, False)
        n += 1
    plan.n_chunks, plan.bytes_planned, plan.truncated = n, used, True
    shown = plan.bounds[n - 1].e + 1 if n else 0
    plan.messages.append(f"Preview shows the first {shown:,} of {plan.bounds[-1].e + 1:,} moves; "
                         "raise the VRAM budget in the add-on preferences to see more")
    return plan
