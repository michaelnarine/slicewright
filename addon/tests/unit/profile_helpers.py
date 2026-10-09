# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for the profile tests: tiny hand-made archives and the fake engine's fixture."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import fake_engine
from slicewright.core.profiles.index import ProfileIndex, build_index
from slicewright.core.profiles.source import ProfileSource


def make_archive(directory: Path, files: dict[str, dict | str], *, root: str = "profiles/",
                 name: str = "profiles.zip") -> str:
    """Write ``{relative path: JSON object or raw text}`` into a zip; returns its path."""
    path = directory / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in files.items():
            z.writestr(root + rel, data if isinstance(data, str) else json.dumps(data))
    return str(path)


def preset(kind: str, name: str, inherits: str = "", instantiation: bool = True, **extra) -> dict:
    data = {"type": kind, "name": name, "instantiation": "true" if instantiation else "false", **extra}
    if inherits:
        data["inherits"] = inherits
    return data


def drain(gen):
    """Run a generator task to the end and return its value."""
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


def build(source: ProfileSource, **kwargs) -> ProfileIndex:
    return drain(build_index(source, **kwargs))


def fixture_source() -> ProfileSource:
    return ProfileSource(fake_engine.profiles_archive())


def fixture_index() -> ProfileIndex:
    with fixture_source() as src:
        return build(src, commit=fake_engine.version()["orca_commit"])
