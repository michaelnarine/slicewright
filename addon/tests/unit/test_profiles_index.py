# SPDX-License-Identifier: GPL-3.0-or-later
"""Profile source and index (03 sections 3.3 and 3.4)."""
import json
import time

import pytest
from profile_helpers import build, drain, fixture_index, fixture_source, make_archive, preset

from slicewright.core.profiles import index as ix
from slicewright.core.profiles.source import ProfileError, ProfileSource
from slicewright.core.ticking import TickRunner


@pytest.fixture(scope="module")
def idx():
    return fixture_index()


def test_fixture_vendors_and_counts(idx):
    assert sorted(idx.vendors) == ["Acme", "Bolt3D", "OrcaFilamentLibrary"]
    assert idx.vendors["Acme"]["version"] == "2.0.1.0"
    assert {k: len(idx.of_kind(k)) for k in ix.KINDS} == {
        "machine": 6, "process": 6, "filament": 11, "model": 3}
    assert idx.skipped == 0


def test_selectable_excludes_bases_and_templates(idx):
    names = {e.name for e in idx.of_kind("machine", selectable_only=True)}
    assert names == {"Acme Maker 1 0.4 nozzle", "Acme Maker 1 0.6 nozzle",
                     "Acme Maker 2 0.4 nozzle", "Bolt One 0.4 nozzle"}
    assert idx.get("machine", "sys:Bolt3D/gcode_bolt_macros").is_template
    assert not idx.get("machine", "sys:Acme/fdm_acme_common").is_template


def test_entry_fields(idx):
    printer = idx.get("machine", "sys:Acme/Acme Maker 1 0.6 nozzle")
    assert (printer.printer_model, printer.printer_variant) == ("Acme Maker 1", "0.6")
    assert printer.inherits == "fdm_acme_common"
    assert printer.info["default_print_profile"] == "0.30mm Draft @Acme"
    model = idx.get("model", "sys:Acme/Acme Maker 1")
    assert model.info["nozzle_diameter"] == "0.4;0.6"
    assert model.info["default_materials"] == "Generic PLA;Generic PETG;Acme PLA Pro"
    shadow = idx.get("filament", "sys:Acme/Generic PLA @Acme")
    assert shadow.alias == "Generic PLA"
    assert shadow.compatible_printers == ["Acme Maker 1 0.4 nozzle"]
    assert shadow.compatible_printers_condition is None      # absent, not empty
    generic = idx.get("filament", "sys:OrcaFilamentLibrary/Generic PLA")
    assert generic.compatible_printers is None
    assert idx.get("filament", "sys:OrcaFilamentLibrary/Generic ABS").compatible_printers == []


def test_include_is_recorded(idx):
    assert idx.get("machine", "sys:Bolt3D/Bolt One 0.4 nozzle").include == ["gcode_bolt_macros"]


def test_lookup_prefers_the_same_vendor_then_the_library(idx):
    assert idx.lookup("filament", "fdm_filament_pla", "Bolt3D").vendor == "Bolt3D"
    assert idx.lookup("filament", "fdm_filament_pla", "Acme").vendor == "OrcaFilamentLibrary"
    assert idx.lookup("filament", "no such preset", "Acme") is None


def test_chain_cycle_and_missing_parent(tmp_path):
    path = make_archive(tmp_path, {
        "V/machine/a.json": preset("machine", "a", "b"), "V/machine/b.json": preset("machine", "b", "a"),
        "V/machine/c.json": preset("machine", "c", "nope")})
    with ProfileSource(path) as src:
        idx = build(src)
    with pytest.raises(ProfileError, match="cycle"):
        idx.chain(idx.get("machine", "sys:V/a"))
    with pytest.raises(ProfileError, match="not found"):
        idx.chain(idx.get("machine", "sys:V/c"))


def test_unreadable_files_are_skipped_and_counted(tmp_path):
    path = make_archive(tmp_path, {
        "V/machine/good.json": preset("machine", "good"), "V/machine/bad.json": "{not json",
        "V/machine/list.json": "[1, 2]"})
    with ProfileSource(path) as src:
        idx = build(src)
    assert [e.name for e in idx.entries] == ["good"] and idx.skipped == 2


def test_archive_root_without_profiles_prefix(tmp_path):
    path = make_archive(tmp_path, {"V.json": {"name": "V", "version": "1"},
                                   "V/process/p.json": preset("process", "p")}, root="")
    with ProfileSource(path) as src:
        idx = build(src)
    assert idx.vendors["V"]["version"] == "1" and idx.get("process", "sys:V/p")


def test_missing_archive_is_a_profile_error(tmp_path):
    with pytest.raises(ProfileError):
        ProfileSource(str(tmp_path / "nope.zip")).paths()


