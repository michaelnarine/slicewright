# SPDX-License-Identifier: GPL-3.0-or-later
"""The result cache (``engine/cache.py``, 03 section 6.4), thumbnails and warnings with REAL ``SliceResult`` objects:
dtypes, the layers dict, ``gcode_line_ends``, stats and warning dicts as the engine really produces them."""
from __future__ import annotations

import base64
import json
import re

import numpy as np
import pytest
from real_helpers import cube, drive, flush_project, request
from contract_helpers import FILAMENT
from slicewright.engine import jobs
from slicewright.engine.cache import ResultCache

pytestmark = pytest.mark.real_engine


@pytest.fixture
def cache(tmp_path):
    return ResultCache(str(tmp_path / "slices"), 512 * 1024 * 1024)


def test_a_real_result_round_trips_through_the_cache(sliced, cache):
    result, key = sliced.result, sliced.key
    entry = cache.store_now(key, result)
    assert entry is not None and cache.has(key) and entry.bytes > 0
    loaded = cache.lookup(key).load()
    assert loaded.from_cache and loaded.key == key
    assert set(loaded.moves) == set(result.moves) and set(loaded.layers) == set(result.layers)
    for name in result.moves:
        a, b = np.asarray(result.moves[name]), loaded.moves[name]
        assert a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b), name
        assert not b.flags.writeable
    for name in result.layers:
        assert np.array_equal(result.layers[name], loaded.layers[name]), name
    assert np.array_equal(result.gcode_line_ends, loaded.gcode_line_ends)
    assert loaded.gcode_line_ends.dtype == np.uint64
    assert loaded.stats == json.loads(json.dumps(result.stats))            # JSON round trip: tuples become lists
    assert list(loaded.warnings) == list(result.warnings) and loaded.objects == list(result.objects)
    assert loaded.wipe_tower == result.wipe_tower
    with open(result.gcode_path, "rb") as a, open(loaded.gcode_path, "rb") as b:
        assert a.read() == b.read()
    assert int(loaded.gcode_line_ends[-1]) == len(open(loaded.gcode_path, "rb").read())   # offsets index the cached file


def test_the_runner_serves_the_second_identical_slice_from_the_cache(real_sc, runner, cache):
    runner.cache = cache
    runner.start(request())
    first = drive(runner)[-1]
    cache.store_now(first.key, first.result)
    runner.start(request())                                                   # same inputs: no engine job
    assert not runner.live
    hit = runner.status
    assert hit.state == jobs.DONE and hit.from_cache and hit.key == first.key and hit.text == "Done (from cache)"
    runner.start(request(use_cache=False))                                    # but a forced slice really slices
    assert runner.live
    assert drive(runner)[-1].result is not None
    runner.start(request(process={"layer_height": "0.3"}))                    # another key misses
    assert runner.live
    drive(runner)


def test_a_cached_multi_filament_plate_keeps_filament_ids_and_the_wipe_tower(real_sc, runner, cache):
    objs = [cube("a", cx=90.0), cube("b", cx=170.0)]
    for o in objs:
        o.compute_key()
    fils = [dict(FILAMENT), {**FILAMENT, "name": "Second", "filament_colour": ["#FF0000"]}]
    runner.start(request(objects=objs, filaments=fils, project=flush_project(2)))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE, final
    result = final.result
    assert set(np.unique(result.moves["filament"])) >= {0}                    # 0-based, 255 where Orca has -1
    entry = cache.store_now(final.key, result)
    loaded = entry.load()
    assert np.array_equal(loaded.moves["filament"], result.moves["filament"])
    assert loaded.objects == ["a", "b"] and loaded.wipe_tower == result.wipe_tower


def _png_blocks(gcode_text: str) -> list[tuple[int, int]]:
    return [(int(m.group(2)), int(m.group(3))) for m in re.finditer(r"^; (thumbnail\w*) begin (\d+)x(\d+) ", gcode_text, re.M)]


def test_thumbnail_images_reach_the_gcode_through_set_thumbnails(runner):
    image = np.zeros((48, 64, 4), np.uint8)
    image[..., 0], image[..., 3] = 200, 255
    printer = {"thumbnails": "64x48/PNG"}
    runner.start(request(printer=printer, thumbnails=[image]))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE, final
    text = open(final.result.gcode_path, errors="replace").read()
    assert _png_blocks(text) == [(64, 48)]
    b64 = re.search(r"; thumbnail begin 64x48 \d+\n((?:; [^\n]*\n)+); thumbnail end", text).group(1)
    assert base64.b64decode("".join(l[2:] for l in b64.splitlines()))[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_thumbnail_source_is_asked_for_the_sizes_the_printer_wants(runner):
    asked = []

    def source(sizes):
        asked.append(list(sizes))
        return [np.full((h, w, 4), 90, np.uint8) for (w, h) in sizes]

    runner.start(request(printer={"thumbnails": "32x32/PNG, 64x48/PNG"}, thumbnail_source=source))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE, final
    assert asked and sorted(map(tuple, asked[0])) == [(32, 32), (64, 48)]
    assert sorted(_png_blocks(open(final.result.gcode_path, errors="replace").read())) == [(32, 32), (64, 48)]


def test_a_failing_thumbnail_source_is_a_warning_issue_not_a_failed_slice(runner):
    def source(sizes):
        raise RuntimeError("no GPU")

    runner.start(request(printer={"thumbnails": "32x32/PNG"}, thumbnail_source=source))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE
    assert [i["code"] for i in final.issues if i["opt_key"] == "thumbnails"] == ["thumbnail_render_failed"]


def test_real_warnings_are_issue_dicts_the_issues_list_can_show(runner):
    # a 0.1 mm wall of a tall thin pillar: Orca warns about thin / unprintable features on some inputs; whatever the
    # engine reports must be well-formed Issues (level, code, message) so that core.issues can merge them.
    runner.start(request(objects=[cube(size=1.0, height=30.0)], process={"wall_loops": "1"}))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE
    for issue in final.issues:
        assert issue["level"] in ("error", "warning", "info") and issue["code"] and isinstance(issue["message"], str)
