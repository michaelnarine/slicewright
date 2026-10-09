# SPDX-License-Identifier: GPL-3.0-or-later
"""core.units (03 section 1.4)."""
import pytest

from slicewright.core import units


def test_blender_defaults_are_a_metre_per_unit():
    assert units.mm_per_bu(1.0) == 1000.0


def test_millimetre_scene_is_one_mm_per_unit():
    assert units.mm_per_bu(units.MM_SCENE_SCALE) == pytest.approx(1.0)


@pytest.mark.parametrize("scale", [0.0, -1.0, float("nan")])
def test_non_positive_scale_is_rejected(scale):
    with pytest.raises(ValueError):
        units.mm_per_bu(scale)


def test_a_default_scene_gets_the_small_bed_notice():
    # 256 mm at 1000 mm/BU is 0.256 BU, under the 0.5 BU threshold.
    assert "only" in units.bed_scale_notice(256.0, 1000.0)


def test_a_millimetre_scene_needs_no_notice():
    assert units.bed_scale_notice(256.0, 1.0) is None


def test_a_huge_bed_gets_the_large_notice():
    assert "consider" in units.bed_scale_notice(2_000_000.0, 1000.0)


@pytest.mark.parametrize("bu, expect", [(0.49, True), (0.5, False), (1000.0, False), (1000.1, True)])
def test_notice_thresholds_are_exclusive_of_the_limits(bu, expect):
    assert (units.bed_scale_notice(bu * 10.0, 10.0) is not None) is expect
