# SPDX-License-Identifier: GPL-3.0-or-later
"""engine/adapter.py: API check, import failures, diagnostics text, exception mapping."""
from __future__ import annotations

import importlib
import types

import fake_engine as sc
import pytest
from slicewright.engine import adapter
from slicewright.engine.adapter import (
    REQUIRED_API, ErrorReport, EngineStatus, api_compatible, diagnostics_text, load,
    map_exception, module_name_from_env,
)


@pytest.mark.parametrize("found,ok", [
    ((1, 0), True), ((1, 7), True), ((0, 9), False), ((2, 0), False), ((2, 5), False),
])
def test_api_compatible_follows_the_04_rule(found, ok):
    assert api_compatible(found) is ok
    assert REQUIRED_API == (1, 0)


def test_load_the_fake_engine():
    status = load("fake_engine")
    assert status.ok and status.module is sc and status.error is None
    assert status.found_api == (1, 0) and status.info["version"]
    assert "engine" in status.summary and "1.0" in status.summary


def test_load_reports_import_errors_instead_of_raising():
    status = load("no_such_engine_module")
    assert not status.ok and status.module is None
    assert "no_such_engine_module" in status.error and status.found_api is None
    assert status.summary == status.error


def test_load_reports_an_incompatible_api_with_both_versions():
    fake = types.SimpleNamespace(version=lambda: {"version": "9.0.0", "api": (2, 1)})
    status = load("fake_engine", importer=lambda name: fake)
    assert not status.ok and status.module is None
    assert status.found_api == (2, 1) and status.required_api == (1, 0)
    assert "2.1" in status.error and "1.0" in status.error
    assert status.info["version"] == "9.0.0"


def test_load_reports_a_broken_version_function():
    def boom():
        raise RuntimeError("nope")
    status = load("fake_engine", importer=lambda name: types.SimpleNamespace(version=boom))
    assert not status.ok and "version() failed" in status.error


@pytest.mark.parametrize("api", [None, 1, "1.0", (1,), (1, 0, 2), ("1", "0"), (1.0, 0), (True, 0), [], {}])
def test_load_reports_a_malformed_api_instead_of_raising(api):
    fake = types.SimpleNamespace(version=lambda: {"version": "1.0.0", "api": api})
    status = load("fake_engine", importer=lambda name: fake)
    assert not status.ok and status.module is None
    assert "version() failed" in status.error


def test_load_reports_a_missing_api_key():
    fake = types.SimpleNamespace(version=lambda: {"version": "1.0.0"})
    assert "version() failed" in load("fake_engine", importer=lambda name: fake).error


def test_load_accepts_a_list_api_with_two_ints():
    fake = types.SimpleNamespace(version=lambda: {"version": "1.0.0", "api": [1, 2]})
    status = load("fake_engine", importer=lambda name: fake)
    assert status.ok and status.found_api == (1, 2)


def test_load_accepts_a_newer_minor_version():
    fake = types.SimpleNamespace(version=lambda: {"version": "1.4.0", "api": (1, 4)})
    assert load("fake_engine", importer=lambda name: fake).ok


def test_module_name_comes_from_the_env_but_only_from_the_allowlist():
    assert module_name_from_env({}) == "slicewright_engine"
    assert module_name_from_env({adapter.ENV_VAR: "fake_engine"}) == "fake_engine"
    assert module_name_from_env({adapter.ENV_VAR: "os"}) == "slicewright_engine"


