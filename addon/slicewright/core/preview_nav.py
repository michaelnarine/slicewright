# SPDX-License-Identifier: GPL-3.0-or-later
"""Layer and in-layer move scrubbing arithmetic (03 section 7.4), pure Python.

A :class:`Scrub` is what the sliders hold: layers ``lo..hi`` are visible, and ``pos`` is the
move offset inside the top layer ``hi`` (None: the whole layer). Everything clamps against the
result's ``layers`` table, so a stale value after a re-slice can never index out of range.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

import numpy as np


@dataclass(frozen=True)
class Scrub:
    lo: int = 0
    hi: int = 0
    pos: int | None = None


def layer_count(layers: Mapping[str, np.ndarray]) -> int:
    return len(layers["first"])


def layer_len(layers: Mapping[str, np.ndarray], i: int) -> int:
    """Number of moves in layer ``i``."""
    return int(layers["last"][i]) - int(layers["first"][i]) + 1


def clamp(s: Scrub, layers: Mapping[str, np.ndarray]) -> Scrub:
    n = layer_count(layers)
    if n == 0:
        return Scrub(0, 0, None)
    hi = min(max(s.hi, 0), n - 1)
    lo = min(max(s.lo, 0), hi)
    pos = None if s.pos is None else min(max(s.pos, 0), layer_len(layers, hi) - 1)
    return Scrub(lo, hi, pos)


def full(layers: Mapping[str, np.ndarray]) -> Scrub:
    return Scrub(0, max(layer_count(layers) - 1, 0), None)


def step_top_layer(s: Scrub, layers: Mapping[str, np.ndarray], delta: int) -> Scrub:
    """Move the top layer; the bottom layer never passes it and in-layer scrubbing resets."""
    return clamp(replace(s, hi=s.hi + delta, pos=None), layers)


def step_bottom_layer(s: Scrub, layers: Mapping[str, np.ndarray], delta: int) -> Scrub:
    return clamp(replace(s, lo=s.lo + delta), layers)


def step_move(s: Scrub, layers: Mapping[str, np.ndarray], delta: int) -> Scrub:
    """Step the in-layer position. From the whole layer, the first step starts at its last
    move; stepping onto (or past) the last move returns to the whole layer."""
    if layer_count(layers) == 0:
        return s
    s = clamp(s, layers)
    last = layer_len(layers, s.hi) - 1
    pos = (last if s.pos is None else s.pos) + delta
    return replace(s, pos=None if pos >= last else max(pos, 0))


def top_position(s: Scrub, layers: Mapping[str, np.ndarray]) -> int:
    """Global index of the last visible move (the nozzle position)."""
    s = clamp(s, layers)
    first, last = int(layers["first"][s.hi]), int(layers["last"][s.hi])
    return last if s.pos is None else min(first + s.pos, last)


def role_mask(bits) -> int:
    """32-bit mask from 32 booleans (bit r set: role id r is drawn)."""
    return sum(1 << i for i, v in enumerate(list(bits)[:32]) if v)
