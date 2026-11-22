# SPDX-License-Identifier: GPL-3.0-or-later
"""The local tooltip corrections (``core/descriptions.py``) against the REAL engine's schema: the flaws they correct are
still there (otherwise the table is stale), and the corrected text reaches the property spec."""
import numpy as np
import pytest
from contract_helpers import box
from real_helpers import drive, request
from slicewright.core import config_codec as cc
from slicewright.core import descriptions as d

pytestmark = pytest.mark.real_engine


@pytest.mark.parametrize("key", sorted(d.CORRECTIONS))
def test_the_engine_text_still_has_the_flaw_so_the_correction_applies(real_sc, key):
    entry = real_sc.config_schema()[key]
    assert d.CORRECTIONS[key].marker in entry["tooltip"], (
        f"the engine's tooltip for {key} changed: {entry['tooltip']!r}; review core/descriptions.py")
    spec = cc.spec_for(key, entry)
    assert spec.description != entry["tooltip"] and d.CORRECTIONS[key].text in spec.description


def test_the_four_z_contouring_keys_are_expert_and_per_object(real_sc):
    """Stage 0 asked whether they belong at Advanced; they stay Expert (the engine's classification), and can be
    overridden per object."""
    schema = real_sc.config_schema()
    keys = ("zaa_enabled", "zaa_min_z", "zaa_minimize_perimeter_height", "zaa_dont_alternate_fill_direction")
    assert {schema[k]["mode"] for k in keys} == {"expert"}
    assert {schema[k]["scope"] for k in keys} == {"region"}


def test_zaa_minimize_perimeter_height_acts_where_the_slope_is_greater_than_the_value(real_sc, runner):
    """The behaviour the corrected tooltip describes: on a ramp of about 19 degrees a value of 10 changes the
    toolpath, and 30 or 90 (slopes below the value) leave it as with 0, which is off."""
    from slicewright.core.plate_data import ExtractedObject

    def toolpath_sum(value):
        v, t = box(128.0, 128.0, 40.0, height=10.0)
        v = v.copy()
        v[5, 2] = v[6, 2] = 24.0                                 # 14 mm over 40 mm
        obj = ExtractedObject("ramp", "ramp", v, t)
        obj.config_overrides = {"zaa_enabled": "1", "zaa_minimize_perimeter_height": value}
        runner.start(request(objects=[obj]))
        return float(np.asarray(drive(runner)[-1].result.moves["position"][:, 2]).sum())

    off, steep, flat_a, flat_b = (toolpath_sum(v) for v in ("0", "10", "30", "90"))
    assert steep != off
    assert flat_a == off and flat_b == off
