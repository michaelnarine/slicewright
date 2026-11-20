# SPDX-License-Identifier: GPL-3.0-or-later
"""Export of real slices (03 section 8.1, 04 sections 4.1 and 5.1): the G-code is the engine's file byte for byte, the
``.gcode.3mf`` is a zip with the parts the firmware reads and the plate thumbnail, and thumbnails are where the doc says
they are: embedded in the G-code of a Klipper printer, only inside the ``.gcode.3mf`` of a Bambu one.

Presets come from the profile library shipped in the engine (the add-on's own index and resolver, no Blender), so the
runs use a real Bambu Lab A1 and a real Voron 2.4 (Klipper) the way a user's slice would."""
from __future__ import annotations

import base64
import hashlib
import os
import re
import struct
import tempfile
import zipfile

import numpy as np
import pytest
from real_helpers import cube, drive
from slicewright.core import export as core_export
from slicewright.core.profiles import models
from slicewright.core.profiles.index import load_or_build
from slicewright.core.profiles.resolve import Resolver
from slicewright.core.profiles.source import ProfileSource
from slicewright.engine import jobs
from slicewright.engine.jobs import JobRunner, SliceRequest

pytestmark = pytest.mark.real_engine

A1 = "Bambu Lab A1 0.4 nozzle"
VORON = "Voron 2.4 250 0.4 nozzle"
SIZES = [(48, 48), (300, 300)]


@pytest.fixture(scope="module")
def library(real_sc):
    """``(index, resolver)`` of the engine's shipped profiles."""
    source = ProfileSource(real_sc.profiles_archive())
    task = load_or_build(source, tempfile.mkdtemp(prefix="sw-index-"), str(real_sc.version()["orca_commit"]))
    try:
        while True:
            next(task)
    except StopIteration as stop:
        index = stop.value
    yield index, Resolver(index, source, real_sc.config_schema())
    source.close()


def presets(library, printer_name):
    """``(printer, process, [filament], bed centre)`` PresetDicts of the printer's default process and first material."""
    index, resolver = library
    entry = next(e for e in index.of_kind("machine", selectable_only=True) if e.name == printer_name)
    process = models.default_process(index, entry)
    filament = models.default_filaments(index, models.model_of(index, entry), entry)[0]

    def preset(kind, e):
        return {**resolver.resolve(kind, e.id).config, "name": e.name}
    printer = preset("machine", entry)
    area = [tuple(float(v) for v in p.split("x")) for p in printer["printable_area"]]
    centre = (sum(x for x, _ in area) / len(area), sum(y for _, y in area) / len(area))
    return printer, preset("process", process), [preset("filament", filament)], centre


