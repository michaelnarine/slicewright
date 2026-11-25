# SPDX-License-Identifier: GPL-3.0-or-later
"""core.frame: world (0, 0) is the bed centre; the engine keeps the printer's frame (03 section 1.3)."""
import numpy as np
import pytest

from slicewright.core import arrange, bed as bed_mod, checks, frame
from slicewright.core.bed import parse_bed
from slicewright.core.transform import bed_transform_to_world

CORNER = "0x0,256x0,256x256,0x256"
CENTRED = "-110x-110,110x-110,110x110,-110x110"
NON_SQUARE = "0x0,300x0,300x200,0x200"
OFFSET_BED = "10x20,110x20,110x220,10x220"


@pytest.mark.parametrize("area, expected", [
    (CORNER, (128, 128)), (CENTRED, (0, 0)), (NON_SQUARE, (150, 100)), (OFFSET_BED, (60, 120))])
def test_bed_offset_is_the_bounding_box_centre(area, expected):
    assert frame.bed_offset(area).tolist() == [*expected, 0.0]
    assert frame.bed_offset(parse_bed(area, "", 100)).tolist() == [*expected, 0.0]


def test_unusable_area_gives_no_offset():
    assert frame.bed_offset("0x0,1x1").tolist() == [0.0, 0.0, 0.0]


def test_a_circular_bed_centre_is_its_bounds_centre():
    t = np.linspace(0, 2 * np.pi, 36, endpoint=False)
    poly = np.column_stack([100 + 90 * np.cos(t), 100 + 90 * np.sin(t)])
    assert frame.bed_offset(poly)[:2] == pytest.approx([100.0, 100.0], abs=1e-9)


def test_world_bed_round_trip_keeps_dtype():
    o = frame.bed_offset(CORNER)
    p32 = np.array([[0, 0, 5], [10, -3, 0]], np.float32)
    b = frame.world_to_bed(p32, o)
    assert b.dtype == np.float32 and b.tolist() == [[128, 128, 5], [138, 125, 0]]
    assert np.array_equal(frame.bed_to_world(b, o), p32)
    xy = frame.world_to_bed(np.array([[1.0, 2.0]]), o)       # 2D points shift in XY only
    assert xy.tolist() == [[129.0, 130.0]]


def test_object_at_world_origin_lands_at_the_bed_centre():
    co = np.array([[-5, -5, 0], [5, 5, 10]], np.float32)       # a cube-ish box about the object origin
    v = frame.to_bed_mm(co, np.eye(4), 1.0, frame.bed_offset(CORNER))
    assert v.dtype == np.float32
    assert v[:, :2].mean(axis=0).tolist() == [128.0, 128.0] and v[:, 2].tolist() == [0.0, 10.0]
    # millimetre scale: 1 BU = 0.001 m -> mm_per_bu = 1; metre scene: 1 BU = 1000 mm
    m = np.eye(4)
    m[:3, 3] = (0.002, 0.0, 0.0)
    assert frame.to_bed_mm(np.zeros((1, 3)), m, 1000.0, frame.bed_offset(CORNER))[0].tolist() == [130.0, 128.0, 0.0]


def test_centred_bed_changes_nothing():
    co = np.array([[1, 2, 3]], np.float32)
    assert frame.to_bed_mm(co, np.eye(4), 1.0, frame.bed_offset(CENTRED)).tolist() == [[1, 2, 3]]


def test_placement_comes_back_shifted_into_world_frame():
    o = frame.bed_offset(CORNER)
    # Engine puts a box centred at world (0, 0) -> bed (128, 128) at bed (50, 60): translate by (-78, -68).
    t = np.eye(4)
    t[:3, 3] = (-78.0, -68.0, 0.0)
    m_old = np.eye(4)
    new = bed_transform_to_world(t, m_old, 1.0, o)
    assert new[:3, 3].tolist() == [-78.0, -68.0, 0.0]          # a pure translation is frame independent
    # A rotation about the bed origin is not: about the world origin it must act on the bed-frame position.
    rot = np.eye(4)
    rot[:2, :2] = [[0, -1], [1, 0]]                              # 90 degrees about the G-code origin
    m_old[:3, 3] = (10.0, 0.0, 0.0)                              # bed position (138, 128)
    new = bed_transform_to_world(rot, m_old, 1.0, o)
    expected_bed = rot[:3, :3] @ np.array([138.0, 128.0, 0.0])    # (-128, 138)
    assert (new[:3, 3] + o).tolist() == pytest.approx(expected_bed.tolist())
    assert bed_transform_to_world(rot, m_old, 1.0, None)[:3, 3].tolist() == pytest.approx([0.0, 10.0, 0.0])


