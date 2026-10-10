# SPDX-License-Identifier: GPL-3.0-or-later
"""Preset resolution behaviour (03 section 3.5): defaults, inherits, include, own keys."""
import time

import fake_engine
import pytest
from profile_helpers import build, fixture_index, fixture_source, make_archive, preset

from slicewright.core.profiles.resolve import Resolver, normalise_value
from slicewright.core.profiles.source import ProfileError, ProfileSource

SCHEMA = fake_engine.config_schema()


@pytest.fixture(scope="module")
def fx():
    with fixture_source() as src:
        index = build(src)
        yield Resolver(index, src, SCHEMA)


def make(tmp_path, files, **kw):
    src = ProfileSource(make_archive(tmp_path, files))
    return Resolver(build(src), src, SCHEMA, **kw)


# 1. defaults ---------------------------------------------------------------------------------

def test_defaults_come_from_the_schema_for_the_matching_preset_type_only(tmp_path):
    r = make(tmp_path, {"V/process/p.json": preset("process", "p"),
                        "V/machine/m.json": preset("machine", "m"),
                        "V/filament/f.json": preset("filament", "f")})
    process, machine, filament = (r.resolve(k, f"sys:V/{n}").config
                                  for k, n in (("process", "p"), ("machine", "m"), ("filament", "f")))
    assert process["layer_height"] == "0.2" and "nozzle_diameter" not in process
    assert machine["printable_height"] == "250" and "layer_height" not in machine
    assert filament["filament_diameter"] == "1.75" and "wall_loops" not in filament
    assert "curr_bed_type" not in process | machine | filament      # preset "none" keys are nobody's


def test_a_key_the_preset_does_not_set_keeps_the_default(fx):
    config = fx.resolve("process", "sys:Acme/0.20mm Standard @Acme").config
    assert config["enable_support"] == "0" and config["skirt_loops"] == "1"


# 2. parent chain -----------------------------------------------------------------------------

def test_inheritance_applies_root_first_then_each_child(tmp_path):
    r = make(tmp_path, {
        "V/process/a.json": preset("process", "a", instantiation=False, wall_loops="1", layer_height="0.1"),
        "V/process/b.json": preset("process", "b", "a", instantiation=False, wall_loops="2"),
        "V/process/c.json": preset("process", "c", "b", wall_loops="3", top_shell_layers="9")})
    got = r.resolve("process", "sys:V/c")
    assert got.chain == ["sys:V/c", "sys:V/b", "sys:V/a"]
    assert got.config["wall_loops"] == "3"               # own beats parent beats grandparent
    assert got.config["layer_height"] == "0.1"           # from the grandparent, over the default 0.2
    assert got.config["top_shell_layers"] == "9"
    assert r.resolve("process", "sys:V/b").config["wall_loops"] == "2"


def test_fixture_filament_chain_and_values(fx):
    got = fx.resolve("filament", "sys:Acme/Generic PLA @Acme")
    assert got.chain == ["sys:Acme/Generic PLA @Acme", "sys:OrcaFilamentLibrary/Generic PLA",
                         "sys:OrcaFilamentLibrary/fdm_filament_pla",
                         "sys:OrcaFilamentLibrary/fdm_filament_common"]
    assert got.config["nozzle_temperature"] == ["205"]         # own
    assert got.config["filament_type"] == ["PLA"]              # from the root
    assert got.config["filament_cost"] == ["20"]


def test_parent_is_found_in_the_same_vendor_before_the_library(fx):
    assert fx.resolve("filament", "sys:Bolt3D/Bolt PLA").config["nozzle_temperature"] == ["195"]
    assert fx.resolve("filament", "sys:OrcaFilamentLibrary/Generic PLA").config[
        "nozzle_temperature"] == ["210"]
    # Acme has no fdm_filament_pla of its own, so the library's is used across vendors
    assert fx.resolve("filament", "sys:Acme/Acme PLA Pro").config["nozzle_temperature"] == ["210"]


