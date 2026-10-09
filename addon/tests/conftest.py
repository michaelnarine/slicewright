# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared pytest setup for ``addon/tests``.

* Puts ``addon/tests`` (for ``fake_engine``) and ``addon`` (for the ``slicewright``
  package) on ``sys.path``.
* Adds ``--backend`` to select which engine backends the contract suite runs
  against: ``fake`` (``addon/tests/fake_engine``) and/or ``real``
  (the installed ``slicewright_engine`` wheel). With no option every backend is
  tried and an unavailable one is skipped. An explicitly requested backend that
  cannot be loaded is a failure, so engine CI cannot silently run nothing.
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ADDON_DIR = TESTS_DIR.parent
for p in (str(ADDON_DIR), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

BACKENDS = ("fake", "real")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "fake_verified_only: asserts something only the fake engine has exercised so far; "
        "re-check against the real engine at plan M2 layer 14")


def pytest_addoption(parser):
    parser.addoption(
        "--backend", action="append", choices=BACKENDS, default=None,
        help="Engine backend(s) for the contract suite (default: all that can be loaded).",
    )
