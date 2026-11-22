# SPDX-License-Identifier: GPL-3.0-or-later
"""The Prepare / Preview rules (03 section 1.1), without Blender."""
from slicewright.core import mode_rules as mr


def test_prepare_is_always_possible():
    assert mr.switch_problem(mr.PREPARE, False) is None
    assert mr.switch_problem(mr.PREPARE, True) is None


def test_preview_needs_a_held_result_and_says_what_to_do():
    assert mr.switch_problem(mr.PREVIEW, True) is None
    reason = mr.switch_problem(mr.PREVIEW, False)
    assert reason == mr.NO_RESULT_REASON and "Slice" in reason


def test_an_unknown_mode_is_refused():
    assert "Unknown mode" in mr.switch_problem("EDIT", True)


def test_a_file_saved_in_preview_stays_there_only_while_its_slice_comes_back():
    assert mr.mode_after_load(mr.PREVIEW, has_key=True, restoring=True) == mr.PREVIEW
    assert mr.mode_after_load(mr.PREVIEW, has_key=True, restoring=False) == mr.PREPARE     # cache entry gone
    assert mr.mode_after_load(mr.PREVIEW, has_key=False, restoring=False) == mr.PREPARE    # never sliced
    assert mr.mode_after_load(mr.PREPARE, has_key=True, restoring=True) == mr.PREPARE
