# SPDX-License-Identifier: GPL-3.0-or-later
"""blender_manifest.toml against the product-name constants and the 03 section 10 rules."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from slicewright import names

PKG = Path(__file__).resolve().parents[2] / "slicewright"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return tomllib.loads((PKG / "blender_manifest.toml").read_text(encoding="utf-8"))


def test_id_and_name_come_from_the_constants(manifest):
    assert manifest["id"] == names.PACKAGE_ID == PKG.name
    assert manifest["name"] == names.PRODUCT_NAME
    assert names.OP_PREFIX == names.PACKAGE_ID.upper()


def test_required_fields(manifest):
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["type"] == "add-on"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["blender_version_min"] == "5.1.0"
    assert manifest["maintainer"] and manifest["website"].startswith("https://")


def test_tagline_rules(manifest):
    tagline = manifest["tagline"]
    assert len(tagline) <= 64 and not tagline.endswith(".")


def test_platforms_and_tags(manifest):
    assert set(manifest["platforms"]) == {"macos-arm64", "windows-x64", "linux-x64"}
    assert manifest["tags"]


def test_licence_is_spdx_and_includes_gpl(manifest):
    assert all(item.startswith("SPDX:") for item in manifest["license"])
    assert "SPDX:GPL-3.0-or-later" in manifest["license"]


def test_every_copyright_entry_starts_with_a_year(manifest):
    assert manifest["copyright"]
    assert all(re.match(r"^\d{4}(-\d{4})? ", c) for c in manifest["copyright"])


def test_permissions_are_terse_and_from_the_allowed_set(manifest):
    for key, text in manifest.get("permissions", {}).items():
        assert key in {"files", "network", "clipboard", "camera", "microphone"}
        assert text and not text.endswith(".") and len(text) <= 64


def test_no_wheels_are_declared_before_the_engine_exists(manifest):
    assert "wheels" not in manifest


def test_build_excludes_tests_and_caches(manifest):
    patterns = manifest["build"]["paths_exclude_pattern"]
    assert "__pycache__/" in patterns and "/tests/" in patterns


def test_the_notice_file_exists_and_states_no_warranty():
    text = (PKG / "NOTICE").read_text(encoding="utf-8")
    assert "No warranty" in text and "GPL-3.0-or-later" in text
