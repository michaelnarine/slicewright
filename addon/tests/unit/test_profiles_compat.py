# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility behaviour (03 section 3.6), evaluated through the engine's ConditionContext."""
import logging

import fake_engine
import pytest
from profile_helpers import build, fixture_source, make_archive, preset

from slicewright.core.profiles.compat import Compat, Subject, _num_extruders, subject_from_entry
from slicewright.core.profiles.resolve import Resolver
from slicewright.core.profiles.source import ProfileSource

SCHEMA = fake_engine.config_schema()
LIB = "OrcaFilamentLibrary"


class Env:
    def __init__(self, src: ProfileSource):
        self.src = src
        self.index = build(src)
        self.resolver = Resolver(self.index, src, SCHEMA)
        self.compat = Compat(fake_engine, self.index, self.resolver)

    def subject(self, kind, preset_id):
        return subject_from_entry(self.resolver, kind, self.index.get(kind, preset_id))

    def printer(self, name, vendor="Acme"):
        return self.subject("machine", f"sys:{vendor}/{name}")

    def process(self, name, vendor="Acme"):
        return self.subject("process", f"sys:{vendor}/{name}")

    def names(self, entries):
        return {e.name for e in entries}


@pytest.fixture(scope="module")
def fx():
    with fixture_source() as src:
        yield Env(src)


def tmp_env(tmp_path, files):
    return Env(ProfileSource(make_archive(tmp_path, files)))


# -- a table over the fixture printers (hand-derived from the fixture, one row per printer) -----

ALL_PROCESSES = {"0.20mm Standard @Acme", "0.30mm Draft @Acme", "0.12mm Fine @Acme",
                 "0.20mm Strong @Acme", "0.20mm Bolt"}
PROCESSES = {
    "Acme Maker 1 0.4 nozzle": {"0.20mm Standard @Acme", "0.12mm Fine @Acme", "0.20mm Strong @Acme"},
    "Acme Maker 1 0.6 nozzle": {"0.30mm Draft @Acme", "0.20mm Strong @Acme"},
    "Acme Maker 2 0.4 nozzle": {"0.20mm Standard @Acme", "0.12mm Fine @Acme", "0.20mm Strong @Acme"},
    "Bolt One 0.4 nozzle": {"0.20mm Bolt", "0.20mm Strong @Acme"},
}
COMMON = {"Generic PETG", "Generic ABS", "Bolt PLA", "Bolt PETG"}
FILAMENTS = {
    "Acme Maker 1 0.4 nozzle": COMMON | {"Generic PLA @Acme", "Acme PLA Pro"},     # library PLA hidden
    "Acme Maker 1 0.6 nozzle": COMMON | {"Generic PLA", "Acme PLA Pro"},
    "Acme Maker 2 0.4 nozzle": COMMON | {"Generic PLA"},
    "Bolt One 0.4 nozzle": COMMON | {"Generic PLA"},
}


@pytest.mark.parametrize("printer", PROCESSES)
def test_processes_per_printer(fx, printer):
    vendor = "Bolt3D" if printer.startswith("Bolt") else "Acme"
    assert fx.names(fx.compat.compatible_processes(fx.printer(printer, vendor))) == PROCESSES[printer]


@pytest.mark.parametrize("printer", FILAMENTS)
def test_filaments_per_printer(fx, printer):
    vendor = "Bolt3D" if printer.startswith("Bolt") else "Acme"
    assert fx.names(fx.compat.compatible_filaments(fx.printer(printer, vendor))) == FILAMENTS[printer]


def test_cross_vendor_matches_are_allowed(fx):
    bolt = fx.printer("Bolt One 0.4 nozzle", "Bolt3D")
    assert "0.20mm Strong @Acme" in fx.names(fx.compat.compatible_processes(bolt))
    acme = fx.printer("Acme Maker 2 0.4 nozzle")
    assert "Bolt PLA" in fx.names(fx.compat.compatible_filaments(acme))


def test_non_selectable_presets_are_never_listed(fx):
    names = fx.names(fx.compat.compatible_filaments(fx.printer("Acme Maker 1 0.4 nozzle")))
    assert not {n for n in names if n.startswith("fdm_")}