def test_engine_path_env_puts_existing_directories_first(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    path = ["/already"]
    env = {adapter.PATH_ENV_VAR: f"{a}{adapter.os.pathsep}{tmp_path / 'missing'}{adapter.os.pathsep}{b}"}
    assert adapter.add_engine_paths(env, path) == [str(a), str(b)]
    assert path == [str(a), str(b), "/already"]
    assert adapter.add_engine_paths(env, path) == []              # idempotent
    assert adapter.add_engine_paths({}, path) == []


def test_load_the_real_module_goes_through_the_engine_path(monkeypatch, tmp_path):
    pkg = tmp_path / "slicewright_engine"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("def version():\n    return {'api': (1, 0), 'version': 'stub'}\n")
    monkeypatch.setenv(adapter.PATH_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(adapter.sys, "path", list(adapter.sys.path))
    monkeypatch.delitem(adapter.sys.modules, "slicewright_engine", raising=False)
    try:
        status = load("slicewright_engine")
        assert status.ok and status.info["version"] == "stub"
    finally:
        adapter.sys.modules.pop("slicewright_engine", None)


def test_diagnostics_text_has_versions_error_and_extras():
    bad = EngineStatus(False, "slicewright_engine", found_api=(2, 0), error="too new",
                       info={"version": "2.0.0", "orca_tag": "v9"})
    text = diagnostics_text(bad, {"GPU": "Metal"}, log_tail="line1\nline2\n")
    for needle in ("Required API: 1.0", "Found API: 2.0", "too new", "version: 2.0.0",
                   "orca_tag: v9", "GPU: Metal", "Log tail:", "line2"):
        assert needle in text
    good = diagnostics_text(load("fake_engine"))
    assert "Engine OK: True" in good and "build:" in good


# --- exception mapping ---------------------------------------------------------------------

def test_cancelled_is_informational():
    r = map_exception(sc, sc.Cancelled("x"))
    assert (r.severity, r.action, r.kind) == ("INFO", "none", "Cancelled")


def test_busy_asks_to_retry():
    assert map_exception(sc, sc.Busy("busy")).action == "retry_later"


def test_state_error_is_a_bug_report():
    r = map_exception(sc, sc.StateError("bad call", "done"))
    assert r.action == "report_bug" and "done" in r.hint


def test_config_error_highlights_the_key():
    r = map_exception(sc, sc.ConfigError("bad", key="layer_height", value="x"))
    assert (r.action, r.opt_key) == ("highlight_key", "layer_height")


def test_validation_error_carries_issues_and_targets():
    issues = [{"level": "error", "code": "paint_out_of_range", "message": "m",
               "opt_key": "filament_colour", "object_name": "Cube"}]
    r = map_exception(sc, sc.ValidationError("m", issues))
    assert r.action == "jump_to_issue" and r.opt_key == "filament_colour"
    assert r.object_name == "Cube" and r.issues == tuple(issues)


def test_slice_arrange_and_engine_errors():
    assert map_exception(sc, sc.SliceError("m", "Cube")).object_name == "Cube"
    assert map_exception(sc, sc.ArrangeError("m", ["a", "b"])).object_names == ("a", "b")
    r = map_exception(sc, sc.EngineError("m", "std::runtime_error"))
    assert r.action == "copy_diagnostics" and r.hint == "std::runtime_error"


def test_builtin_exceptions():
    assert map_exception(sc, MemoryError()).hint.startswith("Try fewer threads")
    assert map_exception(sc, TimeoutError()).severity == "WARNING"
    assert map_exception(sc, TypeError("t")).action == "report_bug"
    assert map_exception(sc, ValueError("v")).action == "report_bug"
    assert map_exception(sc, KeyError("k")).action == "report_bug"


def test_a_plain_engine_error_and_unknown_modules():
    assert map_exception(sc, sc.Error("e")).action == "copy_diagnostics"
    # a module that lacks the exception classes still classifies builtins
    bare = types.SimpleNamespace()
    assert isinstance(map_exception(bare, RuntimeError("r")), ErrorReport)


def test_every_action_is_declared():
    samples = [sc.Cancelled(), sc.Busy(), sc.StateError(), sc.ConfigError(), sc.ValidationError(),
               sc.SliceError(), sc.ArrangeError(), sc.EngineError(), MemoryError(), TypeError()]
    assert {map_exception(sc, e).action for e in samples} <= set(adapter.ACTIONS)


def test_adapter_does_not_import_bpy():
    src = importlib.util.find_spec("slicewright.engine.adapter").origin
    assert "import bpy" not in open(src, encoding="utf-8").read()


def test_checkout_engine_dir_finds_the_build_of_a_source_checkout(tmp_path):
    root = tmp_path / "repo"
    mod = root / "addon" / "slicewright" / "engine"
    mod.mkdir(parents=True)
    (mod / "adapter.py").write_text("")
    assert adapter.checkout_engine_dir(str(mod / "adapter.py")) is None            # not built yet
    (root / "engine" / "build" / "engine" / "python" / "slicewright_engine").mkdir(parents=True)
    assert adapter.checkout_engine_dir(str(mod / "adapter.py")) == str(root / "engine" / "build" / "engine" / "python")


def test_checkout_engine_dir_is_none_for_an_installed_copy(tmp_path):
    mod = tmp_path / "extensions" / "slicewright" / "engine"
    mod.mkdir(parents=True)
    (mod / "adapter.py").write_text("")
    assert adapter.checkout_engine_dir(str(mod / "adapter.py")) is None
