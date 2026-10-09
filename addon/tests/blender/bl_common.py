# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for scripts that run inside Blender (``blender -b --factory-startup --python script.py``).

Blender's bundled Python has no pytest, so these scripts use ``unittest`` and exit with a
status code that ``test_blender_headless.py`` (stock pytest) checks.
"""
from __future__ import annotations

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.dirname(TESTS_DIR)
for _p in (ADDON_DIR, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def handler_snapshot() -> dict[str, list]:
    """Every ``bpy.app.handlers`` list, copied, for before/after comparisons."""
    import bpy
    out = {}
    for name in dir(bpy.app.handlers):
        value = getattr(bpy.app.handlers, name)
        if isinstance(value, list):
            out[name] = list(value)
    return out


def run(module_name: str) -> None:
    """Run every TestCase in ``module_name`` and exit Blender with 0 (pass) or 1 (fail)."""
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[module_name])
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
    sys.stdout.flush()
    sys.exit(0 if result.wasSuccessful() else 1)
