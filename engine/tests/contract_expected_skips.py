# SPDX-License-Identifier: AGPL-3.0-only
"""pytest plugin for engine CI: runs the add-on's contract suite against the real module (``--backend=real``)
and turns the tests for functions that this milestone has not implemented yet into *expected* skips.

    PYTHONPATH=engine/tests:<build>/python pytest -p contract_expected_skips --backend=real addon/tests/contract

``--backend=real`` makes a missing name a failure on purpose (a broken wheel must not hide behind a skip), so
the not-yet-implemented parts are listed here, each with the layer or milestone that removes the entry; the
summary line counts them so the list cannot grow unnoticed. Nothing on the add-on side is edited.
"""
from __future__ import annotations

import pytest

# Whole test modules (the part of the API they exercise is not in the engine yet).
SKIPPED_FILES: dict[str, str] = {
    "test_job_build.py": "SliceJob building and validation: M5",
    "test_job_states.py": "SliceJob state machine: M5",
    "test_result.py": "SliceResult content (moves, layers, stats): M5",
    "test_arrange.py": "SliceJob.arrange: M5",
}

# Single tests by function name.
SKIPPED_TESTS: dict[str, str] = {}

# Stub names (functions and classes) checked by test_surface.py::test_module_functions_match_stub and
# test_classes_match_stub, with the reason they are not required yet.
SKIPPED_SURFACE: dict[str, str] = {
    "SliceResult": "SliceResult: M5",
}

_skipped: list[str] = []


def _reason(item) -> str | None:
    spec = getattr(item, "callspec", None)
    if spec is None or spec.params.get("backend") != "real":
        return None
    name = item.originalname if hasattr(item, "originalname") else item.name
    if item.path.name in SKIPPED_FILES:
        return SKIPPED_FILES[item.path.name]
    if name in SKIPPED_TESTS:
        return SKIPPED_TESTS[name]
    node = spec.params.get("fn") or spec.params.get("cls")
    if node is not None and node.name in SKIPPED_SURFACE:
        return SKIPPED_SURFACE[node.name]
    return None


# Known differences between the contract tests and Orca's actual behaviour (strict xfail: the entry must be
# removed once the contract or the engine changes). Keyed by (test function, parametrized argument, value).
KNOWN_DIFFERENCES: dict[tuple[str, str, str], str] = {
    ("test_eval_condition", "expr", 'not printer_model == "A1"'):
        "Orca's PlaceholderParser binds 'not' tighter than '==': 'not a == b' is a parse error ('Cannot apply a "
        "not operator'); the contract test needs 'not (a == b)'. Spec question for the lead.",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        spec = getattr(item, "callspec", None)
        if spec is not None and spec.params.get("backend") == "real":
            name = getattr(item, "originalname", item.name)
            for (test, arg, value), why in KNOWN_DIFFERENCES.items():
                if name == test and spec.params.get(arg) == value:
                    item.add_marker(pytest.mark.xfail(reason=why, strict=True))
        reason = _reason(item)
        if reason:
            item.add_marker(pytest.mark.skip(reason=f"expected, not implemented yet: {reason}"))
            _skipped.append(item.nodeid)


def pytest_terminal_summary(terminalreporter):
    if _skipped:
        terminalreporter.write_line(f"contract_expected_skips: {len(_skipped)} expected skips for the real backend")