def test_a_missing_parent_and_a_cycle_raise(tmp_path):
    r = make(tmp_path, {"V/machine/x.json": preset("machine", "x", "ghost"),
                        "V/machine/y.json": preset("machine", "y", "z"),
                        "V/machine/z.json": preset("machine", "z", "y")})
    with pytest.raises(ProfileError, match="not found"):
        r.resolve("machine", "sys:V/x")
    with pytest.raises(ProfileError, match="cycle"):
        r.resolve("machine", "sys:V/y")
    with pytest.raises(ProfileError, match="unknown"):
        r.resolve("machine", "sys:V/none")


def test_identity_keys_are_never_inherited(tmp_path):
    r = make(tmp_path, {
        "V/process/base.json": preset("process", "base", instantiation=False, setting_id="S1",
                                      version="1.0", description="d", **{"from": "system"}),
        "V/process/kid.json": preset("process", "kid", "base")})
    config = r.resolve("process", "sys:V/kid").config
    assert not ({"name", "inherits", "from", "instantiation", "type", "setting_id", "version",
                 "description", "include", "filament_id", "renamed_from"} & set(config))


# 3. includes ---------------------------------------------------------------------------------

def _gcode_files(**override):
    files = {
        "V/machine/base.json": preset("machine", "base", instantiation=False, gcode_flavor="marlin",
                                      thumbnails="a"),
        "V/machine/gcode_t1.json": preset("machine", "gcode_t1", instantiation=False,
                                          gcode_flavor="klipper", thumbnails="t1"),
        "V/machine/gcode_t2.json": preset("machine", "gcode_t2", instantiation=False, thumbnails="t2"),
        "V/machine/m.json": preset("machine", "m", "base", include=["gcode_t1", "gcode_t2"]),
    }
    files.update(override)
    return files


def test_includes_apply_after_the_parent_chain_and_in_order(tmp_path):
    got = make(tmp_path, _gcode_files()).resolve("machine", "sys:V/m")
    assert got.config["gcode_flavor"] == "klipper"       # include beats the parent's marlin
    assert got.config["thumbnails"] == "t2"              # later include beats earlier
    assert got.includes == ["gcode_t1", "gcode_t2"] and got.warnings == []


def test_own_keys_beat_includes(tmp_path):
    files = _gcode_files(**{"V/machine/m.json": preset(
        "machine", "m", "base", include=["gcode_t1"], gcode_flavor="reprapfirmware")})
    assert make(tmp_path, files).resolve("machine", "sys:V/m").config["gcode_flavor"] == "reprapfirmware"


def test_a_parents_includes_apply_before_the_childs_own_keys(tmp_path):
    files = {
        "V/machine/gcode_t.json": preset("machine", "gcode_t", instantiation=False, thumbnails="t"),
        "V/machine/p.json": preset("machine", "p", instantiation=False, include=["gcode_t"]),
        "V/machine/c.json": preset("machine", "c", "p", thumbnails="own")}
    r = make(tmp_path, files)
    assert r.resolve("machine", "sys:V/c").config["thumbnails"] == "own"
    assert r.resolve("machine", "sys:V/p").config["thumbnails"] == "t"


def test_only_gcode_templates_can_be_included(tmp_path):
    files = _gcode_files(**{
        "V/machine/other_base.json": preset("machine", "other_base", instantiation=False, thumbnails="x"),
        "V/machine/selectable_gcode.json": preset("machine", "selectable_gcode", thumbnails="y"),
        "V/machine/m.json": preset("machine", "m", "base", include=[
            "other_base", "selectable_gcode", "missing", "gcode_t1"])})
    got = make(tmp_path, files).resolve("machine", "sys:V/m")
    assert got.config["thumbnails"] == "t1" and got.includes == ["gcode_t1"]
    assert len(got.warnings) == 3
    assert "not a G-code template" in got.warnings[0] and "not found" in got.warnings[2]


def test_a_template_may_include_another_and_cycles_are_cut(tmp_path):
    files = _gcode_files(**{
        "V/machine/gcode_t1.json": preset("machine", "gcode_t1", instantiation=False,
                                          include=["gcode_t2"], thumbnails="t1"),
        "V/machine/gcode_t2.json": preset("machine", "gcode_t2", instantiation=False,
                                          include=["gcode_t1"], gcode_flavor="klipper", thumbnails="t2"),
        "V/machine/m.json": preset("machine", "m", "base", include=["gcode_t1"])})
    got = make(tmp_path, files).resolve("machine", "sys:V/m")
    assert got.config["gcode_flavor"] == "klipper"       # reached through t1 -> t2
    assert got.config["thumbnails"] == "t1"              # t1's own keys beat what it included
    assert any("circular" in w for w in got.warnings)


