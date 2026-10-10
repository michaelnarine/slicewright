# SPDX-License-Identifier: GPL-3.0-or-later
"""core.geometry hatching and core.bed (03 section 1.3)."""
import numpy as np
import pytest

from slicewright.core import bed as bed_mod
from slicewright.core.geometry import hatch_segments, parse_points, polygon_bounds

SQUARE = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], float)
L_SHAPE = np.array([[0, 0], [100, 0], [100, 40], [40, 40], [40, 100], [0, 100]], float)


def test_parse_points():
    assert parse_points("0x0,256x0, 256x256 ,0x256").tolist() == [[0, 0], [256, 0], [256, 256], [0, 256]]
    assert parse_points('"1.5x-2"').tolist() == [[1.5, -2.0]]
    assert parse_points("   ").shape == (0, 2)


@pytest.mark.parametrize("bad", ["1,2", "1x2x3", "axb"])
def test_parse_points_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_points(bad)


def test_horizontal_grid_of_a_square():
    seg = hatch_segments(SQUARE, 10.0, 0.0)
    ys = sorted({round(float(s[0][1]), 6) for s in seg})
    # The half-open crossing rule drops the line on the top edge; the outline draws it anyway.
    assert ys == [float(y) for y in range(0, 100, 10)]
    assert np.allclose(np.abs(seg[:, 1, 0] - seg[:, 0, 0]), 100.0)


def test_vertical_grid_is_anchored_to_the_origin_not_the_polygon():
    poly = SQUARE + [3.0, 3.0]            # 3..103: lines still at multiples of 10
    seg = hatch_segments(poly, 10.0, 90.0)
    xs = sorted({round(float(s[0][0]), 6) for s in seg})
    assert xs == [float(x) for x in range(10, 101, 10)]
    assert seg[:, :, 1].min() == pytest.approx(3.0) and seg[:, :, 1].max() == pytest.approx(103.0)


def test_concave_polygon_is_clipped_into_the_right_pieces():
    seg = hatch_segments(L_SHAPE, 10.0, 0.0)
    at_70 = [s for s in seg if abs(s[0][1] - 70.0) < 1e-9]
    assert len(at_70) == 1 and abs(at_70[0][1][0] - at_70[0][0][0]) == pytest.approx(40.0)
    at_20 = [s for s in seg if abs(s[0][1] - 20.0) < 1e-9]
    assert len(at_20) == 1 and abs(at_20[0][1][0] - at_20[0][0][0]) == pytest.approx(100.0)


def test_every_hatch_segment_lies_inside_the_polygon_bounds():
    for angle in (0, 45, 90, 135):
        seg = hatch_segments(L_SHAPE, 4.0, angle)
        assert len(seg)
        assert seg.min() >= -1e-6 and seg.max() <= 100 + 1e-6


def test_diagonal_hatch_segments_run_at_45_degrees():
    seg = hatch_segments(SQUARE, 8.0, 45.0)
    d = seg[:, 1] - seg[:, 0]
    assert np.allclose(np.abs(d[:, 0]), np.abs(d[:, 1]))


def test_degenerate_inputs_give_no_segments():
    assert len(hatch_segments(SQUARE[:2], 10.0)) == 0
    assert len(hatch_segments(SQUARE, 0.0)) == 0


def test_bounds():
    assert polygon_bounds(L_SHAPE) == (0.0, 0.0, 100.0, 100.0)


def test_parse_bed_defaults_and_exclude():
    b = bed_mod.parse_bed(bed_mod.DEFAULT_PRINTABLE_AREA, "0x0,18x0,18x28,0x28", 250)
    assert b.bounds == (0, 0, 256, 256) and b.center == (128, 128) and b.extent_mm == 256
    assert len(b.exclude) == 1 and b.height == 250.0
    assert bed_mod.parse_bed("0x0,10x0,10x10,0x10", "", 5).exclude == ()


def test_parse_bed_needs_a_polygon():
    with pytest.raises(ValueError):
        bed_mod.parse_bed("0x0,10x0", "", 5)


def test_build_geometry_shapes_and_content():
    b = bed_mod.parse_bed("0x0,100x0,100x100,0x100", "0x0,20x0,20x20,0x20", 50)
    g = bed_mod.build_geometry(b)
    assert set(g) == {"grid", "outline", "volume", "exclude_outline", "exclude_hatch",
                      "axis_x", "axis_y", "axis_z"}
    for name, pts in g.items():
        assert pts.dtype == np.float32 and pts.shape[1] == 3 and len(pts) % 2 == 0, name
    assert len(g["outline"]) == 8                      # 4 edges
    assert len(g["volume"]) == 8 + 8                   # top loop + 4 verticals
    assert g["volume"][:, 2].max() == 50.0
    assert len(g["exclude_hatch"]) > 0 and g["exclude_hatch"][:, :2].max() <= 20 + 1e-4
    assert (g["grid"][:, 2] == 0).all()


def test_no_exclude_area_means_no_exclude_geometry():
    g = bed_mod.build_geometry(bed_mod.parse_bed("0x0,50x0,50x50,0x50", "", 10))
    assert len(g["exclude_hatch"]) == 0 and len(g["exclude_outline"]) == 0