def test_only_machine_process_filament_dirs_are_scanned(tmp_path):
    path = make_archive(tmp_path, {"V/other/x.json": preset("machine", "x"),
                                   "V/machine/deep/y.json": preset("machine", "y"),
                                   "V/machine/z.json": preset("machine", "z")})
    with ProfileSource(path) as src:
        assert [e.name for e in build(src).entries] == ["z"]


# -- chunking and the time budget ----------------------------------------------------------------

def _big_archive(tmp_path, n=2400):
    files = {f"V{i % 8}/filament/p{i}.json": preset("filament", f"p{i}", "base", filament_type=["PLA"],
                                                  nozzle_temperature=["200"] * 4, junk="x" * 200)
             for i in range(n)}
    return make_archive(tmp_path, files)


def test_build_runs_in_small_steps_under_the_tick_budget(tmp_path):
    runner = TickRunner(budget_s=0.025)
    step_times, progress = [], []

    def timed():
        gen = ix.build_index(src)
        while True:
            t0 = time.perf_counter()
            try:
                value = next(gen)
            except StopIteration as stop:
                step_times.append(time.perf_counter() - t0)
                return stop.value
            step_times.append(time.perf_counter() - t0)
            progress.append(value[0])
            yield value

    with ProfileSource(_big_archive(tmp_path)) as src:
        task = runner.submit("index", timed())
        ticks = runner.run_until_idle()
    assert len(task.result) == 2400
    assert ticks > 1 and len(step_times) > 8            # several steps, spread over several ticks
    assert max(step_times) < 0.025                      # no single step blows the tick budget
    assert progress == sorted(progress) and progress[-1] == 1.0
    assert task.message.endswith("100%")


def test_step_stops_at_the_time_budget_but_always_makes_progress(tmp_path):
    ticks = iter(range(10_000))
    clock = lambda: next(ticks) * 1.0            # every reading is one "second" later
    with ProfileSource(_big_archive(tmp_path, 40)) as src:
        gen = ix.build_index(src, step_files=300, step_budget_s=0.5, clock=clock)
        steps = 0
        try:
            while True:
                next(gen)
                steps += 1
        except StopIteration as stop:
            assert len(stop.value) == 40
    assert steps == 40                               # budget spent after each file: one file per step


def test_step_files_caps_a_step(tmp_path):
    with ProfileSource(_big_archive(tmp_path, 25)) as src:
        gen = ix.build_index(src, step_files=10, step_budget_s=60)
        steps = sum(1 for _ in iter(lambda: next(gen, None), None))
    assert steps == 3                                # 10 + 10 + 5


# -- cache ---------------------------------------------------------------------------------------

def test_cache_roundtrip_and_invalidation(tmp_path):
    with fixture_source() as src:
        idx = build(src, commit="abc123")
        path = ix.cache_path(str(tmp_path / "cache"), "abc123")
        assert path.endswith("profile-index-abc123.json")
        assert ix.save_cache(idx, path)
        loaded = ix.load_cache(path, "abc123", src.size())
        assert loaded is not None and [e.id for e in loaded.entries] == [e.id for e in idx.entries]
        assert loaded.get("filament", "sys:Acme/Generic PLA @Acme").compatible_printers == [
            "Acme Maker 1 0.4 nozzle"]
        assert ix.load_cache(path, "other-commit", src.size()) is None
        assert ix.load_cache(path, "abc123", src.size() + 1) is None
        assert ix.load_cache(str(tmp_path / "missing.json"), "abc123", src.size()) is None
    (tmp_path / "cache" / "profile-index-abc123.json").write_text("{broken")
    assert ix.load_cache(path, "abc123", 0) is None
    (tmp_path / "cache" / "profile-index-abc123.json").write_text(json.dumps({"format": 99}))
    assert ix.load_cache(path, "abc123", 0) is None


def test_cache_path_sanitises_the_commit(tmp_path):
    assert ix.cache_path(str(tmp_path), "../x/y").endswith("profile-index-xy.json")
    assert ix.cache_path(str(tmp_path), "").endswith("profile-index-unknown.json")


def test_load_or_build_uses_the_cache_second_time(tmp_path):
    cache = str(tmp_path / "cache")
    with fixture_source() as src:
        first = drain(ix.load_or_build(src, cache, "c0ffee"))
        gen = ix.load_or_build(src, cache, "c0ffee")
        assert next(gen)[1] == "Printer library loaded"      # one step, no parsing
        with pytest.raises(StopIteration) as stop:
            next(gen)
        assert len(stop.value.value) == len(first)


def test_unwritable_cache_does_not_fail_the_build(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with fixture_source() as src:
        idx = drain(ix.load_or_build(src, str(blocker / "sub"), "c0ffee"))
    assert len(idx) > 0
