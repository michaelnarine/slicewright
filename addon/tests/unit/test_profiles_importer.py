# SPDX-License-Identifier: GPL-3.0-or-later
"""Importing user presets from OrcaSlicer / BambuStudio data dirs (03 section 3.9), on synthetic dirs."""
import json
import os

import fake_engine
import pytest
from profile_helpers import build, fixture_source

from slicewright.core.profiles import importer as im
from slicewright.core.profiles.resolve import Resolver
from slicewright.core.profiles.user import UserStore

SCHEMA = fake_engine.config_schema()


@pytest.fixture(scope="module")
def env():
    with fixture_source() as src:
        index = build(src)
        yield index, Resolver(index, src, SCHEMA)


def put(base, uid, kind, fn, data):
    folder = base / "user" / uid / kind
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / fn
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def snapshot(base):
    return sorted((str(p), p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else b"")
                  for p in [base, *base.rglob("*")])


# -- locations -----------------------------------------------------------------------------------

def test_candidate_dirs_per_platform():
    assert im.candidate_dirs("darwin", "/Users/me", {}) == [
        ("OrcaSlicer", "/Users/me/Library/Application Support/OrcaSlicer"),
        ("BambuStudio", "/Users/me/Library/Application Support/BambuStudio")]
    assert im.candidate_dirs("win32", "C:\\Users\\me", {"APPDATA": "C:\\Users\\me\\AppData\\Roaming"}) == [
        ("OrcaSlicer", "C:\\Users\\me\\AppData\\Roaming\\OrcaSlicer"),
        ("BambuStudio", "C:\\Users\\me\\AppData\\Roaming\\BambuStudio")]
    assert im.candidate_dirs("win32", "C:\\Users\\me", {})[0][1] == "C:\\Users\\me\\AppData\\Roaming\\OrcaSlicer"
    assert im.candidate_dirs("linux", "/home/me", {}) == [
        ("OrcaSlicer", "/home/me/.config/OrcaSlicer"), ("BambuStudio", "/home/me/.config/BambuStudio"),
        ("OrcaSlicer", "/home/me/.var/app/io.github.softfever.OrcaSlicer/config/OrcaSlicer"),
        ("BambuStudio", "/home/me/.var/app/com.bambulab.BambuStudio/config/BambuStudio")]
    assert im.candidate_dirs("linux", "/home/me", {"XDG_CONFIG_HOME": "/cfg"})[0][1] == "/cfg/OrcaSlicer"


