# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 7: the CLI/GUI config glue of 02 section 5.9, one test per item (engine/src/glue/print_glue.cpp).

* filament_map sizing and the single-extruder rule          (OrcaSlicer.cpp:6027-6034)
* nozzle_volume_type default                                  (6035-6040)
* is_BBL_printer from printer_model                           (6046-6059)
* Model::setExtruderParams / setPrintSpeedTable               (6138-6139)
* print->set_extruder_filament_info (multi-nozzle printers)   (5987-6023)
"""
import re

import numpy as np
import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402
from test_validating_errors import box  # noqa: E402

NATIVE = sc._native


def block(text, key):
    m = re.search(rf"^; {key} = (.*)$", text, re.M)
    return m.group(1) if m else None


def flat_config(machine_extra=None, filaments=1, project=None, process_extra=None):
    p = cube_case.profiles()
    machine = {**p["machine"], **(machine_extra or {})}
    fil = [p["filament"]] + [{**p["filament"], "name": f"F{i}", "filament_colour": [f"#11223{i}"]} for i in range(1, filaments)]
    proj = {"flush_volumes_matrix": ",".join("0" if i == j else "100" for i in range(filaments) for j in range(filaments))} if filaments > 1 else {}
    proj.update(project or {})
    return sc.normalize_config(sc.compose_config(machine, {**p["process"], **(process_extra or {})}, fil, proj or None))["config"]


def slice_cube(flat, objects=1):
    j = sc.SliceJob()
    j.set_config(flat)
    cx, cy = cube_case.bed_centre(cube_case.profiles()["machine"])
    for i in range(objects):
        j.add_object(f"o{i}", *box(cx + 40 * (i - (objects - 1) / 2), cy, size=15.0), extruder=1 + i % 2 if objects > 1 else 0)
    r = j.run()
    return open(r.gcode_path, errors="replace").read(), r


def test_filament_map_gets_one_entry_per_filament():
    flat = flat_config(filaments=3)
    flat["filament_map"] = "1"  # shorter than the filament count
    text, _ = slice_cube(flat, objects=2)
    assert block(text, "filament_map") == "1,1,1"


def test_a_single_extruder_printer_maps_every_filament_to_it():
    flat = flat_config(filaments=2)
    flat["filament_map"] = "2,2"
    text, _ = slice_cube(flat, objects=2)
    assert block(text, "filament_map") == "1,1"


def test_nozzle_volume_type_has_a_standard_entry_per_extruder():
    text, _ = slice_cube(flat_config())
    assert block(text, "nozzle_volume_type") == "Standard"


def test_the_bambu_dialect_follows_printer_model():
    plain_text, _ = slice_cube(flat_config())
    assert NATIVE._glue_state()["is_bbl_processor"] is False
    bbl_text, _ = slice_cube(flat_config(machine_extra={"printer_model": "Bambu Lab A1"}))
    assert NATIVE._glue_state()["is_bbl_processor"] is True
    # exclude_object is on for the Bambu dialect only: its object start/stop and label list appear
    assert "; model label id:" in bbl_text and "; model label id:" not in plain_text
    # and the next plain job resets the process-global flag again (it defaults to true and is flipped by Orca)
    slice_cube(flat_config())
    assert NATIVE._glue_state()["is_bbl_processor"] is False


def test_the_global_extruder_and_speed_tables_are_filled_per_job():
    flat = flat_config(filaments=2)
    slice_cube(flat, objects=2)
    state = NATIVE._glue_state()
    first_keys = set(state["extruder_params"])
    assert {0, 1} <= first_keys  # one per filament (Orca adds one more entry for the wipe tower)
    assert state["extruder_params"][0]["material"] == "PLA"
    assert state["extruder_params"][0]["end_temp"] > 0
    speeds = state["speed_map"]
    assert speeds["bed_points"] >= 4 and speeds["max"] >= max(speeds["perimeter"], speeds["external_perimeter"], speeds["infill"]) > 0
    # a later job with other values replaces them (nothing stale from the first job)
    slice_cube(flat_config(process_extra={"outer_wall_speed": "123"}))
    later = NATIVE._glue_state()
    assert len(later["extruder_params"]) == len(first_keys) - 1  # one filament now: nothing stale from the first job
    assert later["speed_map"]["external_perimeter"] == 123.0


def test_brim_uses_the_extruder_params_table():
    text, r = slice_cube(flat_config(process_extra={"brim_type": "outer_only", "brim_width": "5"}))
    assert re.search(r"^;TYPE:Brim$", text, re.M)
    assert (r.moves["role"] == sc.enums()["role"]["Brim"]).any()


# --- multi-nozzle printers (set_extruder_filament_info) ----------------------------------------------------

TWO_NOZZLES = {"nozzle_diameter": ["0.4", "0.4"], "extruder_offset": ["0x0", "0x0"], "extruder_colour": ["#FCE94F", "#FCE94F"]}


def two_nozzle_flat():
    p = cube_case.profiles()
    machine = dict(TWO_NOZZLES)
    for k, v in p["machine"].items():
        if isinstance(v, list) and len(v) == 1 and k.startswith(("retract", "deretraction", "wipe", "z_hop", "travel_slope", "min_layer", "max_layer",
                                                                  "extruder_printable", "nozzle_type", "extruder_type")):
            machine[k] = v * 2
    return flat_config(machine_extra=machine, filaments=2, project={"flush_volumes_matrix": "0,100,100,0,0,100,100,0", "flush_multiplier": "1,1"})


def test_a_two_nozzle_printer_gets_the_default_filament_info_and_an_automatic_map():
    text, r = slice_cube(two_nozzle_flat(), objects=2)
    assert block(text, "extruder_ams_count") == "1#0|4#1;1#0|4#1"
    assert block(text, "filament_map_mode") == "Auto For Flush"
    assert block(text, "nozzle_volume_type") == "Standard,Standard"
    # the automatic mapping put the two filaments on different nozzles and the result carries it (moves.nozzle)
    assert block(text, "filament_map") == "1,2"
    m = r.moves
    assert set(np.unique(m["nozzle"]).tolist()) == {0, 1}
    ext = m["type"] == sc.enums()["move_type"]["Extrude"]
    for f, nozzle in ((0, 0), (1, 1)):
        assert (m["nozzle"][ext & (m["filament"] == f)] == nozzle).all()


# --- Bambu H2D: real presets, several variants per filament ------------------------------------------------

import bbl_profiles  # noqa: E402


def h2d(filaments=2, collapse_variants=False, **project):
    printer = bbl_profiles.resolve("machine", "Bambu Lab H2D 0.4 nozzle")
    process = bbl_profiles.resolve("process", "0.20mm Standard @BBL H2D")
    filament = bbl_profiles.resolve("filament", "Bambu PLA Basic @BBL H2D")
    proj = {"flush_volumes_matrix": "0,100,100,0,0,100,100,0", "flush_multiplier": "1,1"}
    proj.update(project)
    return sc.compose_config(printer, process, [filament] * filaments, proj, collapse_variants=collapse_variants)


VARIANT_CASES = [
    ("Standard,Standard", "1,2", "25,25", ["Direct Drive Standard", "Direct Drive Standard"]),
    ("Standard,High Flow", "1,2", "25,40", ["Direct Drive Standard", "Direct Drive High Flow"]),
    ("Standard,High Flow", "2,1", "40,25", ["Direct Drive High Flow", "Direct Drive Standard"]),
]


@pytest.mark.parametrize("volume_types,filament_map,expected_speeds,expected_variants", VARIANT_CASES)
def test_collapse_variants_gives_one_value_per_slot_from_the_slots_extruder_variant(volume_types, filament_map, expected_speeds, expected_variants):
    """04 section 6.1, the display form: one value per slot, taken from the variant (extruder type and nozzle flow) of
    the extruder the filament map sends the slot to."""
    flat = h2d(collapse_variants=True, nozzle_volume_type=volume_types, filament_map=filament_map)
    assert flat["filament_max_volumetric_speed"] == expected_speeds
    assert [v.strip('"') for v in flat["filament_extruder_variant"].split(";")] == expected_variants
    assert len(flat["nozzle_temperature"].split(",")) == 2  # per slot, not per variant


def test_compose_config_returns_the_uncollapsed_gui_form_by_default():
    """04 section 6.1: the variants of every filament stay in the vectors and filament_self_index says which entries
    belong to which slot; Print::apply collapses them once (a collapse here made it collapse twice)."""
    flat = h2d(nozzle_volume_type="Standard,High Flow", filament_map="1,2")
    variants = [v.strip('"') for v in flat["filament_extruder_variant"].split(";")]
    per_slot = len(variants) // 2
    assert per_slot > 1 and len(variants) == 2 * per_slot
    assert flat["filament_self_index"] == ",".join(str(slot) for slot in (1, 2) for _ in range(per_slot))
    assert len(flat["filament_max_volumetric_speed"].split(",")) == len(variants)
    # the display form is a different, shorter vector
    assert len(h2d(collapse_variants=True, nozzle_volume_type="Standard,High Flow", filament_map="1,2")["filament_max_volumetric_speed"].split(",")) == 2
    # and a config with one variant per filament is the same in both forms
    single = (cube_case.profiles()["machine"], cube_case.profiles()["process"], [cube_case.profiles()["filament"]])
    assert sc.compose_config(*single) == sc.compose_config(*single, collapse_variants=True)


def h2d_gcode(volume_types, filament_map):
    """Slices an H2D plate whose two filaments differ (slot 2 is slot 1 with other speeds and temperatures) with the
    filament map fixed (Manual: in the default mode, "Auto For Flush", Orca computes the map itself)."""
    printer = bbl_profiles.resolve("machine", "Bambu Lab H2D 0.4 nozzle")
    process = bbl_profiles.resolve("process", "0.20mm Standard @BBL H2D")
    first = bbl_profiles.resolve("filament", "Bambu PLA Basic @BBL H2D")
    second = {**first, "name": "Second", "filament_max_volumetric_speed": ["12", "30"], "nozzle_temperature": ["200", "210"]}
    project = {"flush_volumes_matrix": "0,100,100,0,0,100,100,0", "flush_multiplier": "1,1", "nozzle_volume_type": volume_types,
               "filament_map": filament_map, "filament_map_mode": "Manual"}
    flat = sc.normalize_config(sc.compose_config(printer, process, [first, second], project))["config"]
    j = sc.SliceJob()
    j.set_config(flat)
    j.add_object("left", *box(100, 100, size=15.0))
    j.add_object("right", *box(150, 100, size=15.0), extruder=2)
    return open(j.run().gcode_path, errors="replace").read()


# (nozzle_volume_type, filament_map, filament_max_volumetric_speed, nozzle_temperature) per slot: slot 1 is the PLA preset (Standard 25,
# High Flow 40; 220 C), slot 2 the modified copy (Standard 12, High Flow 30; 200 C and 210 C) on the extruder the map names.
E2E_CASES = [
    ("Standard,Standard", "1,2", "25,12", "220,200"),
    ("Standard,High Flow", "1,2", "25,30", "220,210"),
    ("Standard,High Flow", "2,1", "40,12", "220,200"),
]


@pytest.mark.parametrize("volume_types,filament_map,expected_speeds,expected_temps", E2E_CASES)
def test_a_sliced_h2d_plate_collapses_the_variants_once_per_slot(volume_types, filament_map, expected_speeds, expected_temps):
    """End to end (compose_config -> normalize_config -> set_config -> Print::apply): the G-code's CONFIG block holds one
    value per slot, from the variant of the slot's own filament on the extruder the map sends it to, so
    filament_max_volumetric_speed differs per slot. A config that was already collapsed (collapse_variants=True) and is
    then collapsed again by Print::apply, without filament_self_index, gave slot 2 the values of slot 1 for
    Standard,Standard ("25,25" and "220,220" for these filaments)."""
    text = h2d_gcode(volume_types, filament_map)
    assert block(text, "filament_max_volumetric_speed") == expected_speeds
    assert block(text, "nozzle_temperature") == expected_temps
    assert block(text, "filament_map") == filament_map
    assert block(text, "nozzle_volume_type") == volume_types
    assert block(text, "filament_extruder_variant").count(";") == 1  # one variant per slot after the collapse


def test_an_h2d_plate_slices_in_the_bambu_dialect_with_object_labels():
    flat = sc.normalize_config(h2d(nozzle_volume_type="Standard,High Flow", filament_map="1,2"))["config"]
    j = sc.SliceJob()
    j.set_config(flat)
    j.add_object("left", *box(100, 100, size=15.0))
    j.add_object("right", *box(150, 100, size=15.0), extruder=2)
    r = j.run()
    text = open(r.gcode_path, errors="replace").read()
    assert NATIVE._glue_state()["is_bbl_processor"] is True
    assert "; model label id: 1,2" in text, "the exclude-object label ids are the engine's index + 1"
    m = r.moves
    ext = m["type"] == sc.enums()["move_type"]["Extrude"]
    assert {0, 1} <= set(np.unique(m["object_id"][ext]).tolist())
    assert set(np.unique(m["nozzle"][ext]).tolist()) == {0, 1}
    # no engine warning about an unselected dialect any more
    assert not [w for w in r.warnings if w["opt_key"] == "printer_model"]
