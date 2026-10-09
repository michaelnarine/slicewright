# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for engine/tools/collect_dep_licenses.py (synthetic archives, no Orca needed)."""
from __future__ import annotations

import io
import sys
import tarfile
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import collect_dep_licenses as cdl  # noqa: E402


def make_tar(path: Path, files: dict[str, str]) -> None:
    with tarfile.open(path, "w:gz") as t:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


def make_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)


def downloads(tmp_path: Path) -> Path:
    dl = tmp_path / "dl"
    for d in ("Boost", "ZLIB", "libnoise", "Bare"):
        (dl / d).mkdir(parents=True)
    make_tar(dl / "Boost" / "boost.tar.gz", {"boost-1/LICENSE_1_0.txt": "BSL", "boost-1/libs/x/LICENSE": "deep"})
    make_zip(dl / "ZLIB" / "v1.zip", {"zlib-1/README": "zlib licence", "zlib-1/zlib.h": "x"})
    make_zip(dl / "libnoise" / "1.0.zip", {"libnoise-1/src/noise.cpp": "x"})
    make_zip(dl / "Bare" / "a.zip", {"n-1/src/main.c": "x"})
    return dl


def test_finds_licences_and_ignores_deep_files(tmp_path):
    out = tmp_path / "out"
    manifest, errors = cdl.collect(downloads(tmp_path), ["Boost", "ZLIB"], out)
    assert errors == []
    assert [p.name for p in (out / "licenses" / "Boost").iterdir()] == ["LICENSE_1_0.txt"]
    assert (out / "licenses" / "ZLIB" / "README").read_text() == "zlib licence"
    assert any("sha256=" in line for line in manifest)


def test_supplied_text_is_used_and_recorded(tmp_path):
    out = tmp_path / "out"
    manifest, errors = cdl.collect(downloads(tmp_path), ["libnoise"], out)
    assert errors == []
    assert (out / "licenses" / "libnoise" / "LGPL-2.1.txt").is_file()
    assert any("supplied by Slicewright" in line for line in manifest)


def test_missing_licence_and_missing_archive_fail(tmp_path):
    _, errors = cdl.collect(downloads(tmp_path), ["Bare", "Absent"], tmp_path / "out")
    assert len(errors) == 2
    assert "no licence file" in errors[0] and "no source archive" in errors[1]


def test_cli_exit_status(tmp_path):
    libdir = tmp_path / "prefix" / "usr" / "local" / "lib"
    libdir.mkdir(parents=True)
    (libdir / "libz.a").write_bytes(b"")
    args = ["--downloads", str(downloads(tmp_path)), "--prefix", str(tmp_path / "prefix"),
            "--out", str(tmp_path / "out"), "--commit", "abc", "--deps"]
    assert cdl.main(args + ["Boost;ZLIB;libnoise"]) == 1  # GMP and MPFR archives are absent
    assert cdl.main(args + ["Bare"]) == 1


def test_in_tree_recipe_reads_the_orca_tree(tmp_path):
    expat = tmp_path / "orca" / "deps" / "EXPAT" / "expat"
    expat.mkdir(parents=True)
    (expat / "COPYING").write_text("MIT")
    (expat / "expat.h").write_text("x")
    out, dl = tmp_path / "out", downloads(tmp_path)
    manifest, errors = cdl.collect(dl, ["EXPAT"], out, tmp_path / "orca")
    assert errors == [] and (out / "licenses" / "EXPAT" / "COPYING").read_text() == "MIT"
    _, errors = cdl.collect(dl, ["EXPAT"], out, tmp_path / "nowhere")
    assert errors and "no licence file" in errors[0]
