# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``backend`` fixture: one contract suite, parametrized over engine backends (04 section 11)."""
import importlib

import pytest
from contract_helpers import unavailable

BACKENDS = ("fake", "real")
_MODULES = {"fake": "fake_engine", "real": "slicewright_engine"}


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
