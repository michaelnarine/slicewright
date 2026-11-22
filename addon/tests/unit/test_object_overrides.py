# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-object override rules (``core/object_overrides.py``) against the fake engine's schema, without Blender."""
import fake_engine as sc
import pytest
from slicewright.core import object_overrides as oo

SCHEMA = sc.config_schema()


def test_only_object_and_region_scope_keys_are_per_object():
    assert oo.is_per_object(SCHEMA, "wall_loops")                      # object scope
    assert oo.is_per_object(SCHEMA, "sparse_infill_density")           # region scope
    assert not oo.is_per_object(SCHEMA, "printable_height")            # global
    assert not oo.is_per_object(SCHEMA, "no_such_key")
    assert {SCHEMA[k]["scope"] for k in SCHEMA if oo.is_per_object(SCHEMA, k)} <= {"object", "region"}


def test_sanitize_keeps_valid_keys_and_reports_the_rest_as_warnings():
    kept, issues = oo.sanitize(SCHEMA, {"wall_loops": "4", "printable_height": "50", "from_the_future": "1"}, "Bolt#2")
    assert kept == {"wall_loops": "4"}
    by_code = {i["code"]: i for i in issues}
    assert set(by_code) == {oo.NOT_PER_OBJECT, oo.UNKNOWN_KEY}
    for issue in issues:
        assert issue["level"] == "warning" and issue["object_name"] == "Bolt#2"
        assert set(issue) == {"level", "code", "message", "opt_key", "object_name"}       # the 04 section 2.6 shape
        assert "Bolt#2" in issue["message"]
    assert by_code[oo.NOT_PER_OBJECT]["opt_key"] == "printable_height"
    assert "whole plate" in by_code[oo.NOT_PER_OBJECT]["message"]
    assert by_code[oo.UNKNOWN_KEY]["opt_key"] == "from_the_future"


def test_sanitize_of_nothing_is_nothing():
    assert oo.sanitize(SCHEMA, {}, "A") == ({}, [])


def _schema(**modes):
    return {k: {"scope": "object", "mode": m, "label": k.title()} for k, m in modes.items()} | {
        "g": {"scope": "global", "mode": "simple", "label": "G"}}


def test_offerable_follows_the_detail_mode_and_the_develop_switch():
    schema = _schema(a="simple", b="advanced", c="expert", d="develop")
    assert oo.offerable(schema, "simple") == ["a"]
    assert oo.offerable(schema, "advanced") == ["a", "b"]
    assert oo.offerable(schema, "expert") == ["a", "b", "c"]
    assert oo.offerable(schema, "expert", show_develop=True) == ["a", "b", "c", "d"]


def test_offerable_skips_global_keys_and_the_excluded_and_sorts_by_label():
    schema = {"z_key": {"scope": "region", "mode": "simple", "label": "Alpha"},
              "a_key": {"scope": "object", "mode": "simple", "label": "beta"},
              "g_key": {"scope": "global", "mode": "simple", "label": "Gamma"}}
    assert oo.offerable(schema, "expert") == ["z_key", "a_key"]
    assert oo.offerable(schema, "expert", exclude=["z_key"]) == ["a_key"]


def test_pick_label_names_the_key():
    assert oo.pick_label(SCHEMA, "wall_loops") == f"{SCHEMA['wall_loops']['label']}  (wall_loops)"
    assert oo.pick_label({}, "odd") == "odd  (odd)"


@pytest.mark.parametrize("level", ["simple", "advanced", "expert"])
def test_every_offered_key_is_accepted_by_the_engine_as_an_override(level):
    import numpy as np
    from contract_helpers import box
    keys = oo.offerable(SCHEMA, level, show_develop=True)
    assert keys
    v, t = box()
    for key in keys:
        job = sc.SliceJob()
        job.set_config(sc.normalize_config(sc.compose_config(
            {"name": "P", "printable_area": ["0x0", "256x0", "256x256", "0x256"], "printable_height": "250",
             "nozzle_diameter": ["0.4"]}, {"name": "Q"}, [{"name": "F"}], None))["config"])
        job.add_object("cube", v, t, config_overrides={key: SCHEMA[key]["default"]})
    assert np is not None
