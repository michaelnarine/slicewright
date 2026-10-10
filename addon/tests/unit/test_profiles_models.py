# SPDX-License-Identifier: GPL-3.0-or-later
"""Vendor -> model -> nozzle -> printer, and the defaults of a new printer (03 section 3.7)."""
import pytest
from profile_helpers import build, fixture_index, make_archive, preset

from slicewright.core.profiles import models as m
from slicewright.core.profiles.source import ProfileSource


@pytest.fixture(scope="module")
def idx():
    return fixture_index()


def test_vendors_exclude_the_filament_library(idx):
    assert m.vendors(idx) == ["Acme", "Bolt3D"]


def test_models_and_nozzles(idx):
    assert [e.name for e in m.models(idx, "Acme")] == ["Acme Maker 1", "Acme Maker 2"]
    maker1 = idx.get("model", "sys:Acme/Acme Maker 1")
    assert m.nozzle_options(maker1) == ["0.4", "0.6"]
    assert m.nozzle_options(idx.get("model", "sys:Bolt3D/Bolt One")) == ["0.4"]


def test_nozzles_are_normalised_and_deduplicated(tmp_path):
    src = ProfileSource(make_archive(tmp_path, {"V/machine/M.json": {
        "type": "machine_model", "name": "M", "nozzle_diameter": "0.40;0.4;0.6;0.60"}}))
    with src:
        model = build(src).get("model", "sys:V/M")
    assert m.nozzle_options(model) == ["0.4", "0.6"]


def test_printer_for_matches_model_and_variant(idx):
    maker1 = idx.get("model", "sys:Acme/Acme Maker 1")
    assert m.printer_for(idx, maker1, "0.6").name == "Acme Maker 1 0.6 nozzle"
    assert m.printer_for(idx, maker1, "0.60").name == "Acme Maker 1 0.6 nozzle"
    assert m.printer_for(idx, maker1, "0.8") is None
    assert m.model_of(idx, idx.get("machine", "sys:Acme/Acme Maker 1 0.6 nozzle")) is maker1


def test_a_new_printer_takes_the_default_process_and_materials(idx):
    got = m.choose(idx, idx.get("model", "sys:Acme/Acme Maker 1"), "0.6")
    assert got.printer.name == "Acme Maker 1 0.6 nozzle"
    assert got.process.name == "0.30mm Draft @Acme"
    # "Generic PLA" resolves to the library preset; the vendor shadow only differs by alias
    assert [e.name for e in got.filaments] == ["Generic PLA", "Generic PETG", "Acme PLA Pro"]
    assert m.choose(idx, idx.get("model", "sys:Acme/Acme Maker 1"), "1.0") is None


def test_default_materials_prefer_the_printers_vendor(idx):
    got = m.choose(idx, idx.get("model", "sys:Bolt3D/Bolt One"), "0.4")
    assert [e.vendor for e in got.filaments] == ["Bolt3D", "Bolt3D"]
    assert got.process.id == "sys:Bolt3D/0.20mm Bolt"


def test_missing_defaults_are_dropped_not_errors(tmp_path):
    src = ProfileSource(make_archive(tmp_path, {
        "V/machine/M.json": {"type": "machine_model", "name": "M", "nozzle_diameter": "0.4",
                             "default_materials": "Nope;Also Nope;Base;Real"},
        "V/machine/M 0.4.json": preset("machine", "M 0.4", printer_model="M", printer_variant="0.4",
                                       default_print_profile="Gone"),
        "V/filament/Base.json": preset("filament", "Base", instantiation=False),
        "V/filament/Real.json": preset("filament", "Real")}))
    with src:
        index = build(src)
    got = m.choose(index, index.get("model", "sys:V/M"), "0.4")
    assert got.process is None and [e.name for e in got.filaments] == ["Real"]   # bases are not selectable


def test_search_matches_every_word_in_vendor_or_name(idx):
    printers = idx.of_kind("machine", selectable_only=True)
    assert m.search_ids(printers, "") == [e.id for e in printers]
    assert m.search_ids(printers, "maker 0.6") == ["sys:Acme/Acme Maker 1 0.6 nozzle"]
    assert m.search_ids(printers, "BOLT one") == ["sys:Bolt3D/Bolt One 0.4 nozzle"]
    assert m.search_ids(printers, "acme maker", limit=2) == [
        "sys:Acme/Acme Maker 1 0.4 nozzle", "sys:Acme/Acme Maker 1 0.6 nozzle"]
    assert m.search_ids(printers, "zzz") == []
