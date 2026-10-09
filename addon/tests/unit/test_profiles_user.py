# SPDX-License-Identifier: GPL-3.0-or-later
"""User presets (03 sections 2.5 and 3.8)."""
import json
import logging
import os

import fake_engine
import pytest
from profile_helpers import build, fixture_source

from slicewright.core.profiles import user as u
from slicewright.core.profiles.resolve import Resolver
from slicewright.core.profiles.source import ProfileError

SCHEMA = fake_engine.config_schema()


@pytest.fixture(scope="module")
def resolver():
    with fixture_source() as src:
        yield Resolver(build(src), src, SCHEMA)


@pytest.fixture
def store(tmp_path):
    return u.UserStore(str(tmp_path / "presets"))


# -- secrets -------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "print_host", "printhost_apikey", "printhost_cafile", "printhost_user", "printhost_port",
    "print_host_webui", "printhost_password", "bambu_access_code", "AccessCode", "my_passwd",
    "api_key", "apikey_x", "auth_token", "client_secret", "passphrase", "credentials"])
def test_secret_keys_are_recognised(key):
    assert u.is_secret_key(key)


@pytest.mark.parametrize("key", ["layer_height", "nozzle_temperature", "printer_model", "filament_type"])
def test_ordinary_keys_are_not_secrets(key):
    assert not u.is_secret_key(key)


def test_strip_secrets_copies():
    cfg = {"print_host": "h", "layer_height": "0.2", "printhost_apikey": ["k"]}
    assert u.strip_secrets(cfg) == {"layer_height": "0.2"} and "print_host" in cfg


# -- diff and revert -----------------------------------------------------------------------------

def test_diff_normalises_scalar_vs_one_element_list():
    parent = {"a": ["1"], "b": "2", "c": "x", "only_parent": "p", "d": ["1", "2"]}
    edited = {"a": "1", "b": ["2"], "c": "y", "new": "n", "name": "ignored", "d": ["1", "2"]}
    assert u.diff_config(parent, edited) == {"c": "y", "new": "n"}


def test_diff_detects_vector_changes_and_stringifies():
    assert u.diff_config({"v": ["1", "2"]}, {"v": ["1", "3"], "n": 4}) == {"v": ["1", "3"], "n": "4"}


def test_changed_rows_and_revert():
    parent = {"a": "1", "b": "2"}
    edited = {"a": "9", "b": "2", "z": "new"}
    assert u.changed_rows(parent, edited) == [("a", "1", "9"), ("z", None, "new")]
    u.revert_key(edited, parent, "a")
    assert edited["a"] == "1"
    u.revert_key(edited, parent, "z")
    assert "z" not in edited
    edited.update(a="5", b="6")
    u.revert_all(edited, parent)
    assert edited == parent


# -- file shape, store ---------------------------------------------------------------------------

def test_build_preset_json_has_orca_shape():
    data = u.build_preset_json("process", "Mine", "0.20mm Standard @Acme", {"wall_loops": "4"}, "1.2")
    assert list(data) == ["type", "from", "inherits", "name", "version", "wall_loops"]
    assert data["from"] == "User" and data["type"] == "process"
    assert "inherits" not in u.build_preset_json("process", "Mine", None, {}, "1")
    with pytest.raises(ValueError):
        u.build_preset_json("model", "x", None, {}, "1")


def test_save_load_round_trip_and_list(store):
    path = store.save("process", "Strong walls", "0.20mm Standard @Acme", {"wall_loops": "5"}, "2.0")
    assert os.path.isfile(path) and not os.path.exists(path + ".part")
    assert store.exists("process", "Strong walls") and not store.exists("machine", "Strong walls")
    assert store.load("process", "Strong walls")["wall_loops"] == "5"
    assert store.list("process") == ["Strong walls"] and store.list("machine") == []
    assert store.id_of("Strong walls") == "user:Strong walls"


def test_secrets_are_never_written(store, tmp_path):
    diff = {"print_host": "10.0.0.2", "printhost_apikey": "k", "printhost_cafile": "/x",
            "bambu_access_code": "1234", "wifi_password": ["p"], "layer_height": "0.1"}
    path = store.save("machine", "Printer", None, diff)
    text = open(path, encoding="utf-8").read()
    assert json.loads(text) == {"type": "machine", "from": "User", "name": "Printer",
                                "version": "1.0.0.0", "layer_height": "0.1"}
    for secret in ("10.0.0.2", "1234", "apikey"):
        assert secret not in text
    store.write("machine", "Raw", {"printhost_user": "me", "x": "1"})        # also on raw writes
    assert "printhost_user" not in store.load("machine", "Raw")


