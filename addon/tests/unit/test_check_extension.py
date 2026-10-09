# SPDX-License-Identifier: GPL-3.0-or-later
"""addon/tools/check_extension.py on small hand-made zips."""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_extension.py"
spec = importlib.util.spec_from_file_location("check_extension", TOOL)
check_extension = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_extension)

MANIFEST = 'copyright = ["2026 Someone"]\n'
GOOD_PY = "# SPDX-License-Identifier: GPL-3.0-or-later\nx = 1\n"


def make(tmp_path: Path, files: dict[str, str]) -> str:
    path = tmp_path / "ext.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return str(path)


def test_a_clean_zip_passes(tmp_path):
    assert check_extension.check_zip(make(tmp_path, {
        "blender_manifest.toml": MANIFEST, "LICENSE": "text", "__init__.py": GOOD_PY})) == []


def test_a_zip_without_the_licence_fails(tmp_path):
    problems = check_extension.check_zip(make(tmp_path, {"blender_manifest.toml": MANIFEST}))
    assert any("no LICENSE" in p for p in problems)


@pytest.mark.parametrize("extra,needle", [
    ({"__pycache__/x.cpython-313.pyc": "x"}, "__pycache__"),
    ({"tests/test_a.py": GOOD_PY}, "tests/test_a.py"),
    ({"icon.png": "x"}, "binary asset"),
    ({"bad.py": "x = 1\n"}, "no 'SPDX"),
])
def test_problems_are_reported(tmp_path, extra, needle):
    problems = check_extension.check_zip(make(tmp_path, {"blender_manifest.toml": MANIFEST, "LICENSE": "x", **extra}))
    assert any(needle in p for p in problems), problems


def test_copyright_must_start_with_a_year(tmp_path):
    zpath = make(tmp_path, {"blender_manifest.toml": 'copyright = ["Someone 2026"]\n', "LICENSE": "x"})
    assert any("year" in p for p in check_extension.check_zip(zpath))


def test_missing_manifest(tmp_path):
    assert any("no blender_manifest" in p for p in check_extension.check_zip(make(tmp_path, {"a.py": GOOD_PY})))
