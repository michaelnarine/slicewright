# SPDX-License-Identifier: GPL-3.0-or-later
"""Plate thumbnails (``core/thumbnails.py``) and how the runner feeds them to ``set_thumbnails``, without Blender."""
from __future__ import annotations

import fake_engine as sc
import numpy as np
import pytest
from slicewright.core import thumbnails as th
from slicewright.engine import jobs
from test_jobs import cube, drive, request

CUBE_V = np.array([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 10), (10, 0, 10), (10, 10, 10),
                   (0, 10, 10)], np.float32)
CUBE_T = np.array([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                   (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)], np.int32)


# -- the thumbnails key ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("48x48/PNG,300x300/PNG", [(48, 48, "PNG"), (300, 300, "PNG")]),
    ("48x48/PNG, 300x300/JPG", [(48, 48, "PNG"), (300, 300, "JPG")]),
    ("  32 x 32 / qoi ", [(32, 32, "QOI")]),
    ("32X24", [(32, 24, "")]),
    ("160x120/PNG;96x96/PNG", [(160, 120, "PNG"), (96, 96, "PNG")]),
    ("", []), (None, []), ("garbage", []), ("0x10/PNG", []), ("10x0", []), ("-4x4", []),
    ("5000x100/PNG,16x16/PNG", [(16, 16, "PNG")]),
    ("12x/PNG, x12, 20x20/PNG", [(20, 20, "PNG")]),
])
def test_parse_specs(text, expected):
    assert [tuple(s) for s in th.parse_specs(text)] == expected


def test_distinct_sizes_keep_first_occurrence_and_serve_every_format():
    specs = th.parse_specs("48x48/PNG,300x300/PNG,48x48/JPG,300x200/PNG")
    assert th.distinct_sizes(specs) == [(48, 48), (300, 300), (300, 200)]
    assert th.distinct_sizes([]) == []


@pytest.mark.parametrize("value, rgb", [
    ("#FF0080", (1.0, 0.0, 128 / 255)), (["#00ff00"], (0.0, 1.0, 0.0)), ("0000FF", (0.0, 0.0, 1.0)),
    ("", th.DEFAULT_COLOUR), ([], th.DEFAULT_COLOUR), (None, th.DEFAULT_COLOUR), ("red", th.DEFAULT_COLOUR)])
def test_parse_colour(value, rgb):
    assert th.parse_colour(value) == pytest.approx(rgb)


# -- framing -----------------------------------------------------------------------------------------------------

def clip(lo, hi, aspect):
    view, proj = th.iso_matrices(lo, hi, aspect)
    corners = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    return (proj @ view @ corners.T).T


@pytest.mark.parametrize("aspect", [1.0, 2.0, 0.5, 300 / 48])
@pytest.mark.parametrize("lo, hi", [((90, 90, 0), (110, 110, 20)), ((0, 0, 0), (250, 250, 250)),
                                    ((10, 20, 0), (11, 22, 0.2))])
def test_the_whole_plate_is_inside_the_image(lo, hi, aspect):
    c = clip(lo, hi, aspect)
    assert np.abs(c[:, :3]).max() <= 1.0 + 1e-9                  # x, y and depth all inside the clip volume
    assert np.abs(c[:, :2]).max() > 0.5                          # and it fills a good part of the image


def test_a_single_point_does_not_break_the_framing():
    c = clip((100, 100, 0), (100, 100, 0), 1.0)
    assert np.isfinite(c).all() and np.abs(c[:, :3]).max() <= 1.0 + 1e-9


def test_the_plate_is_centred_and_the_view_is_fixed():
    a = clip((90, 90, 0), (110, 110, 20), 1.0)
    shifted = clip((190, 40, 0), (210, 60, 20), 1.0)
    assert np.allclose(a, shifted, atol=1e-9)                    # same framing wherever the plate sits
    assert abs(a[:, 0].min() + a[:, 0].max()) < 1e-9 and abs(a[:, 1].min() + a[:, 1].max()) < 1e-9
    view, _ = th.iso_matrices((0, 0, 0), (50, 50, 50), 1.0)
    forward = -view[2, :3]                                       # the camera looks down its own -Z
    expect = -np.asarray(th.VIEW_DIRECTION) / np.linalg.norm(th.VIEW_DIRECTION)
    assert np.allclose(forward, expect)


def test_the_view_looks_at_the_plate_from_front_right_above():
    view, _ = th.iso_matrices((0, 0, 0), (100, 100, 100), 1.0)
    top = view @ np.array([50, 50, 100, 1.0])
    bottom = view @ np.array([50, 50, 0, 1.0])
    assert top[1] > bottom[1]                                    # up is up in the image
    right = view @ np.array([100, 50, 50, 1.0])
    left = view @ np.array([0, 50, 50, 1.0])
    assert right[0] > left[0]                                    # +X is to the right
    assert view[2, 3] < 0                                        # the plate is in front of the camera


