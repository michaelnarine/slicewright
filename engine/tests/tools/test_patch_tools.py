# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for engine/tools/apply_patches.py and lint_patch_headers.py (no Orca needed)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import patchhdr  # noqa: E402

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}

GOOD_HEADER = """From: A Dev <dev@example.com>
Date: Fri, 9 Oct 2026 12:00:00 -0400
Subject: [PATCH] touch greeting

Purpose: Change the greeting.
SPDX-License-Identifier: AGPL-3.0-only
Upstream-Status: Pending
Orca-Base: v0.0.1
---
"""

DIFF = """diff --git a/src/hello.txt b/src/hello.txt
--- a/src/hello.txt
+++ b/src/hello.txt
@@ -1 +1 @@
-hello
+hello, world
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


def tool(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOLS / name), *args], env=ENV, capture_output=True, text=True)


@pytest.fixture()
def tree(tmp_path: Path):
    """A fake 'Orca' repo, a pin file and an empty patch directory."""
    src = tmp_path / "orca"
    (src / "src").mkdir(parents=True)
    (src / "src" / "hello.txt").write_text("hello\n")
    git(src, "init", "-q")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "base")
    commit = git(src, "rev-parse", "HEAD")
    pin = tmp_path / "ORCA_PIN"
    pin.write_text(f"tag=v0.0.1\ncommit={commit}\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    return src, pin, patches, tmp_path / "work"


def apply(src, pin, patches, work, *extra):
    return tool("apply_patches.py", "--source", str(src), "--pin", str(pin), "--patches", str(patches), "--work", str(work), *extra)


def lint(pin, patches):
    return tool("lint_patch_headers.py", "--pin", str(pin), "--patches", str(patches))


def test_apply_and_idempotent(tree):
    src, pin, patches, work = tree
    (patches / "0001-greeting.patch").write_text(GOOD_HEADER + DIFF)
    r = apply(src, pin, patches, work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (work / "src" / "hello.txt").read_text() == "hello, world\n"
    assert (src / "src" / "hello.txt").read_text() == "hello\n"  # source untouched
    again = apply(src, pin, patches, work)
    assert "up to date" in again.stdout
    assert apply(src, pin, patches, work, "--force").returncode == 0


def test_check_mode_leaves_nothing(tree):
    src, pin, patches, work = tree
    (patches / "0001-greeting.patch").write_text(GOOD_HEADER + DIFF)
    assert apply(src, pin, patches, work, "--check").returncode == 0
    assert not work.exists()


def test_rejected_hunk_fails(tree):
    src, pin, patches, work = tree
    (patches / "0001-greeting.patch").write_text(GOOD_HEADER + DIFF.replace("-hello\n", "-goodbye\n"))
    r = apply(src, pin, patches, work)
    assert r.returncode == 1
    assert "0001-greeting.patch does not apply" in r.stderr


def test_noop_is_skipped(tree):
    src, pin, patches, work = tree
    (patches / "0000-example-noop.patch").write_text(GOOD_HEADER + "Noop: true\n")
    r = apply(src, pin, patches, work)
    assert r.returncode == 0, r.stderr
    assert "skip   0000-example-noop.patch" in r.stdout


def test_wrong_commit_is_refused(tree):
    src, pin, patches, work = tree
    pin.write_text("tag=v0.0.1\ncommit=" + "0" * 40 + "\n")
    assert apply(src, pin, patches, work).returncode == 2


def test_dirty_submodule_is_refused(tree):
    src, pin, patches, work = tree
    (src / "src" / "hello.txt").write_text("edited in place\n")
    r = apply(src, pin, patches, work)
    assert r.returncode == 2
    assert "pristine" in r.stderr


def test_lint_accepts_good_and_noop(tree):
    _, pin, patches, _ = tree
    (patches / "0001-greeting.patch").write_text(GOOD_HEADER + DIFF)
    (patches / "0000-example-noop.patch").write_text(GOOD_HEADER + "Noop: true\n")
    r = lint(pin, patches)
    assert r.returncode == 0, r.stdout


def test_lint_accepts_to_be_submitted(tree):
    _, pin, patches, _ = tree
    header = GOOD_HEADER.replace("Upstream-Status: Pending", "Upstream-Status: to be submitted")
    (patches / "0001-greeting.patch").write_text(header + DIFF)
    r = lint(pin, patches)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize(
    "mutate, expect",
    [
        (lambda h: h.replace("Purpose: Change the greeting.\n", ""), "missing header field 'Purpose:'"),
        (lambda h: h.replace("SPDX-License-Identifier: AGPL-3.0-only", "SPDX-License-Identifier: MIT"), "must be AGPL-3.0-only"),
        (lambda h: h.replace("Upstream-Status: Pending", "Upstream-Status: maybe"), "Upstream-Status must be"),
        (lambda h: h.replace("Date: Fri, 9 Oct 2026 12:00:00 -0400", "Date: soon"), "not an RFC 2822"),
        (lambda h: h.replace("A Dev <dev@example.com>", "A Dev"), "'From:' must be"),
        (lambda h: h.replace("Orca-Base: v0.0.1", "Orca-Base: v9.9.9"), "pins v0.0.1"),
    ],
)
def test_lint_rejects_bad_headers(tree, mutate, expect):
    _, pin, patches, _ = tree
    (patches / "0001-greeting.patch").write_text(mutate(GOOD_HEADER) + DIFF)
    r = lint(pin, patches)
    assert r.returncode == 1
    assert expect in r.stdout


def test_lint_rejects_names_duplicates_and_missing_hunks(tree):
    _, pin, patches, _ = tree
    (patches / "0001-greeting.patch").write_text(GOOD_HEADER + DIFF)
    (patches / "0001-other.patch").write_text(GOOD_HEADER + DIFF)
    (patches / "fix-me.patch").write_text(GOOD_HEADER + DIFF)
    (patches / "0002-empty.patch").write_text(GOOD_HEADER)
    (patches / "notes.txt").write_text("stray")
    out = lint(pin, patches).stdout
    assert "also used by" in out
    assert "file name must match" in out
    assert "no 'diff --git' hunks" in out
    assert "only *.patch files" in out


def test_lint_rejects_repo_prefixed_paths(tree):
    _, pin, patches, _ = tree
    bad = DIFF.replace("a/src/hello.txt", "a/engine/third_party/OrcaSlicer/src/hello.txt").replace(
        "b/src/hello.txt", "b/engine/third_party/OrcaSlicer/src/hello.txt"
    )
    (patches / "0001-greeting.patch").write_text(GOOD_HEADER + bad)
    assert "relative to the Orca tree root" in lint(pin, patches).stdout


def test_repo_series_is_clean():
    """The committed series and pin must lint clean."""
    r = tool("lint_patch_headers.py")
    assert r.returncode == 0, r.stdout
