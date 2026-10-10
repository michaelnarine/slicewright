# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 9: set_thumbnails through Orca's own encoder and callback (04 section 4.1, A.2 #1)."""
import base64
import re
import struct
import zlib

import numpy as np
import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402
import bbl_profiles  # noqa: E402
from test_validating_errors import box  # noqa: E402


def image(w, h, seed=0):
    """RGBA (H, W, 4): the top row is red, the bottom row blue, the rest a gradient (so a vertical flip is visible)."""
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 255, size=(h, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    a[0, :, :3] = (255, 0, 0)
    a[-1, :, :3] = (0, 0, 255)
    return np.ascontiguousarray(a)


def config(thumbnails, **extra):
    p = cube_case.profiles()
    machine = {**p["machine"], "thumbnails": thumbnails, **extra}
    return sc.normalize_config(sc.compose_config(machine, p["process"], [p["filament"]]))["config"]


def job(thumbnails, images=None, **extra):
    j = sc.SliceJob()
    j.set_config(config(thumbnails, **extra))
    j.add_object("cube", *box(*cube_case.bed_centre(cube_case.profiles()["machine"])))
    if images is not None:
        j.set_thumbnails(images)
    return j


def blocks(text):
    """tag -> list of (w, h, bytes) of the thumbnail blocks in the G-code."""
    out = {}
    for m in re.finditer(r"^; (\w+) begin (\d+)x(\d+) (\d+)\n((?:; [^\n]*\n)+?); \1 end$", text, re.M):
        data = base64.b64decode("".join(line[2:] for line in m.group(5).splitlines()))
        out.setdefault(m.group(1), []).append((int(m.group(2)), int(m.group(3)), data))
    return out


def decode_png(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", 0
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and ctype == 6
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride, rows, prev = w * 4, [], np.zeros(w * 4, np.uint8)
    for y in range(h):
        f, line = raw[y * (stride + 1)], np.frombuffer(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)], np.uint8).astype(np.int32)
        cur = np.zeros(stride, np.int32)
        for i in range(stride):
            a = cur[i - 4] if i >= 4 else 0
            b, c = int(prev[i]), (int(prev[i - 4]) if i >= 4 else 0)
            if f == 0: v = line[i]
            elif f == 1: v = line[i] + a
            elif f == 2: v = line[i] + b
            elif f == 3: v = line[i] + (a + b) // 2
            else:
                p_ = a + b - c
                pa, pb, pc = abs(p_ - a), abs(p_ - b), abs(p_ - c)
                v = line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)
            cur[i] = v & 255
        rows.append(cur.astype(np.uint8))
        prev = rows[-1]
    return np.array(rows).reshape(h, w, 4)


def test_bad_images_are_rejected_synchronously():
    j = job("16x16/PNG")
    for bad, exc in ((np.zeros((16, 16, 3), np.uint8), ValueError), (np.zeros((16, 16, 4), np.float32), TypeError),
                     (np.zeros((16, 16), np.uint8), ValueError), (np.zeros((0, 16, 4), np.uint8), ValueError),
                     ([[0]], TypeError), (np.asfortranarray(np.zeros((8, 8, 4), np.uint8)), TypeError)):
        with pytest.raises(exc):
            j.set_thumbnails([bad])
    with pytest.raises(TypeError):
        j.set_thumbnails("not a list")
    j.set_thumbnails([])  # clears


def test_png_jpg_and_qoi_blocks_are_written_for_the_sizes_in_the_config():
    j = job("16x16/PNG,24x12/JPG,8x8/QOI", [image(16, 16), image(24, 12, 1), image(8, 8, 2), image(99, 99, 3)])
    assert not [i for i in j.validate() if i["code"] == "thumbnail_missing"]
    text = open(j.run().gcode_path, errors="replace").read()
    b = blocks(text)
    assert [(w, h) for w, h, _ in b["thumbnail"]] == [(16, 16)]
    assert [(w, h) for w, h, _ in b["thumbnail_JPG"]] == [(24, 12)]
    assert [(w, h) for w, h, _ in b["thumbnail_QOI"]] == [(8, 8)]
    assert b["thumbnail_JPG"][0][2][:2] == b"\xff\xd8" and b["thumbnail_QOI"][0][2][:4] == b"qoif"


