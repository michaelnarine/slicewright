# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``backend`` fixture: one contract suite, parametrized over engine backends (04 section 11)."""
import importlib
import os

import pytest
from contract_helpers import unavailable

BACKENDS = ("fake", "real")
_MODULES = {"fake": "fake_engine", "real": "slicewright_engine"}

# The fake grows in layers (plan section 10). A test module is skipped while the backend lacks
# the names it needs, unless that backend was requested with --backend, where it fails instead,
# so a broken real wheel cannot hide behind a skip (test_surface checks every name).
REQUIRES = {
    "test_config.py": ("compose_config", "normalize_config", "eval_condition", "ConditionContext"),
    "test_job_build.py": ("SliceJob",),
    "test_job_states.py": ("SliceJob",),
    "test_result.py": ("SliceJob",),
    "test_arrange.py": ("SliceJob",),
}


@pytest.fixture(params=BACKENDS)
def backend(request):
    """The engine module under test (``sc`` in 04)."""
    name = request.param
    wanted = request.config.getoption("--backend")
    if wanted and name not in wanted:
        pytest.skip(f"backend {name!r} not selected")
    try:
        module = importlib.import_module(_MODULES[name])
    except ImportError as exc:
        unavailable(request.config, name, str(exc))
    # A stale empty directory on sys.path imports as a namespace package: no __file__, no API.
    if getattr(module, "__file__", None) is None or not hasattr(module, "API_VERSION"):
        unavailable(request.config, name, f"{_MODULES[name]} is not an engine module (stale directory?)")
    missing = [n for n in REQUIRES.get(request.node.path.name, ())
               if not hasattr(module, n)]
    if missing:
        unavailable(request.config, name, f"not implemented yet: {', '.join(missing)}")
    module._contract_backend_name = name  # informational, for failure messages
    return module


@pytest.fixture
def jobs_backend(backend, request):
    """``backend``, skipped while it has no ``SliceJob`` yet.

    The fake grows in layers (plan section 10). A backend lacking ``SliceJob``
    fails ``test_surface`` once the backend is requested explicitly, so this
    skip cannot hide a broken wheel in engine CI.
    """
    if not hasattr(backend, "SliceJob"):
        unavailable(request.config, backend._contract_backend_name, "no SliceJob yet")
    return backend