def test_bounds():
    lo, hi = th.bounds([CUBE_V, CUBE_V + 5])
    assert lo.tolist() == [0, 0, 0] and hi.tolist() == [15, 15, 15]
    assert th.bounds([]) is None and th.bounds([np.zeros((0, 3))]) is None


# -- shading -----------------------------------------------------------------------------------------------------

def test_shaded_triangles_shapes_dtypes_and_colours():
    pos, col = th.shaded_triangles([(CUBE_V, CUBE_T, (1.0, 0.5, 0.0))])
    assert pos.shape == (36, 3) and col.shape == (36, 4)
    assert pos.dtype == np.float32 and col.dtype == np.float32
    assert np.all(col[:, 3] == 1.0) and np.all((col >= 0) & (col <= 1))
    assert np.allclose(col[:, 1], col[:, 0] * 0.5, atol=1e-6) and np.all(col[:, 2] == 0)   # the hue is kept
    assert np.array_equal(pos[:3], CUBE_V[CUBE_T[0]])


def test_faces_are_shaded_by_their_direction():
    _, col = th.shaded_triangles([(CUBE_V, CUBE_T, (1.0, 1.0, 1.0))])
    face = col[::3, 0]                                           # one value per triangle
    top, bottom = face[2], face[0]                               # triangles 2/3 are z = 10, 0/1 are z = 0
    assert top > bottom and bottom == pytest.approx(th.AMBIENT, abs=1e-6)
    assert len(np.unique(np.round(face, 4))) >= 3                # not one flat shade


def test_several_meshes_and_empty_or_degenerate_input():
    pos, col = th.shaded_triangles([(CUBE_V, CUBE_T, (1, 0, 0)), (CUBE_V + 20, CUBE_T, (0, 0, 1))])
    assert pos.shape == (72, 3) and col[0, 0] > 0 and col[36, 2] > 0 and col[36, 0] == 0
    empty = th.shaded_triangles([])
    assert empty[0].shape == (0, 3) and empty[1].shape == (0, 4)
    flat = th.shaded_triangles([(np.zeros((3, 3), np.float32), np.array([[0, 1, 2]], np.int32), (1, 1, 1))])
    assert np.isfinite(flat[1]).all()


# -- the images ----------------------------------------------------------------------------------------------------

def test_engine_image_puts_row_zero_at_the_top():
    h, w = 3, 4
    readback = np.zeros((h, w, 4), np.uint8)
    readback[0, :, 0] = 10                                       # OpenGL row 0 is the bottom of the picture
    readback[2, :, 0] = 30                                       # and the last row the top
    img = th.engine_image(readback.reshape(-1), w, h)
    assert img.shape == (h, w, 4) and img.dtype == np.uint8 and img.flags["C_CONTIGUOUS"]
    assert img[0, 0, 0] == 30 and img[2, 0, 0] == 10
    assert img.flags["OWNDATA"] or img.base is not readback


def test_engine_image_converts_floats_and_rejects_a_wrong_size():
    img = th.engine_image(np.full(2 * 2 * 4, 0.5, np.float32), 2, 2)
    assert img.dtype == np.uint8 and img[0, 0, 0] in (127, 128)
    with pytest.raises(ValueError):
        th.engine_image(np.zeros(10, np.uint8), 2, 2)


def test_make_images_renders_each_size_once_in_the_engine_layout():
    calls = []

    def render(w, h):
        calls.append((w, h))
        px = np.zeros((h, w, 4), np.uint8)
        px[0, :, 3] = 255                                        # the bottom row, in GPU order
        return px.reshape(-1)
    images = th.make_images([(48, 48), (300, 200)], render)
    assert calls == [(48, 48), (300, 200)]
    assert [i.shape for i in images] == [(48, 48, 4), (200, 300, 4)]
    assert all(i.dtype == np.uint8 for i in images)
    assert images[0][-1, 0, 3] == 255 and images[0][0, 0, 3] == 0       # bottom row ended up last


# -- the runner passes them to set_thumbnails --------------------------------------------------------------------

PNG_PRINTER = {"thumbnails": "48x48/PNG,300x300/PNG"}


def sizes_source(log):
    def source(sizes):
        log.append(list(sizes))
        return [np.full((h, w, 4), 7, np.uint8) for w, h in sizes]
    return source


def codes(status):
    return [i["code"] for i in status.issues]


def run(req):
    runner = jobs.JobRunner(sc)
    runner.start(req)
    return drive(runner)[-1]