# -- the process leg: compatible_prints(_condition) ----------------------------------------------

def test_filaments_must_also_accept_the_process(fx):
    printer = fx.printer("Acme Maker 1 0.6 nozzle")
    with_draft = fx.compat.compatible_filaments(printer, fx.process("0.30mm Draft @Acme"))
    with_strong = fx.compat.compatible_filaments(printer, fx.process("0.20mm Strong @Acme"))
    assert "Acme PLA Pro" not in fx.names(with_draft)       # condition: layer_height < 0.3
    assert "Acme PLA Pro" in fx.names(with_strong)
    assert "Generic PETG" in fx.names(with_draft)           # no compatible_prints at all


def test_compatible_prints_list_and_empty_cases(tmp_path):
    env = tmp_env(tmp_path, {
        "V/machine/m.json": preset("machine", "m"),
        "V/process/p1.json": preset("process", "p1"), "V/process/p2.json": preset("process", "p2"),
        "V/filament/f_list.json": preset("filament", "f_list", compatible_prints=["p1"]),
        "V/filament/f_none.json": preset("filament", "f_none")})
    m = env.subject("machine", "sys:V/m")
    assert env.names(env.compat.compatible_filaments(m, env.subject("process", "sys:V/p1"))) == {
        "f_list", "f_none"}
    assert env.names(env.compat.compatible_filaments(m, env.subject("process", "sys:V/p2"))) == {"f_none"}


# -- the printer leg: list, condition, user presets ----------------------------------------------

def test_a_non_empty_list_wins_over_a_condition(tmp_path):
    env = tmp_env(tmp_path, {
        "V/machine/m.json": preset("machine", "m", nozzle_diameter=["0.4"]),
        "V/process/p.json": preset("process", "p", compatible_printers=["other"],
                                   compatible_printers_condition="nozzle_diameter[0] == 0.4")})
    assert env.compat.compatible_processes(env.subject("machine", "sys:V/m")) == []


def test_empty_list_without_condition_is_compatible(tmp_path):
    env = tmp_env(tmp_path, {"V/machine/m.json": preset("machine", "m"),
                             "V/process/p.json": preset("process", "p", compatible_printers=[])})
    assert env.names(env.compat.compatible_processes(env.subject("machine", "sys:V/m"))) == {"p"}


def test_the_condition_sees_printer_preset_and_num_extruders(tmp_path):
    env = tmp_env(tmp_path, {
        "V/machine/m.json": preset("machine", "m", nozzle_diameter=["0.4", "0.4"]),
        "V/process/ok.json": preset("process", "ok", compatible_printers_condition=(
            'printer_preset == "m" and num_extruders == 2')),
        "V/process/no.json": preset("process", "no", compatible_printers_condition="num_extruders == 1")})
    assert env.names(env.compat.compatible_processes(env.subject("machine", "sys:V/m"))) == {"ok"}


def test_condition_errors_count_as_compatible_and_are_logged(tmp_path, caplog):
    env = tmp_env(tmp_path, {
        "V/machine/m.json": preset("machine", "m"),
        "V/process/broken.json": preset("process", "broken", compatible_printers_condition="((("),
        "V/process/unknown.json": preset("process", "unknown",
                                         compatible_printers_condition="no_such_key == 1")})
    with caplog.at_level(logging.WARNING, logger="slicewright.profiles"):
        got = env.compat.compatible_processes(env.subject("machine", "sys:V/m"))
    assert env.names(got) == {"broken", "unknown"}
    assert len([r for r in caplog.records if "treating as compatible" in r.message]) == 2


def test_a_user_printer_matches_through_its_inherits(tmp_path):
    env = tmp_env(tmp_path, {
        "V/machine/sys.json": preset("machine", "sys"),
        "V/process/p.json": preset("process", "p", compatible_printers=["sys"]),
        "V/process/q.json": preset("process", "q", compatible_printers=["elsewhere"])})
    cfg = env.subject("machine", "sys:V/sys").config
    mine = Subject("user:Mine", "Mine", cfg, inherits="sys", is_user=True)
    assert env.names(env.compat.compatible_processes(mine)) == {"p"}
    # a system printer's own `inherits` is never matched against the list
    sys_like = Subject("sys:V/x", "x", cfg, inherits="sys", is_user=False)
    assert env.compat.compatible_processes(sys_like) == []


