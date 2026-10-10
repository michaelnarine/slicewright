# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 10: write_gcode_3mf with the PlateData port (02 section 5.9, engine/src/glue/plate_glue.cpp).

The file is opened with Orca's own reader (load_bbs_3mf, through the private _load_3mf_summary hook) and the fields
the Bambu firmware reads are compared with the slice."""
import hashlib
import re
import zipfile

import numpy as np
import pytest

sc = pytest.importorskip("slicewright_engine")
import bbl_profiles  # noqa: E402
import cube_case  # noqa: E402
from test_thumbnails import decode_png, image  # noqa: E402
from test_validating_errors import box  # noqa: E402

NATIVE = sc._native


def a1(thumbnails=None, name="Plate A"):
    printer = bbl_profiles.resolve("machine", "Bambu Lab A1 0.4 nozzle")
    process = bbl_profiles.resolve("process", "0.20mm Standard @BBL A1")
    filament = bbl_profiles.resolve("filament", "Bambu PLA Basic @BBL A1")
    flat = sc.normalize_config(sc.compose_config({**printer, "thumbnails": "16x16/PNG"}, process, [filament]))["config"]
    j = sc.SliceJob()
    j.set_config(flat)
    j.add_object("cube", *box(128.0, 128.0))
    if thumbnails is not None:
        j.set_thumbnails(thumbnails)
    return j.run()


def config_xml(z, name):
    return z.read(name).decode()


def meta(xml, key):
    m = re.search(rf'<metadata key="{key}" value="([^"]*)"', xml)
    return m.group(1) if m else None


@pytest.fixture(scope="module")
def a1_file(tmp_path_factory):
    big = image(64, 64, 7)
    r = a1([image(16, 16), big])
    path = tmp_path_factory.mktemp("3mf") / "plate.gcode.3mf"
    r.write_gcode_3mf(str(path), {"plate_name": "Plate A", "ignored": 1})
    return r, path, big


def test_the_archive_has_the_parts_the_firmware_reads(a1_file):
    _, path, _ = a1_file
    names = set(zipfile.ZipFile(path).namelist())
    for part in ("Metadata/plate_1.gcode", "Metadata/plate_1.gcode.md5", "Metadata/slice_info.config",
                 "Metadata/model_settings.config", "Metadata/project_settings.config", "Metadata/plate_1.png",
                 "Metadata/plate_1.json", "3D/3dmodel.model", "[Content_Types].xml", "_rels/.rels"):
        assert part in names, part
    # 04 section 5.1: the plate thumbnails are the only images (the GL-rendered no_light/top/pick ones are not produced)
    assert {n for n in names if n.lower().endswith((".png", ".jpg", ".jpeg"))} == {"Metadata/plate_1.png", "Metadata/plate_1_small.png"}


def test_the_gcode_part_is_the_engines_gcode_and_the_md5_matches(a1_file):
    r, path, _ = a1_file
    z = zipfile.ZipFile(path)
    gcode = z.read("Metadata/plate_1.gcode")
    assert gcode == open(r.gcode_path, "rb").read()
    assert z.read("Metadata/plate_1.gcode.md5").decode().strip().lower() == hashlib.md5(gcode).hexdigest()


def test_the_plate_thumbnail_is_the_largest_image(a1_file):
    _, path, big = a1_file
    decoded = decode_png(zipfile.ZipFile(path).read("Metadata/plate_1.png"))
    assert np.array_equal(decoded, big)


def test_slice_info_carries_the_plate_data_the_firmware_reads(a1_file):
    r, path, _ = a1_file
    xml = config_xml(zipfile.ZipFile(path), "Metadata/slice_info.config")
    s = r.stats
    assert meta(xml, "index") == "1"
    assert meta(xml, "prediction") == str(int(s["time_s"]["normal"]))                  # gcode_prediction
    assert abs(float(meta(xml, "weight")) - sum(f["g"] for f in s["filament_per_extruder"])) < 0.006   # gcode_weight
    assert meta(xml, "printer_model_id") == "N2S"                                      # Bambu Lab A1
    assert meta(xml, "nozzle_diameters") == "0.4"
    assert meta(xml, "label_object_enabled") == "true"                                 # is_label_object_enabled
    assert meta(xml, "support_used") == "false" and meta(xml, "outside") == "false"
    assert meta(xml, "filament_maps") == "1"
    assert float(meta(xml, "first_layer_time")) > 0
    assert 'identify_id="1" name="cube" skipped="false"' in xml
    f = re.search(r'<filament id="1"[^>]*type="PLA"[^>]*color="(#[0-9A-Fa-f]{6})"[^>]*used_m="([\d.]+)" used_g="([\d.]+)"', xml)
    assert f, xml                                                                      # parse_filament_info
    assert abs(float(f.group(3)) - s["filament_per_extruder"][0]["g"]) < 0.006
    assert abs(float(f.group(2)) - s["filament_per_extruder"][0]["mm"] / 1000.0) < 0.006
    assert re.search(r'<layer_filament_list filament_list="0" layer_ranges="0 \d+"', xml)  # layer_filaments
    assert '<nozzle id="0"' in xml                                                      # nozzle info


def test_the_plate_name_reaches_model_settings(a1_file):
    _, path, _ = a1_file
    assert "Plate A" in config_xml(zipfile.ZipFile(path), "Metadata/model_settings.config")


def test_orcas_own_reader_opens_the_file(a1_file):
    """Round trip: load_bbs_3mf (the code Orca and Bambu Studio open a .gcode.3mf with) reads our file."""
    r, path, _ = a1_file
    summary = NATIVE._load_3mf_summary(str(path))
    assert summary["ok"] and summary["is_bbl_3mf"]
    assert summary["object_names"] == ["cube"] and summary["config_keys"] > 500
    p = summary["plates"][0]
    assert p["plate_name"] == "Plate A"
    assert p["gcode_prediction"] == str(int(r.stats["time_s"]["normal"]))
    assert p["gcode_weight"] == "%.2f" % sum(f["g"] for f in r.stats["filament_per_extruder"])
    assert p["printer_model_id"] == "N2S" and p["nozzle_diameters"] == "0.4"
    assert p["is_label_object_enabled"] is True and p["filament_maps"] == [1]
    assert [(f["id"], f["type"]) for f in p["filaments"]] == [(0, "PLA")]


def test_a_multi_nozzle_multi_filament_plate_round_trips(tmp_path):
    printer = bbl_profiles.resolve("machine", "Bambu Lab H2D 0.4 nozzle")
    process = bbl_profiles.resolve("process", "0.20mm Standard @BBL H2D")
    filament = bbl_profiles.resolve("filament", "Bambu PLA Basic @BBL H2D")
    flat = sc.normalize_config(sc.compose_config(printer, process, [filament, filament], {
        "flush_volumes_matrix": "0,100,100,0,0,100,100,0", "flush_multiplier": "1,1", "filament_map": "1,2",
        "nozzle_volume_type": "Standard,High Flow"}))["config"]
    j = sc.SliceJob()
    j.set_config(flat)
    j.add_object("left", *box(100, 100, size=15.0))
    j.add_object("right", *box(150, 100, size=15.0), extruder=2)
    r = j.run()
    path = tmp_path / "h2d.gcode.3mf"
    r.write_gcode_3mf(str(path))
    z = zipfile.ZipFile(path)
    xml = config_xml(z, "Metadata/slice_info.config")
    # the writer separates the map with spaces; the automatic mapping decides which filament goes to which nozzle,
    # and the file must say what the G-code did (moves.nozzle)
    maps = [int(v) for v in meta(xml, "filament_maps").split()]
    assert sorted(maps) == [1, 2] and meta(xml, "nozzle_diameters") == "0.4,0.4"
    m = r.moves
    ext = m["type"] == sc.enums()["move_type"]["Extrude"]
    for f in (0, 1):
        assert set(np.unique(m["nozzle"][ext & (m["filament"] == f)]).tolist()) == {maps[f] - 1}
    assert xml.count("<filament id=") == 2 and xml.count("<nozzle id=") == 2
    assert 'volume_type="Standard"' in xml and 'volume_type="High Flow"' in xml
    assert "<layer_filament_list" in xml and meta(xml, "limit_filament_maps") is not None
    summary = NATIVE._load_3mf_summary(str(path))
    assert summary["ok"] and summary["object_names"] == ["left", "right"]
    assert summary["plates"][0]["filament_maps"] == maps and len(summary["plates"][0]["filaments"]) == 2


def test_a_plain_printer_slice_writes_a_3mf_too(tmp_path):
    r = cube_case.build_job(sc).run()
    path = tmp_path / "cube.gcode.3mf"
    r.write_gcode_3mf(str(path))
    assert NATIVE._load_3mf_summary(str(path))["ok"]
    names = zipfile.ZipFile(path).namelist()
    assert "Metadata/plate_1.gcode" in names and "Metadata/plate_1.png" not in names  # no image was given
    # the dialect flag is restored for whatever runs next
    assert NATIVE._glue_state()["is_bbl_processor"] is False


def test_plate_meta_is_validated(tmp_path):
    r = cube_case.build_job(sc).run()
    with pytest.raises(TypeError):
        r.write_gcode_3mf(str(tmp_path / "x.3mf"), "name")
    with pytest.raises(TypeError):
        r.write_gcode_3mf(str(tmp_path / "x.3mf"), {"plate_name": 3})
    r.write_gcode_3mf(str(tmp_path / "ok.3mf"), None)


def test_an_unwritable_path_is_an_engine_error_and_frees_the_engine_lock(tmp_path):
    r = cube_case.build_job(sc).run()
    with pytest.raises(sc.EngineError):
        r.write_gcode_3mf(str(tmp_path / "no" / "such" / "dir" / "x.3mf"))
    follow_up = cube_case.build_job(sc)
    follow_up.start()  # the writer's guard released the lock on the error path: no Busy
    follow_up.result()
    r.write_gcode_3mf(str(tmp_path / "ok.3mf"))


def test_a_failing_plate_capture_keeps_the_slice_and_only_loses_the_3mf(tmp_path):
    """The data for the .gcode.3mf is a by-product of the job: if capturing it fails, the G-code, moves and stats are
    still delivered, a warning says what is missing and write_gcode_3mf raises EngineError."""
    NATIVE._fail_plate_snapshots(True)
    try:
        r = cube_case.build_job(sc).run()
    finally:
        NATIVE._fail_plate_snapshots(False)
    assert cube_case.diff_against_reference(open(r.gcode_path, errors="replace").read()) == []
    assert r.stats["layer_count"] > 0
    warned = [w for w in r.warnings if w["code"] == "engine" and ".gcode.3mf" in w["message"]]
    assert len(warned) == 1 and warned[0]["level"] == "warning"
    with pytest.raises(sc.EngineError) as err:
        r.write_gcode_3mf(str(tmp_path / "x.3mf"))
    assert err.value.detail == "no plate"
    # and the next job captures again
    again = cube_case.build_job(sc).run()
    assert not [w for w in again.warnings if ".gcode.3mf" in w["message"]]
    again.write_gcode_3mf(str(tmp_path / "y.3mf"))


def test_the_writer_takes_the_engine_lock(tmp_path):
    r = cube_case.build_job(sc).run()
    running = cube_case.build_job(sc)
    running.start()
    with pytest.raises(sc.Busy):
        r.write_gcode_3mf(str(tmp_path / "x.3mf"))
    running.cancel()
    try:
        running.result()
    except sc.Cancelled:
        pass
    r.write_gcode_3mf(str(tmp_path / "x.3mf"))


def test_the_result_writes_after_its_job_is_gone(tmp_path):
    import gc

    j = cube_case.build_job(sc)
    r = j.run()
    del j
    gc.collect()
    r.write_gcode_3mf(str(tmp_path / "late.3mf"))
    assert NATIVE._load_3mf_summary(str(tmp_path / "late.3mf"))["ok"]