def test_the_sizes_in_the_printer_config_are_rendered_and_passed(monkeypatch):
    got = []
    real = sc.SliceJob.set_thumbnails
    monkeypatch.setattr(sc.SliceJob, "set_thumbnails", lambda job, images: (got.append(images), real(job, images))[1])
    seen = []
    status = run(request(printer=PNG_PRINTER, thumbnail_source=sizes_source(seen)))
    assert status.state == jobs.DONE
    assert seen == [[(48, 48), (300, 300)]]
    assert [(a.shape, a.dtype) for a in got[0]] == [((48, 48, 4), np.uint8), ((300, 300, 4), np.uint8)]
    assert "thumbnail_missing" not in codes(status)               # the engine found each size it asked for


def test_one_image_per_distinct_size_whatever_the_formats(monkeypatch):
    seen = []
    status = run(request(printer={"thumbnails": "48x48/PNG,48x48/JPG,160x120/QOI"}, thumbnail_source=sizes_source(seen)))
    assert seen == [[(48, 48), (160, 120)]] and "thumbnail_missing" not in codes(status)


def test_no_thumbnails_requested_means_none_rendered_and_none_passed(monkeypatch):
    called = []
    monkeypatch.setattr(sc.SliceJob, "set_thumbnails", lambda job, images: called.append(images))
    seen = []
    for printer in ({}, {"thumbnails": ""}, {"thumbnails": "nonsense"}):
        status = run(request(printer=printer, thumbnail_source=sizes_source(seen)))
        assert status.state == jobs.DONE
    assert seen == [] and called == []


def test_no_source_and_no_thumbnails_passes_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(sc.SliceJob, "set_thumbnails", lambda job, images: called.append(images))
    status = run(request(printer=PNG_PRINTER))
    assert called == [] and codes(status).count("thumbnail_missing") == 2     # the engine reports both sizes missing


def test_explicit_thumbnails_on_the_request_are_passed_as_given(monkeypatch):
    got = []
    real = sc.SliceJob.set_thumbnails
    monkeypatch.setattr(sc.SliceJob, "set_thumbnails", lambda job, images: (got.append(images), real(job, images))[1])
    img = np.zeros((48, 48, 4), np.uint8)
    run(request(printer={"thumbnails": "48x48/PNG"}, thumbnails=[img], thumbnail_source=lambda s: pytest.fail("used")))
    assert got == [[img]]


def test_a_source_that_cannot_render_leaves_an_info_note_and_the_slice_goes_on(monkeypatch):
    called = []
    monkeypatch.setattr(sc.SliceJob, "set_thumbnails", lambda job, images: called.append(images))

    def skipped(sizes):
        raise th.Skipped("Thumbnails are not rendered in background mode (no GPU)")
    status = run(request(printer=PNG_PRINTER, thumbnail_source=skipped))
    assert status.state == jobs.DONE and called == []
    note = next(i for i in status.issues if i["code"] == "thumbnails_skipped")
    assert note["level"] == "info" and "background mode" in note["message"] and note["opt_key"] == "thumbnails"


def test_a_source_that_fails_is_a_warning_not_a_failed_slice(monkeypatch):
    called = []
    monkeypatch.setattr(sc.SliceJob, "set_thumbnails", lambda job, images: called.append(images))

    def boom(sizes):
        raise RuntimeError("no GPU context")
    status = run(request(printer=PNG_PRINTER, thumbnail_source=boom))
    assert status.state == jobs.DONE and called == []
    warn = next(i for i in status.issues if i["code"] == "thumbnail_render_failed")
    assert warn["level"] == "warning" and "no GPU context" in warn["message"]


def test_a_source_that_returns_nothing_passes_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(sc.SliceJob, "set_thumbnails", lambda job, images: called.append(images))
    assert run(request(printer=PNG_PRINTER, thumbnail_source=lambda sizes: [])).state == jobs.DONE
    assert run(request(printer=PNG_PRINTER, thumbnail_source=lambda sizes: None)).state == jobs.DONE
    assert called == []


def test_a_cache_hit_renders_nothing(tmp_path):
    from slicewright.engine.cache import ResultCache
    cache = ResultCache(str(tmp_path / "c"))
    runner = jobs.JobRunner(sc, cache)
    seen = []
    runner.start(request(printer=PNG_PRINTER, thumbnail_source=sizes_source(seen)))
    first = drive(runner)[-1]
    cache.store_now(first.key, first.result)
    assert seen == [[(48, 48), (300, 300)]]
    hit = runner.start(request(printer=PNG_PRINTER, thumbnail_source=sizes_source(seen)))
    assert hit.from_cache and len(seen) == 1                      # thumbnails are not part of the key, nor rendered


def test_thumbnails_do_not_change_the_slice_key():
    a = run(request(printer=PNG_PRINTER, thumbnail_source=sizes_source([])))
    b = run(request(printer=PNG_PRINTER))
    assert a.key == b.key
