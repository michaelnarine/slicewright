# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-object setting overrides through the add-on's job runner against the REAL engine (03 section 4.1): an override
on one object changes that object's slice and not its neighbour's, and moves the slice key."""
from __future__ import annotations

import numpy as np
import pytest
from contract_helpers import box
from real_helpers import cube, drive, request
from slicewright.core.plate_data import ExtractedObject
from slicewright.engine import jobs

pytestmark = pytest.mark.real_engine


def _two(a_overrides=None, b_overrides=None, make=cube):
    a, b = make("a", 60.0), make("b", 190.0)
    a.config_overrides, b.config_overrides = dict(a_overrides or {}), dict(b_overrides or {})
    return [a, b]


def _slice(runner, objects):
    status = runner.start(request(objects=objects))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE, final
    return status.key, final.result


def _ramp(name, cx):
    """A 40 mm wide block whose top rises 4 mm along x: a surface Z contouring has something to do on."""
    v, t = box(cx, 128.0, 40.0, height=10.0)
    v = v.copy()
    v[5, 2] = v[6, 2] = 14.0
    return ExtractedObject(name, name, v, t)


def _moves_of(result, index):
    return int((result.moves["object_id"] == index).sum())


def test_a_wall_count_override_changes_the_filament_used_and_the_key(runner):
    key0, plain = _slice(runner, _two())
    key1, thick = _slice(runner, _two({"wall_loops": "6"}))
    mm = lambda r: r.stats["filament_per_extruder"][0]["mm"]
    assert mm(thick) > mm(plain) * 1.1 and key1 != key0
    key2, both = _slice(runner, _two({"wall_loops": "6"}, {"wall_loops": "6"}))
    assert mm(both) > mm(thick) * 1.05 and key2 not in (key0, key1)


def test_a_layer_height_override_adds_layers_for_that_object(runner):
    _, plain = _slice(runner, _two())
    _, fine = _slice(runner, _two({"layer_height": "0.1"}))
    assert len(plain.layers["z"]) == 100 and len(fine.layers["z"]) > 150


def test_zaa_on_one_object_changes_only_that_objects_toolpath(runner):
    _, plain = _slice(runner, _two(make=_ramp))
    _, zaa = _slice(runner, _two({"zaa_enabled": "1"}, make=_ramp))
    assert _moves_of(zaa, 0) > _moves_of(plain, 0) * 1.1                 # contoured: many more, shorter moves
    assert _moves_of(zaa, 1) == _moves_of(plain, 1)                      # the neighbour is untouched
    z = zaa.moves["position"][:, 2][zaa.moves["object_id"] == 0]
    z0 = plain.moves["position"][:, 2][plain.moves["object_id"] == 0]
    assert len(np.unique(np.round(z, 3))) > 2 * len(np.unique(np.round(z0, 3)))


def test_an_override_equal_to_the_default_still_moves_the_key(runner):
    key0, _ = _slice(runner, _two())
    key1, _ = _slice(runner, _two({"wall_loops": "2"}))                  # what the plate already uses
    assert key1 != key0                                                   # a different input, so a different slice key