def test_fixture_include(fx):
    got = fx.resolve("machine", "sys:Bolt3D/Bolt One 0.4 nozzle")
    assert got.includes == ["gcode_bolt_macros"]
    assert got.config["thumbnails"] == "32x32,300x300"
    assert got.config["gcode_flavor"] == "marlin2"       # own key wins over the include's klipper
    assert got.config["printable_height"] == "180"


# 4. own keys, values and compat fields -------------------------------------------------------

def test_unknown_keys_and_value_shapes_pass_through(tmp_path):
    r = make(tmp_path, {"V/process/p.json": preset(
        "process", "p", not_in_schema="v", wall_loops=3, enable_support=True, junk=None,
        nested={"a": 1}, speeds=[1, 2.5])})
    config = r.resolve("process", "sys:V/p").config
    assert config["not_in_schema"] == "v"
    assert config["wall_loops"] == "3" and config["enable_support"] == "1"
    assert config["speeds"] == ["1", "2.5"]
    assert "junk" not in config and "nested" not in config


def test_normalise_value():
    assert normalise_value(["a", 1]) == ["a", "1"]
    assert normalise_value(2) == "2" and normalise_value(False) == "0"
    assert normalise_value(None) is None and normalise_value({}) is None


def test_compat_fields_are_inherited_and_an_empty_own_list_overrides(tmp_path):
    r = make(tmp_path, {
        "V/process/base.json": preset("process", "base", instantiation=False,
                                      compatible_printers=["P1"], compatible_printers_condition="c"),
        "V/process/kid.json": preset("process", "kid", "base"),
        "V/process/cleared.json": preset("process", "cleared", "base", compatible_printers=[])})
    assert r.resolve("process", "sys:V/kid").config["compatible_printers"] == ["P1"]
    assert r.resolve("process", "sys:V/cleared").config["compatible_printers"] == []
    assert r.resolve("process", "sys:V/cleared").config["compatible_printers_condition"] == "c"


def test_models_cannot_be_resolved(fx):
    with pytest.raises(ProfileError, match="no settings"):
        fx.resolve_entry(fx.index.get("model", "sys:Acme/Acme Maker 1"))


# 5. cache and speed --------------------------------------------------------------------------

def test_lru_returns_the_same_object_and_evicts_the_oldest(tmp_path):
    files = {f"V/process/p{i}.json": preset("process", f"p{i}") for i in range(4)}
    r = make(tmp_path, files, cache_size=2)
    a = r.resolve("process", "sys:V/p0")
    assert r.resolve("process", "sys:V/p0") is a and (r.hits, r.misses) == (1, 1)
    r.resolve("process", "sys:V/p1")
    r.resolve("process", "sys:V/p0")                      # p0 is now the newest
    r.resolve("process", "sys:V/p2")                      # evicts p1
    assert r.resolve("process", "sys:V/p0") is a
    before = r.misses
    r.resolve("process", "sys:V/p1")
    assert r.misses == before + 1


def test_resolving_is_fast_enough_for_a_lazy_ui(fx):
    t0 = time.perf_counter()
    for e in fx.index.of_kind("filament", selectable_only=True):
        fx.resolve_entry(e)
    assert (time.perf_counter() - t0) / 8 < 0.005          # the spec budgets 2-5 ms per preset


# 6. renamed_from -----------------------------------------------------------------------------

def test_renamed_presets_are_found_through_renamed_from(tmp_path):
    src = ProfileSource(make_archive(tmp_path, {
        "V/process/new.json": preset("process", "new", renamed_from=["old", "older"])}))
    index = build(src)
    assert index.get_or_renamed("process", "sys:V/older").name == "new"
    assert index.get_or_renamed("process", "sys:V/new").name == "new"
    assert index.get_or_renamed("process", "sys:V/none") is None
    assert index.get_or_renamed("process", "user:mine") is None