def test_the_image_orientation_is_row_zero_at_the_top():
    img = image(16, 16)
    j = job("16x16/PNG", [img])
    png = blocks(open(j.run().gcode_path, errors="replace").read())["thumbnail"][0][2]
    decoded = decode_png(png)
    assert np.array_equal(decoded, img), "the PNG in the G-code must equal the image that was passed in"
    assert tuple(decoded[0, 0, :3]) == (255, 0, 0) and tuple(decoded[-1, 0, :3]) == (0, 0, 255)


def test_a_size_without_an_image_is_skipped_with_a_warning():
    j = job("16x16/PNG,32x32/PNG", [image(16, 16)])
    missing = [i for i in j.validate() if i["code"] == "thumbnail_missing"]
    assert len(missing) == 1 and missing[0]["level"] == "warning" and "32x32" in missing[0]["message"]
    r = j.run()
    assert [(w, h) for w, h, _ in blocks(open(r.gcode_path, errors="replace").read())["thumbnail"]] == [(16, 16)]
    assert any(w["code"] == "thumbnail_missing" for w in r.warnings)


def test_without_set_thumbnails_nothing_is_written_and_nothing_is_reported():
    j = job("16x16/PNG,32x32/PNG")
    text = open(j.run().gcode_path, errors="replace").read()
    assert "THUMBNAIL_BLOCK_START" not in text
    assert not [i for i in job("16x16/PNG").validate() if i["code"] == "thumbnail_missing"]


def test_an_invalid_thumbnails_value_is_a_config_error():
    # Orca rejects it while the config is built; the exception translator gives the typed ConfigError (test_errors.py).
    with pytest.raises(sc.ConfigError) as err:
        config("not-a-size")
    assert "thumbnails" in err.value.message


def test_set_thumbnails_invalidates_the_validate_cache():
    """The thumbnail checks (thumbnail_missing) are part of validate(), so a change of the images redoes it."""
    j = job("16x16/PNG,32x32/PNG")
    first = j.validate()
    assert j._validation_runs() == 1 and [i for i in first if i["code"] == "thumbnail_missing"] == []
    j.validate()
    assert j._validation_runs() == 1  # cached
    j.set_thumbnails([image(16, 16)])
    second = j.validate()
    assert j._validation_runs() == 2
    assert [i["opt_key"] for i in second if i["code"] == "thumbnail_missing"] == ["thumbnails"]


def test_set_thumbnails_after_start_is_a_state_error():
    j = job("16x16/PNG")
    j.start()
    with pytest.raises(sc.StateError):
        j.set_thumbnails([image(16, 16)])
    j.result()


def test_bambu_printers_get_no_thumbnails_in_plain_gcode():
    """04 section 4.1: Orca skips the callback for Bambu printers (GCode.cpp:2644-2659)."""
    printer = bbl_profiles.resolve("machine", "Bambu Lab A1 0.4 nozzle")
    process = bbl_profiles.resolve("process", "0.20mm Standard @BBL A1")
    filament = bbl_profiles.resolve("filament", "Bambu PLA Basic @BBL A1")
    flat = sc.normalize_config(sc.compose_config({**printer, "thumbnails": "16x16/PNG"}, process, [filament]))["config"]
    j = sc.SliceJob()
    j.set_config(flat)
    j.add_object("cube", *box(128.0, 128.0))
    j.set_thumbnails([image(16, 16)])
    assert not [i for i in j.validate() if i["code"] == "thumbnail_missing"]
    text = open(j.run().gcode_path, errors="replace").read()
    assert "THUMBNAIL_BLOCK_START" not in text