def test_placement_matrices_use_the_offset_and_scale():
    o = frame.bed_offset(CORNER)
    t = np.eye(4)
    t[:3, 3] = (30.0, 0.0, 0.0)
    out = arrange.placement_matrices([{"index": 3, "transform": t}], {3: np.eye(4)}, 1000.0, o)
    assert out[3][:3, 3].tolist() == pytest.approx([0.03, 0.0, 0.0])
    assert arrange.placement_matrices([{"index": 3, "transform": t}], {3: np.eye(4)}, 1000.0)[3][0, 3] == pytest.approx(0.03)


@pytest.mark.parametrize("area", [CORNER, CENTRED, NON_SQUARE, OFFSET_BED])
def test_bed_geometry_is_shifted_so_the_plate_is_centred_on_world_zero(area):
    bed = parse_bed(area, "", 100)
    g = bed_mod.build_geometry(bed)
    outline = g["outline"].astype(np.float64)
    assert (outline[:, :2].min(axis=0) + outline[:, :2].max(axis=0)) == pytest.approx([0.0, 0.0], abs=1e-4)
    raw = bed_mod.build_geometry(bed, offset=(0, 0))
    assert np.allclose(raw["outline"][:, :2] - g["outline"][:, :2], frame.bed_offset(bed)[:2], atol=1e-4)


def test_grid_stays_on_the_same_physical_lines():
    bed = parse_bed(OFFSET_BED, "", 100)            # centre (60, 120)
    o = frame.bed_offset(bed)
    g = bed_mod.build_geometry(bed)["grid"].astype(np.float64)
    bed_xy = g[:, :2] + o[:2]
    ys = bed_xy[np.isclose(g[:, 1][:, None], g[:, 1][:, None])[:, 0]][:, 1]
    # every horizontal line sits at a multiple of 10 mm in the engine/G-code frame
    horizontal = bed_xy[::2][np.isclose(bed_xy[::2, 1], bed_xy[1::2, 1])]
    assert len(horizontal) and np.allclose(horizontal[:, 1] % 10.0, 0.0, atol=1e-3)
    vertical = bed_xy[::2][np.isclose(bed_xy[::2, 0], bed_xy[1::2, 0])]
    assert len(vertical) and np.allclose(vertical[:, 0] % 10.0, 0.0, atol=1e-3)
    assert ys.size


def test_axes_and_exclusion_area_move_with_the_frame():
    bed = parse_bed(CORNER, "0x0,30x0,30x30,0x30", 100)
    g = bed_mod.build_geometry(bed)
    assert g["axis_x"][0].tolist() == [-128.0, -128.0, 0.0]            # the G-code origin, in world coordinates
    assert g["exclude_outline"][:, :2].min(axis=0).tolist() == [-128.0, -128.0]
    assert g["exclude_hatch"][:, :2].max() <= -98.0 + 1e-4
    assert g["volume"][:, 2].max() == 100.0                              # Z is never shifted


def test_centred_bed_geometry_is_unchanged():
    bed = parse_bed(CENTRED, "", 100)
    a, b = bed_mod.build_geometry(bed), bed_mod.build_geometry(bed, offset=(0, 0))
    assert all(np.array_equal(a[k], b[k]) for k in a)


@pytest.mark.parametrize("area", [CORNER, CENTRED])
def test_printable_area_check_runs_in_the_bed_frame(area):
    """An object is tested after the offset is added, so the same world placement passes on both bed kinds."""
    bed = parse_bed(area, "", 100)
    o = frame.bed_offset(bed)
    world = np.array([[x, y, z] for x in (-20, 20) for y in (-20, 20) for z in (0, 10)], np.float32)
    assert checks.out_of_volume(frame.world_to_bed(world, o), bed) == []
    far = world + [300.0, 0.0, 0.0]
    assert checks.OUTSIDE_BED in checks.out_of_volume(frame.world_to_bed(far, o), bed)
    # a world-frame test (the old assumption) would wrongly reject a centred-at-zero object on the corner bed
    if o[0]:
        assert checks.OUTSIDE_BED in checks.out_of_volume(world, bed)


def test_exclusion_area_check_uses_the_bed_frame():
    bed = parse_bed(CORNER, "0x0,30x0,30x30,0x30", 100)        # front-left corner of the plate
    o = frame.bed_offset(bed)
    at_corner_world = np.array([[x, y, z] for x in (-120, -110) for y in (-120, -110) for z in (0, 5)], np.float32)
    assert checks.IN_EXCLUDE in checks.out_of_volume(frame.world_to_bed(at_corner_world, o), bed)
    at_centre = at_corner_world + [118.0, 118.0, 0.0]
    assert checks.out_of_volume(frame.world_to_bed(at_centre, o), bed) == []
