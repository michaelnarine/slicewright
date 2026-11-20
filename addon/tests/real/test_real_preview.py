# SPDX-License-Identifier: GPL-3.0-or-later
"""The preview's data path (``core/preview_data``, ``preview_legend``, ``preview_palette``) fed by real results:
the packing keeps what the shader needs (ids that fit their bit fields, finite values, every role named)."""
from __future__ import annotations

import numpy as np
import pytest
from conftest import cube, drive, flush_project, request
from contract_helpers import FILAMENT
from slicewright.core import preview_data as pd
from slicewright.core import preview_legend as legend
from slicewright.core import preview_palette as pp

pytestmark = pytest.mark.real_engine


@pytest.fixture
def two_filament_result(runner):
    objs = [cube("a", cx=90.0), cube("b", cx=170.0)]
    fils = [dict(FILAMENT), {**FILAMENT, "name": "Second", "filament_colour": ["#FF0000"]}]
    runner.start(request(objects=objs, filaments=fils, project=flush_project(2)))
    final = drive(runner)[-1]
    assert final.state == "done", final
    return final.result


def test_the_ids_fit_the_bit_fields_the_shader_unpacks(real_sc, sliced):
    moves = sliced.result.moves
    assert int(moves["role"].max()) < 2 ** pd.ROLE_BITS and int(moves["type"].max()) < 2 ** pd.TYPE_BITS
    enums = real_sc.enums()
    assert max(enums["role"].values()) < 32 and max(enums["move_type"].values()) < 16
    assert {"Retract", "Seam"} <= set(pd.MARKER_TYPES) & set(enums["move_type"])      # the marker names exist


@pytest.mark.parametrize("which", ["cube", "two_filaments"])
def test_chunks_pack_and_unpack_to_the_moves(real_sc, sliced, two_filament_result, which):
    result = sliced.result if which == "cube" else two_filament_result
    moves, layers = result.moves, result.layers
    for b in pd.chunk_bounds(len(moves["type"])):
        pos, meta = pd.pack_positions(moves, b), pd.pack_meta(moves, b)
        assert np.isfinite(pos).all()
        un = pd.unpack_meta(meta[: b.n_texels])
        sel = slice(b.s, b.e + 1)
        assert np.array_equal(un["role"][1:], moves["role"][sel]) and np.array_equal(un["type"][1:], moves["type"][sel])
        assert np.array_equal(un["layer_id"][1:], moves["layer_id"][sel])
        assert np.array_equal(un["filament"][1:], moves["filament"][sel]) or moves["filament"].max() == 255
    for view in pd.VIEW_MODES:
        scalar = pd.view_scalar(moves, layers, view)
        if scalar is not None:
            assert scalar.shape == (len(moves["type"]),) and np.isfinite(scalar).all(), view


def test_the_legend_and_summary_read_the_real_stats(real_sc, two_filament_result):
    stats = two_filament_result.stats
    rows = legend.legend_rows(stats, dict(real_sc.enums()["role"]))
    assert rows and abs(sum(r.percent for r in rows) - 100.0) < 1e-6
    assert all(r.role_id < 32 for r in rows) and all(r.label for r in rows)
    lines = legend.summary_lines(stats)
    assert lines[0].startswith("Time: ") and any(l.startswith("Filament 2") for l in lines) and "Layers: " in " ".join(lines)
    assert abs(legend.accounted_time(stats) - stats["time_s"]["normal"]) / stats["time_s"]["normal"] < 0.01
    assert all(name in pp.ROLE_STYLE for name in stats["time_by_role_s"]), \
        [n for n in stats["time_by_role_s"] if n not in pp.ROLE_STYLE]       # every real role has a legend style


def test_the_palette_has_a_colour_for_every_real_role_and_type(real_sc):
    enums = real_sc.enums()
    pal = pp.palette_array(dict(enums["role"]), dict(enums["move_type"]))
    assert pal.dtype == np.float32 and np.isfinite(pal).all()
    assert [n for n in enums["role"] if n not in pp.ROLE_STYLE] == []      # no real role falls back to mid grey
