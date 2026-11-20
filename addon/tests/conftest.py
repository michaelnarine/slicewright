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

# The real engine, when it is built: ``SLICEWRIGHT_ENGINE_PATH`` (as the add-on reads it) or the build directory of
# ``engine/tools/build_local_macos.sh``. Putting it on ``sys.path`` makes ``slicewright_engine`` importable here and,
# through the environment variable, in the headless Blender subprocesses of ``tests/blender``.
ENGINE_BUILD_DIR = TESTS_DIR.parents[1] / "engine" / "build" / "engine" / "python"


def _find_real_engine() -> None:
    import os
    env = os.environ.get("SLICEWRIGHT_ENGINE_PATH", "")
    if not env and (ENGINE_BUILD_DIR / "slicewright_engine").is_dir():
        env = os.environ["SLICEWRIGHT_ENGINE_PATH"] = str(ENGINE_BUILD_DIR)
    for entry in reversed([e for e in env.split(os.pathsep) if e]):
        if entry not in sys.path:
            sys.path.append(entry)


_find_real_engine()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "fake_verified_only: asserts something only the fake engine has exercised so far; "
        "re-check against the real engine at plan M2 layer 14")
    config.addinivalue_line(
        "markers",
        "real_engine: runs the add-on's orchestration against the built slicewright_engine; skipped when it is not built")


def pytest_addoption(parser):
    parser.addoption(
        "--backend", action="append", choices=BACKENDS, default=None,
        help="Engine backend(s) for the contract suite (default: all that can be loaded).",
    )


import pytest  # noqa: E402


def real_engine_or_none():
    """The imported ``slicewright_engine`` module, or ``None`` when it is not built / not importable."""
    try:
        import slicewright_engine
    except ImportError:
        return None
    return slicewright_engine if hasattr(slicewright_engine, "API_VERSION") else None


@pytest.fixture(scope="session")
def real_sc(request):
    """The real engine (``sc`` in 04) for the tests of the add-on's orchestration against it. Skips cleanly when the
    module is not built, unless ``--backend=real`` asked for it (then a missing module fails)."""
    module = real_engine_or_none()
    if module is None:
        message = ("slicewright_engine is not built (engine/tools/build_local_macos.sh) or not on "
                   "SLICEWRIGHT_ENGINE_PATH")
        if "real" in (request.config.getoption("--backend") or ()):
            pytest.fail(message)
        pytest.skip(message)
    return module
