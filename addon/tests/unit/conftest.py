# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixtures shared by the unit tests that talk to the in-process fake servers (``tests/fake_net.py``)."""
from __future__ import annotations

import fake_net
import pytest


@pytest.fixture(scope="session")
def certs(tmp_path_factory):
    """Two throw-away self-signed certificates ``(a, b)``, each ``(certfile, keyfile)``, generated with the openssl
    command line for this test session. Skips when openssl is not installed."""
    if not fake_net.have_openssl():
        pytest.skip("the openssl command line is needed to make a throw-away test certificate")
    directory = tmp_path_factory.mktemp("certs")
    return fake_net.make_self_signed(directory, "a"), fake_net.make_self_signed(directory, "b")


@pytest.fixture(autouse=True)
def network_allowed():
    """The unit tests talk to loopback fake servers: install an "allowed" provider for them (the default, with no
    Blender around, is to refuse; ``test_net_access.py`` covers that)."""
    from slicewright.core.network import access
    access.set_provider(lambda: access.ALLOW)
    yield
    access.set_provider(None)
