# SPDX-License-Identifier: GPL-3.0-or-later
"""Structural rules for ``addon/slicewright`` (03 sections 6.1 and 9.1), checked on the AST."""
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "slicewright"
THREAD_MODULES = {"threading", "_thread", "concurrent", "multiprocessing", "asyncio"}


def _sources(*subdirs: str):
    roots = [PKG / s for s in subdirs] if subdirs else [PKG]
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0], node.lineno


def test_package_exists_and_has_sources():
    assert any(True for _ in _sources())


def test_no_threading_anywhere_in_the_addon():
    """The design uses timers plus a pollable engine: no threads, processes or event loops."""
    bad = [f"{p.relative_to(PKG)}:{line} imports {mod}"
           for p, tree in _sources() for mod, line in _imports(tree) if mod in THREAD_MODULES]
    assert not bad, bad


def test_core_and_engine_packages_do_not_import_bpy():
    bad = [f"{p.relative_to(PKG)}:{line}"
           for p, tree in _sources("core", "engine", "names.py") for mod, line in _imports(tree)
           if mod in ("bpy", "gpu", "bmesh", "mathutils", "bl_ui")]
    assert not bad, bad


def test_only_the_adapter_names_the_engine_module():
    """Everything else goes through ``engine.adapter`` (04 section 10)."""
    bad = []
    for p, tree in _sources():
        if p.name in ("adapter.py", "names.py"):
            continue
        for mod, line in _imports(tree):
            if mod in ("slicewright_engine", "fake_engine"):
                bad.append(f"{p.relative_to(PKG)}:{line}")
    assert not bad, bad


def test_importing_the_package_does_not_import_bpy():
    """Only function-level imports of bpy in the package ``__init__``, so unit tests can import it."""
    tree = ast.parse((PKG / "__init__.py").read_text(encoding="utf-8"))
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    found = {a.name.split(".")[0] for n in top_level if isinstance(n, ast.Import) for a in n.names}
    found |= {n.module.split(".")[0] for n in top_level
              if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module}
    assert "bpy" not in found
