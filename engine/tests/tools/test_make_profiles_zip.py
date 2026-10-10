# SPDX-License-Identifier: AGPL-3.0-only
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import make_profiles_zip as mz  # noqa: E402


def make_tree(root: Path):
    (root / "BBL" / "filament").mkdir(parents=True)
    (root / "BBL" / "filament" / "PLA.json").write_text("{}")
    (root / "BBL.json").write_text("{}")
    (root / "check_unused_setting_id.py").write_text("print()")
    (root / "FlyingBear" / "error_hull_show").mkdir(parents=True)
    (root / "FlyingBear" / "error_hull_show" / "x.json").write_text("{}")


def test_only_json_sorted_and_stray_skipped(tmp_path):
    make_tree(tmp_path / "p")
    out = tmp_path / "profiles.zip"
    assert mz.build(tmp_path / "p", out) == 2
    with zipfile.ZipFile(out) as z:
        assert z.namelist() == ["profiles/BBL.json", "profiles/BBL/filament/PLA.json"]


def test_deterministic(tmp_path):
    make_tree(tmp_path / "p")
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    mz.build(tmp_path / "p", a)
    mz.build(tmp_path / "p", b)
    assert a.read_bytes() == b.read_bytes()
