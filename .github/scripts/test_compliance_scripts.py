# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for check_spdx.py and check_dco.py (run with pytest)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import check_dco  # noqa: E402
import check_spdx  # noqa: E402

GPL = "# SPDX-License-Identifier: GPL-3.0-or-later\n"
AGPL = "# SPDX-License-Identifier: AGPL-3.0-only\n"


def test_which_files_are_checked():
    assert check_spdx.is_checked("addon/slicewright/__init__.py")
    assert check_spdx.is_checked("engine/CMakeLists.txt")
    assert check_spdx.is_checked("engine/python/slicewright_engine/__init__.pyi")
    assert not check_spdx.is_checked("docs/design/x.py")
    assert not check_spdx.is_checked("addon/LICENSE")
    assert not check_spdx.is_checked("addon/slicewright/blender_manifest.toml")
    assert not check_spdx.is_checked("engine/third_party/OrcaSlicer/src/a.cpp")
    assert not check_spdx.is_checked("engine/patches/orca/0001.cmake")
    assert not check_spdx.is_checked("engine/tests/fixtures/exported/schema.py")


def test_matching_header_passes():
    assert check_spdx.check_file("addon/a.py", GPL + "x = 1\n") is None
    assert check_spdx.check_file("engine/a.py", AGPL + "x = 1\n") is None
    assert check_spdx.check_file("engine/a.cpp", "// SPDX-License-Identifier: AGPL-3.0-only\n") is None
    assert check_spdx.check_file("engine/a.c", "/* SPDX-License-Identifier: AGPL-3.0-only */\n") is None


def test_missing_header_fails():
    assert "missing" in check_spdx.check_file("addon/a.py", "x = 1\n")


def test_crossed_boundary_fails():
    assert "requires exactly" in check_spdx.check_file("addon/a.py", AGPL)
    assert "requires exactly" in check_spdx.check_file("engine/a.py", GPL)


def test_two_identifiers_fail():
    assert check_spdx.check_file("addon/a.py", GPL + AGPL)


def test_header_must_be_near_top():
    text = "\n" * 30 + GPL
    assert check_spdx.check_file("addon/a.py", text)


def test_dco_trailer():
    good = "Add thing\n\nSigned-off-by: A B <a@example.com>\n"
    bad = "Add thing\n"
    no_email = "Add thing\n\nSigned-off-by: A B\n"
    assert check_dco.unsigned([("1", good), ("2", bad), ("3", no_email)]) == ["2", "3"]