def image(w, h, seed):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 255, (h, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    return a


def slice_plate(library, printer_name, *, thumbnails=True):
    printer, process, filaments, (cx, cy) = presets(library, printer_name)
    images = [image(w, h, w) for w, h in SIZES] if thumbnails else None
    runner = JobRunner(__import__("slicewright_engine"))
    runner.start(SliceRequest(printer, process, filaments, [cube("cube", cx=cx, cy=cy, size=20.0)], thumbnails=images))
    final = drive(runner, timeout=120)[-1]
    assert final.state == jobs.DONE, final
    return final.result, images


def png_size(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def write_3mf(result, tmp_path, name="plate.gcode.3mf", plate_name="Plate"):
    dest = str(tmp_path / name)
    core_export.atomic_write(dest, lambda tmp: result.write_gcode_3mf(tmp, {"plate_name": plate_name}))
    return dest


def write_gcode(result, tmp_path, name="plate.gcode"):
    dest = str(tmp_path / name)
    core_export.atomic_write(dest, result.write_gcode)
    return dest


# -- the G-code ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("printer", [A1, VORON])
def test_the_exported_gcode_is_the_engines_gcode_byte_for_byte(library, printer, tmp_path):
    result, _ = slice_plate(library, printer)
    dest = write_gcode(result, tmp_path)
    assert open(dest, "rb").read() == open(result.gcode_path, "rb").read()
    assert os.listdir(tmp_path) == ["plate.gcode"]
    assert result.output_filename("my.part").startswith("my.part") and result.output_filename("my.part").endswith(".gcode")


def test_a_klipper_printer_embeds_its_thumbnails_in_the_gcode(library, tmp_path):
    result, images = slice_plate(library, VORON)
    text = open(write_gcode(result, tmp_path)).read()
    blocks = re.findall(r"; thumbnail begin (\d+)x(\d+) (\d+)\n((?:; [^\n]*\n)+); thumbnail end", text)
    assert sorted((int(w), int(h)) for w, h, _, _ in blocks) == SIZES
    for w, h, length, body in blocks:
        encoded = "".join(line[2:] for line in body.splitlines())
        assert len(encoded) == int(length)                                                   # Orca's count is the base64's
        assert png_size(base64.b64decode(encoded)) == (int(w), int(h))


def test_a_bambu_printer_has_no_thumbnails_in_its_plain_gcode(library, tmp_path):
    """04 section 4.1: Orca skips the callback for Bambu printers; their thumbnails live in the .gcode.3mf."""
    result, _ = slice_plate(library, A1)
    assert "; thumbnail begin" not in open(write_gcode(result, tmp_path)).read()


# -- the .gcode.3mf ---------------------------------------------------------------------------------------------

def test_the_a1_3mf_holds_the_parts_the_firmware_reads_and_the_plate_thumbnail(library, tmp_path):
    result, images = slice_plate(library, A1)
    dest = write_3mf(result, tmp_path, plate_name="Benchy plate")
    assert os.listdir(tmp_path) == ["plate.gcode.3mf"]
    with zipfile.ZipFile(dest) as z:
        assert z.testzip() is None
        names = set(z.namelist())
        for part in ("Metadata/plate_1.gcode", "Metadata/plate_1.gcode.md5", "Metadata/slice_info.config",
                     "Metadata/model_settings.config", "Metadata/project_settings.config", "Metadata/plate_1.json",
                     "3D/3dmodel.model", "[Content_Types].xml", "_rels/.rels"):
            assert part in names, part
        # 04 section 5.1: the plate thumbnails are the only images
        assert {n for n in names if n.lower().endswith((".png", ".jpg"))} == {"Metadata/plate_1.png", "Metadata/plate_1_small.png"}
        gcode = z.read("Metadata/plate_1.gcode")
        assert gcode == open(result.gcode_path, "rb").read()                                 # the engine's G-code, whole
        assert z.read("Metadata/plate_1.gcode.md5").decode().strip().lower() == hashlib.md5(gcode).hexdigest()
        assert png_size(z.read("Metadata/plate_1.png")) == (300, 300)                       # the largest set_thumbnails image
        assert "Benchy plate" in z.read("Metadata/model_settings.config").decode()
        slice_info = z.read("Metadata/slice_info.config").decode()
        assert f'key="prediction" value="{int(result.stats["time_s"]["normal"])}"' in slice_info


def test_the_3mf_opens_with_orcas_own_reader_when_the_engine_offers_the_hook(real_sc, library, tmp_path):
    hook = getattr(real_sc._native, "_load_3mf_summary", None)
    if hook is None:
        pytest.skip("the private 3mf reader hook is not in this build")
    result, _ = slice_plate(library, A1)
    summary = hook(write_3mf(result, tmp_path, plate_name="Round trip"))
    assert summary["ok"] and summary["is_bbl_3mf"] and summary["plates"][0]["plate_name"] == "Round trip"


def test_a_slice_without_thumbnails_has_no_plate_image_in_its_3mf(library, tmp_path):
    """Background Blender renders none: the file is still valid, only without the cover image."""
    result, _ = slice_plate(library, A1, thumbnails=False)
    with zipfile.ZipFile(write_3mf(result, tmp_path)) as z:
        names = set(z.namelist())
    assert "Metadata/plate_1.gcode" in names and "Metadata/plate_1.png" not in names


def test_a_klipper_printer_can_export_a_3mf_too(library, tmp_path):
    result, _ = slice_plate(library, VORON)
    with zipfile.ZipFile(write_3mf(result, tmp_path)) as z:
        assert z.read("Metadata/plate_1.gcode") == open(result.gcode_path, "rb").read()
        assert png_size(z.read("Metadata/plate_1.png")) == (300, 300)


def test_a_failed_3mf_write_leaves_nothing_and_does_not_hurt_the_slice(real_sc, library, tmp_path):
    result, _ = slice_plate(library, A1)
    dest = tmp_path / "plate.gcode.3mf"
    dest.write_bytes(b"old")
    with pytest.raises(core_export.ExportError):
        core_export.atomic_write(str(tmp_path / "nope" / "x.gcode.3mf"), result.write_gcode_3mf)
    with pytest.raises(core_export.ExportError, match="already exists"):
        core_export.atomic_write(str(dest), result.write_gcode_3mf)
    assert dest.read_bytes() == b"old" and os.listdir(tmp_path) == ["plate.gcode.3mf"]
    assert zipfile.is_zipfile(write_3mf(result, tmp_path, name="again.gcode.3mf"))          # the result is still good


def test_the_3mf_is_busy_while_a_job_runs_and_atomic_write_removes_its_temp(real_sc, library, tmp_path):
    result, _ = slice_plate(library, A1)
    printer, process, filaments, (cx, cy) = presets(library, A1)
    other = JobRunner(real_sc)
    other.start(SliceRequest(printer, process, filaments, [cube("big", cx=cx, cy=cy, size=20.0, height=60.0)]))
    try:
        with pytest.raises(real_sc.Busy):
            core_export.atomic_write(str(tmp_path / "busy.gcode.3mf"), result.write_gcode_3mf)
        assert os.listdir(tmp_path) == []
    finally:
        other.cancel(discard=True)
        drive_until_stopped(other)
    write_3mf(result, tmp_path)


def drive_until_stopped(runner):
    import time
    deadline = time.monotonic() + 30
    while runner.live and time.monotonic() < deadline:
        runner.poll()
        time.sleep(0.005)
