# SPDX-License-Identifier: GPL-3.0-or-later
"""A pure-Python stand-in for ``slicewright_engine`` (04 section 11, 03 section 9.2).

Used as ``import fake_engine as sc`` by the contract suite and by add-on tests.
It reports the same ``API_VERSION`` as the real engine and is held to the same
contract tests (``addon/tests/contract``).
"""
from .api import (  # noqa: F401
    API_VERSION, CancelToken, config_schema, enums, licenses, profiles_archive, resources_dir,
    set_log, tab_layout, version,
)
from .errors import (  # noqa: F401
    ArrangeError, Busy, Cancelled, ConfigError, EngineError, Error, SliceError, StateError,
    ValidationError,
)
