# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit buffers as presets: role narrowing, canonical diffs and the file form of values."""
import fake_engine
import pytest
from profile_helpers import build, fixture_source

from slicewright.core import config_codec as cc
from slicewright.core.profiles import edits
from slicewright.core.profiles.resolve import Resolver
from slicewright.core.profiles.user import UserStore, resolve_user_preset

SCHEMA = fake_engine.config_schema()


def buffer_of(config):
    """What load_flat + to_flat would give: every schema key as canonical text, preset values on top."""
    flat = {k: cc.format_value(cc.spec_for(k, e), cc.parse_value(cc.spec_for(k, e), e["default"]))
            if cc.spec_for(k, e).kind != cc.STRING else e["default"] for k, e in SCHEMA.items()}
    flat.update(edits.parent_flat(SCHEMA, config))
    return flat


@pytest.fixture(scope="module")
def env():
    with fixture_source() as src:
        index = build(src)
        yield index, Resolver(index, src, SCHEMA)


def test_role_flat_keeps_only_the_roles_keys_and_orphans():
    flat = {"layer_height": "0.2", "nozzle_diameter": "0.4", "nozzle_temperature": "200", "orphan": "x"}
    assert edits.role_flat(SCHEMA, "process", flat) == {"layer_height": "0.2", "orphan": "x"}
    assert edits.role_flat(SCHEMA, "machine", flat) == {"nozzle_diameter": "0.4", "orphan": "x"}
    assert edits.role_flat(SCHEMA, "filament", flat) == {"nozzle_temperature": "200", "orphan": "x"}


def test_an_untouched_buffer_has_no_diff(env):
    index, resolver = env
    for kind, pid in (("process", "sys:Acme/0.12mm Fine @Acme"), ("machine", "sys:Acme/Acme Maker 2 0.4 nozzle"),
                      ("filament", "sys:Acme/Acme PLA Pro")):
        config = resolver.resolve(kind, pid).config
        assert edits.diff_flat(SCHEMA, kind, config, buffer_of(config)) == {}, pid


def test_equivalent_spellings_are_not_differences():
    parent = {"layer_height": "0.20", "sparse_infill_density": "15", "enable_support": "true",
              "wall_loops": "2.0"}
    edited = {"layer_height": "0.2", "sparse_infill_density": "15%", "enable_support": "1", "wall_loops": "2"}
    assert edits.diff_flat(SCHEMA, "process", parent, edited) == {}


def test_changed_keys_are_the_diff_and_other_roles_are_ignored():
    parent = {"layer_height": "0.2", "wall_loops": "2"}
    edited = {"layer_height": "0.25", "wall_loops": "2", "nozzle_temperature": "999", "extra_key": "v"}
    assert edits.diff_flat(SCHEMA, "process", parent, edited) == {"layer_height": "0.25", "extra_key": "v"}


def test_vectors_leave_as_lists_scalars_as_text():
    parent = {"nozzle_diameter": ["0.4"], "printable_area": ["0x0", "220x0", "220x220", "0x220"]}
    edited = {"nozzle_diameter": "0.4,0.6", "printable_area": "0x0,100x0,100x100,0x100", "gcode_flavor": "klipper"}
    got = edits.diff_for_file(SCHEMA, "machine", parent, edited)
    assert got == {"nozzle_diameter": ["0.4", "0.6"], "printable_area": ["0x0", "100x0", "100x100", "0x100"],
                   "gcode_flavor": "klipper"}
    assert edits.file_value(SCHEMA, "filament_type", '"PLA";"PETG"') == ["PLA", "PETG"]
    assert edits.file_value(SCHEMA, "unknown", "a,b") == "a,b"


def test_rows_carry_key_label_and_both_texts_sorted_by_label():
    parent = {"layer_height": "0.2", "wall_loops": "2"}
    got = edits.rows(SCHEMA, "process", parent, {"layer_height": "0.3", "wall_loops": "5"})
    assert got == [("layer_height", "Layer height", "0.2", "0.3"), ("wall_loops", "Wall loops", "2", "5")]


def test_a_saved_diff_resolves_back_to_the_edited_values(env, tmp_path):
    index, resolver = env
    parent = resolver.resolve("process", "sys:Acme/0.20mm Standard @Acme")
    edited = buffer_of(parent.config)
    edited.update({"layer_height": "0.16", "sparse_infill_density": "25%", "enable_support": "1"})
    diff = edits.diff_for_file(SCHEMA, "process", parent.config, edited)
    assert set(diff) == {"layer_height", "sparse_infill_density", "enable_support"}
    store = UserStore(str(tmp_path))
    store.save("process", "My fine", "0.20mm Standard @Acme", diff)
    got = resolve_user_preset(resolver, "process", store, "My fine").config
    assert edits.diff_flat(SCHEMA, "process", got, edited) == {}
    assert got["wall_loops"] == parent.config["wall_loops"]            # untouched keys come from the parent
    assert store.load("process", "My fine")["inherits"] == "0.20mm Standard @Acme"