def test_the_library_rule_also_matches_the_printers_inherits(tmp_path):
    env = tmp_env(tmp_path, {
        f"{LIB}/filament/Generic PLA.json": preset("filament", "Generic PLA"),
        "V/filament/Generic PLA @V.json": preset("filament", "Generic PLA @V",
                                                 compatible_printers=["sys"]),
        "V/machine/sys.json": preset("machine", "sys")})
    cfg = env.subject("machine", "sys:V/sys").config
    mine = Subject("user:Mine", "Mine", cfg, inherits="sys", is_user=True)
    other = Subject("user:Other", "Other", cfg, inherits="x", is_user=True)
    assert env.names(env.compat.compatible_filaments(mine)) == {"Generic PLA @V"}
    assert env.names(env.compat.compatible_filaments(other)) == {"Generic PLA"}


def test_the_library_rule_only_applies_to_library_presets_with_an_empty_list(tmp_path):
    env = tmp_env(tmp_path, {
        f"{LIB}/filament/A.json": preset("filament", "A", compatible_printers=["m"]),
        f"{LIB}/filament/B.json": preset("filament", "B"),
        "V/filament/A @V.json": preset("filament", "A @V", compatible_printers=["m"]),
        "V/filament/B @V.json": preset("filament", "B @V", compatible_printers=["m"]),
        "V/filament/B.json": preset("filament", "B", compatible_printers=["m"]),   # vendor preset, same name
        "V/machine/m.json": preset("machine", "m")})
    got = env.names(env.compat.compatible_filaments(env.subject("machine", "sys:V/m")))
    assert got == {"A", "A @V", "B @V", "B"}     # library A has a list; library B is hidden, V's B is not


def test_compat_fields_are_inherited_through_the_chain(tmp_path):
    env = tmp_env(tmp_path, {
        "V/machine/m1.json": preset("machine", "m1"), "V/machine/m2.json": preset("machine", "m2"),
        "V/process/base.json": preset("process", "base", instantiation=False, compatible_printers=["m1"]),
        "V/process/inherits.json": preset("process", "inherits", "base"),
        "V/process/cleared.json": preset("process", "cleared", "base", compatible_printers=[])})
    assert env.names(env.compat.compatible_processes(env.subject("machine", "sys:V/m1"))) == {
        "inherits", "cleared"}
    assert env.names(env.compat.compatible_processes(env.subject("machine", "sys:V/m2"))) == {"cleared"}


def test_a_preset_with_a_broken_chain_is_hidden(tmp_path, caplog):
    env = tmp_env(tmp_path, {"V/machine/m.json": preset("machine", "m"),
                             "V/process/lost.json": preset("process", "lost", "ghost"),
                             "V/process/fine.json": preset("process", "fine")})
    with caplog.at_level(logging.WARNING, logger="slicewright.profiles"):
        assert env.names(env.compat.compatible_processes(env.subject("machine", "sys:V/m"))) == {"fine"}
    assert any("lost" in r.message for r in caplog.records)


# -- caching -------------------------------------------------------------------------------------

def test_results_are_cached_per_printer_and_process_until_invalidated(fx):
    printer = fx.printer("Acme Maker 1 0.4 nozzle")
    process = fx.process("0.20mm Standard @Acme")
    first = fx.compat.compatible_filaments(printer, process)
    evaluations = fx.compat.evaluations
    assert fx.compat.compatible_filaments(printer, process) is first
    assert fx.compat.evaluations == evaluations
    assert fx.compat.compatible_filaments(printer) is not first       # another key
    fx.compat.invalidate()
    again = fx.compat.compatible_filaments(printer, process)
    assert again == first and again is not first


def test_num_extruders():
    assert _num_extruders({"nozzle_diameter": ["0.4", "0.6"]}) == 2
    assert _num_extruders({"nozzle_diameter": "0.4,0.4,0.4"}) == 3
    assert _num_extruders({}) == 1