def test_candidate_dirs_touches_no_filesystem(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("filesystem access")
    monkeypatch.setattr(os, "stat", boom)
    monkeypatch.setattr(os, "listdir", boom)
    im.candidate_dirs("linux", "/nonexistent", {})


# -- scanning ------------------------------------------------------------------------------------

def test_scan_finds_user_dirs_and_kinds(tmp_path):
    put(tmp_path, "1234", "process", "a.json", {"name": "A", "inherits": "p"})
    put(tmp_path, "default", "filament", "b.json", {"name": "B"})
    put(tmp_path, "1234", "machine", "c.json", {"name": "C"})
    put(tmp_path, "1234", "machine", "c.info", "ignored")
    put(tmp_path, "1234", "other", "d.json", {"name": "D"})
    got = im.scan(str(tmp_path), "OrcaSlicer")
    assert [(c.kind, c.name, c.inherits, c.app) for c in got.candidates] == [
        ("machine", "C", "", "OrcaSlicer"), ("process", "A", "p", "OrcaSlicer"),
        ("filament", "B", "", "OrcaSlicer")]
    assert got.skipped == []
    assert im.scan(str(tmp_path / "missing")).candidates == []


def test_scan_skips_bad_huge_and_non_object_files(tmp_path):
    put(tmp_path, "1", "process", "good.json", {"name": "good"})
    put(tmp_path, "1", "process", "bad.json", "{nope")
    put(tmp_path, "1", "process", "list.json", "[1]")
    put(tmp_path, "1", "process", "huge.json", {"name": "huge", "pad": "x" * (im.MAX_FILE_BYTES + 1)})
    got = im.scan(str(tmp_path))
    assert [c.name for c in got.candidates] == ["good"]
    reasons = {os.path.basename(p): r for p, r in got.skipped}
    assert "unreadable" in reasons["bad.json"] and "not a JSON object" in reasons["list.json"]
    assert "2 MB" in reasons["huge.json"]


def test_scan_does_not_follow_symlinks_out_of_the_base(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text('{"name": "stolen"}')
    base = tmp_path / "base"
    folder = base / "user" / "1" / "process"
    folder.mkdir(parents=True)
    (folder / "ok.json").write_text('{"name": "ok"}')
    try:
        os.symlink(outside / "secret.json", folder / "link.json")
        os.symlink(outside, base / "user" / "1" / "machine")
    except OSError:
        pytest.skip("symlinks unavailable")
    got = im.scan(str(base))
    assert [c.name for c in got.candidates] == ["ok"]
    assert any("symbolic link" in r for _, r in got.skipped)


# -- planning ------------------------------------------------------------------------------------

def cand(kind, name, inherits="", **extra):
    return im.Candidate("OrcaSlicer", f"/x/{name}.json", kind, name, inherits,
                        {"name": name, "inherits": inherits, **extra})


def test_known_parent_keeps_inherits_and_only_the_differences(env, tmp_path):
    index, resolver = env
    store = UserStore(str(tmp_path / "store"))
    c = cand("process", "Mine", "0.20mm Standard @Acme", wall_loops="4", layer_height="0.2",
             version="2.0", setting_id="X", user_id="u", updated_time="1", is_custom_defined="1",
             from_="User")
    [item] = im.plan_import([c], index, resolver, store)
    assert item.parent == "0.20mm Standard @Acme" and item.warnings == []
    assert item.preset_json == {"type": "process", "from": "User", "inherits": "0.20mm Standard @Acme",
                                "name": "Mine", "version": "2.0", "wall_loops": "4", "from_": "User"}


def test_unknown_parent_is_flattened_with_a_warning(env, tmp_path):
    index, resolver = env
    store = UserStore(str(tmp_path / "store"))
    [item] = im.plan_import([cand("process", "P", "Some Other Printer Profile", wall_loops="9")],
                            index, resolver, store)
    assert item.parent is None and "inherits" not in item.preset_json
    assert item.preset_json["wall_loops"] == "9"
    assert "parent 'Some Other Printer Profile' not found; imported without inheritance" in item.warnings[0]


def test_no_parent_means_no_warning(env, tmp_path):
    index, resolver = env
    [item] = im.plan_import([cand("filament", "F", filament_type=["PLA"])], index, resolver,
                            UserStore(str(tmp_path)))
    assert item.parent is None and item.warnings == []


def test_name_clashes_get_an_imported_suffix(env, tmp_path):
    index, resolver = env
    store = UserStore(str(tmp_path / "store"))
    store.save("process", "Dup", None, {})
    items = im.plan_import([cand("process", "Dup"), cand("process", "Dup"), cand("process", "Dup"),
                            cand("machine", "Dup")], index, resolver, store)
    assert [i.name for i in items] == ["Dup (imported)", "Dup (imported 2)", "Dup (imported 3)", "Dup"]
    assert "saved as 'Dup (imported)'" in items[0].warnings[0]
    assert items[0].preset_json["name"] == "Dup (imported)"


def test_secrets_are_stripped(env, tmp_path):
    index, resolver = env
    c = cand("machine", "M", print_host="10.0.0.5", printhost_apikey="KEY", bambu_access_code="9999",
             printhost_password="pw", gcode_flavor="klipper")
    [item] = im.plan_import([c], index, resolver, UserStore(str(tmp_path)))
    text = json.dumps(item.preset_json)
    for secret in ("10.0.0.5", "KEY", "9999", "pw"):
        assert secret not in text
    assert item.preset_json["gcode_flavor"] == "klipper"


# -- applying and read-only behaviour ------------------------------------------------------------

def test_apply_writes_through_the_store_and_never_overwrites(env, tmp_path):
    index, resolver = env
    store = UserStore(str(tmp_path / "store"))
    items = im.plan_import([cand("process", "A", "0.20mm Standard @Acme", wall_loops="5")],
                           index, resolver, store)
    [res] = im.apply_import(items, store)
    assert res.error is None and os.path.isfile(res.path)
    assert store.load("process", "A")["wall_loops"] == "5"
    [again] = im.apply_import(items, store)                 # planned before it existed; now it does
    assert again.path is None and "already exists" in again.error
    assert store.load("process", "A")["wall_loops"] == "5"


def test_scan_and_plan_leave_the_source_untouched(env, tmp_path):
    index, resolver = env
    base = tmp_path / "Orca"
    put(base, "1", "process", "a.json", {"name": "A", "inherits": "0.20mm Standard @Acme", "wall_loops": "5"})
    put(base, "1", "machine", "bad.json", "{x")
    before = snapshot(base)
    found = im.scan(str(base), "OrcaSlicer")
    im.plan_import(found.candidates, index, resolver, UserStore(str(tmp_path / "store")))
    assert snapshot(base) == before
    assert not (tmp_path / "store").exists()                # planning does not even create our store


def test_scan_works_on_a_read_only_tree(tmp_path):
    base = tmp_path / "Orca"
    p = put(base, "1", "process", "a.json", {"name": "A"})
    os.chmod(p, 0o444)
    os.chmod(p.parent, 0o555)
    try:
        assert [c.name for c in im.scan(str(base)).candidates] == ["A"]
    finally:
        os.chmod(p.parent, 0o755)
