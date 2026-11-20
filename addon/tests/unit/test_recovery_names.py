# SPDX-License-Identifier: GPL-3.0-or-later
"""File-name bookkeeping of the recovery copies (``core/recovery.py``), without Blender."""
from __future__ import annotations

import os

import pytest
from slicewright.core import recovery


def touch(directory, *names):
    for n in names:
        (directory / n).write_text("x")


def test_the_defaults_are_five_minutes_and_three_copies():
    assert (recovery.INTERVAL_S, recovery.KEEP) == (300.0, 3)


@pytest.mark.parametrize("given, stem", [
    ("/work/My Part.blend", "My Part"), ("", "untitled"), ("/a/b/.blend", "blend"),
    ("/a/c:d*e?.blend", "c_d_e_"), ("/a/" + "x" * 80 + ".blend", "x" * recovery.MAX_STEM),
    ("part.v2.blend", "part.v2"), ("../../etc/passwd", "passwd")])
def test_safe_stem(given, stem):
    assert recovery.safe_stem(given) == stem


def test_copies_are_numbered_across_the_directory_and_listed_oldest_first(tmp_path):
    assert recovery.next_path(str(tmp_path), "/w/a.blend") == str(tmp_path / "a-1.blend")
    touch(tmp_path, "a-1.blend", "b-2.blend", "a-10.blend", "a-9.blend")
    assert [n for n, _ in recovery.list_copies(str(tmp_path))] == [1, 2, 9, 10]    # numeric, not lexical
    assert recovery.next_path(str(tmp_path), "/w/c.blend") == str(tmp_path / "c-11.blend")


def test_prune_keeps_the_newest_and_only_touches_recovery_copies(tmp_path):
    touch(tmp_path, *(f"a-{i}.blend" for i in range(1, 7)), "notes.txt", "a-3.blend1", "b.blend", "plain-x.blend")
    removed = recovery.prune(str(tmp_path), keep=3)
    assert sorted(os.path.basename(p) for p in removed) == ["a-1.blend", "a-2.blend", "a-3.blend"]
    assert sorted(os.listdir(tmp_path)) == ["a-3.blend1", "a-4.blend", "a-5.blend", "a-6.blend", "b.blend",
                                            "notes.txt", "plain-x.blend"]
    assert recovery.prune(str(tmp_path), keep=3) == []


def test_a_missing_directory_has_no_copies(tmp_path):
    assert recovery.list_copies(str(tmp_path / "nope")) == [] and recovery.prune(str(tmp_path / "nope")) == []


def test_prune_skips_a_copy_it_cannot_remove(tmp_path, monkeypatch):
    touch(tmp_path, *(f"a-{i}.blend" for i in range(1, 6)))
    real = os.remove

    def fussy(path):
        if path.endswith("a-1.blend"):
            raise PermissionError("held open")
        real(path)
    monkeypatch.setattr(os, "remove", fussy)
    removed = recovery.prune(str(tmp_path), keep=3)
    assert [os.path.basename(p) for p in removed] == ["a-2.blend"]