def test_unsafe_names_cannot_escape_the_store(store, tmp_path):
    for bad in ("", "  ", "../evil", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            store.save("process", bad, None, {})
    path = store.save("process", 'we<ird>:"|?* name.', None, {})
    assert os.path.dirname(path) == os.path.join(store.root, "process")
    assert store.list("process") == ['we<ird>:"|?* name.']
    assert store.load("process", 'we<ird>:"|?* name.')["name"] == 'we<ird>:"|?* name.'
    dot = store.save("process", ".hidden", None, {})
    assert not os.path.basename(dot).startswith(".")
    assert not (tmp_path / "evil.json").exists()
    with pytest.raises(ValueError):
        store.save("bogus", "x", None, {})


def test_non_ascii_names_round_trip(store):
    store.save("filament", "Café PLA", None, {"filament_cost": ["3"]})
    assert store.list("filament") == ["Café PLA"] and store.exists("filament", "Café PLA")


def test_corrupt_files_are_skipped_with_a_warning(store, caplog):
    store.save("process", "good", None, {})
    folder = os.path.join(store.root, "process")
    open(os.path.join(folder, "bad.json"), "w").write("{nope")
    open(os.path.join(folder, "list.json"), "w").write("[1]")
    open(os.path.join(folder, "notes.txt"), "w").write("x")
    with caplog.at_level(logging.WARNING, logger="slicewright.profiles"):
        assert store.list("process") == ["good"]
    assert len(caplog.records) == 2
    with pytest.raises(ProfileError):
        store.load("process", "absent")


def test_duplicate_rename_delete(store):
    store.save("process", "A", "p", {"x": "1"})
    store.duplicate("process", "A", "B")
    assert store.load("process", "B")["name"] == "B" and store.load("process", "B")["x"] == "1"
    with pytest.raises(ValueError):
        store.duplicate("process", "A", "B")
    store.rename("process", "B", "C")
    assert not store.exists("process", "B") and store.load("process", "C")["name"] == "C"
    with pytest.raises(ValueError):
        store.rename("process", "C", "A")
    assert store.delete("process", "C") and not store.delete("process", "C")
    assert store.list("process") == ["A"]


# -- resolving against the fixture library -------------------------------------------------------

def test_save_diff_and_resolve_round_trip(resolver, store):
    parent = resolver.resolve("process", "sys:Acme/0.20mm Standard @Acme")
    edited = dict(parent.config, wall_loops="6", sparse_infill_density="30%")
    diff = u.diff_config(parent.config, edited)
    assert diff == {"wall_loops": "6", "sparse_infill_density": "30%"}
    store.save("process", "Mine", "0.20mm Standard @Acme", diff)
    got = u.resolve_user_preset(resolver, "process", store, "Mine")
    assert got.config == edited and got.id == "user:Mine"
    assert got.chain[0] == "user:Mine" and got.chain[1] == "sys:Acme/0.20mm Standard @Acme"
    assert "inherits" not in got.config and "from" not in got.config


def test_resolve_without_parent_uses_defaults_and_missing_parent_raises(resolver, store):
    store.save("process", "Standalone", None, {"wall_loops": "7"})
    got = u.resolve_user_preset(resolver, "process", store, "Standalone")
    assert got.config["wall_loops"] == "7" and got.config["layer_height"] == "0.2"
    store.save("process", "Orphan", "no such parent", {})
    with pytest.raises(ProfileError, match="not found"):
        u.resolve_user_preset(resolver, "process", store, "Orphan")


def test_ambiguous_parent_names_are_deterministic_and_flagged(resolver, store):
    pid, others = u.parent_id(resolver.index, "filament", "fdm_filament_pla")
    assert pid == "sys:Bolt3D/fdm_filament_pla" and others == ["sys:OrcaFilamentLibrary/fdm_filament_pla"]
    store.save("filament", "Mine", "fdm_filament_pla", {})
    got = u.resolve_user_preset(resolver, "filament", store, "Mine")
    assert got.config["nozzle_temperature"] == ["195"] and "several vendors" in got.warnings[-1]
    assert u.parent_id(resolver.index, "filament", "zzz") == (None, [])


# -- embedding -----------------------------------------------------------------------------------

def _item(kind, name, **cfg):
    return {"kind": kind, "id": f"user:{name}", "name": name, "inherits": "p", "config": cfg}


def test_embed_round_trip_strips_secrets():
    printer = _item("machine", "P", print_host="h", printhost_apikey="k", nozzle_diameter=["0.4"])
    process = _item("process", "Q", wall_loops="3", wifi_token="t")
    filaments = [_item("filament", "F1", filament_type=["PLA"], x_password="hunter2"), None]
    text = u.dumps_embedded(printer, process, filaments)
    for secret in ('"h"', '"k"', '"t"', "hunter2", "apikey", "password"):
        assert secret not in text
    got = u.loads_embedded(text)
    assert got["format"] == u.EMBED_FORMAT
    assert got["printer"]["config"] == {"nozzle_diameter": ["0.4"]}
    assert [f["name"] for f in got["filaments"]] == ["F1"]


def test_loading_embedded_text_strips_secrets_it_finds(tmp_path):
    tampered = json.dumps({"format": 1, "printer": _item("machine", "P", print_host="h", a="1"),
                           "process": None, "filaments": [_item("filament", "F", printhost_user="u")]})
    got = u.loads_embedded(tampered)
    assert got["printer"]["config"] == {"a": "1"} and got["filaments"][0]["config"] == {}


@pytest.mark.parametrize("text", ["", "{broken", "[]", '{"format": 99}', "null"])
def test_loads_embedded_tolerates_garbage(text):
    assert u.loads_embedded(text) == {}


def test_embedded_fallback():
    emb = u.loads_embedded(u.dumps_embedded(
        _item("machine", "P", a="1"), None, [_item("filament", "F", b="2")]))
    assert u.embedded_fallback(emb, "machine", "user:P") == {"a": "1"}
    assert u.embedded_fallback(emb, "filament", "user:F") == {"b": "2"}
    assert u.embedded_fallback(emb, "process", "user:P") is None
    assert u.embedded_fallback({}, "machine", "user:P") is None
