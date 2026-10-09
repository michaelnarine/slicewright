# SPDX-License-Identifier: GPL-3.0-or-later
"""Module surface, versioning and module-level helpers (04 sections 3, 7, 10)."""
from __future__ import annotations

import ast
import inspect
import os
import zipfile
from pathlib import Path

import pytest
from contract_helpers import unavailable

REPO = Path(__file__).resolve().parents[3]
STUB = REPO / "engine" / "python" / "slicewright_engine" / "__init__.pyi"
JOB_NAMES = {"SliceJob", "SliceResult"}
EXCEPTIONS = {
    "Cancelled": "Error", "Busy": "Error", "StateError": "Error", "ConfigError": "Error",
    "ValidationError": "Error", "SliceError": "Error", "ArrangeError": "Error",
    "EngineError": "Error",
}
REQUIRED_API = (1, 0)


def _stub_tree() -> ast.Module:
    return ast.parse(STUB.read_text(encoding="utf-8"))


def _sig_shape(args: ast.arguments, drop_self: bool):
    """(positional names, keyword-only names, names that have defaults) of a stub function."""
    pos = [a.arg for a in args.posonlyargs + args.args]
    if drop_self and pos and pos[0] == "self":
        pos = pos[1:]
    kwonly = [a.arg for a in args.kwonlyargs]
    n_pos_defaults = len(args.defaults)
    with_default = set(pos[len(pos) - n_pos_defaults:]) if n_pos_defaults else set()
    with_default |= {a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None}
    return pos, kwonly, with_default


def _check_callable(obj, node: ast.FunctionDef, drop_self: bool, label: str) -> None:
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return  # native callables may not expose a signature; names are still checked
    params = [p for p in sig.parameters.values() if not (drop_self and p.name == "self")]
    pos = [p.name for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    kwonly = [p.name for p in params if p.kind == p.KEYWORD_ONLY]
    with_default = {p.name for p in params if p.default is not p.empty}
    want_pos, want_kwonly, want_default = _sig_shape(node.args, drop_self=drop_self)
    assert pos == want_pos, f"{label}: positional parameters {pos} != stub {want_pos}"
    assert kwonly == want_kwonly, f"{label}: keyword-only parameters {kwonly} != stub {want_kwonly}"
    assert with_default == want_default, f"{label}: defaults on {with_default} != stub {want_default}"


def _stub_functions():
    return [n for n in _stub_tree().body if isinstance(n, ast.FunctionDef)]


def _stub_classes():
    return [n for n in _stub_tree().body if isinstance(n, ast.ClassDef)]


def _require(backend, request, name: str):
    if hasattr(backend, name):
        return getattr(backend, name)
    if name in JOB_NAMES and not hasattr(backend, "SliceJob"):
        unavailable(request.config, backend._contract_backend_name, f"no {name} yet")
    pytest.fail(f"backend is missing public name {name!r}")


@pytest.mark.parametrize("fn", _stub_functions(), ids=lambda n: n.name)
def test_module_functions_match_stub(backend, request, fn):
    obj = _require(backend, request, fn.name)
    assert callable(obj)
    _check_callable(obj, fn, drop_self=False, label=fn.name)


@pytest.mark.parametrize("cls", _stub_classes(), ids=lambda n: n.name)
def test_classes_match_stub(backend, request, cls):
    obj = _require(backend, request, cls.name)
    assert inspect.isclass(obj)
    for node in cls.body:
        if isinstance(node, ast.FunctionDef):
            is_property = any(isinstance(d, ast.Name) and d.id == "property"
                              for d in node.decorator_list)
            member = inspect.getattr_static(obj, node.name, None)
            assert member is not None, f"{cls.name}.{node.name} missing"
            if is_property:
                assert isinstance(member, property), f"{cls.name}.{node.name} must be a property"
            elif node.name != "__init__":
                _check_callable(getattr(obj, node.name), node, drop_self=True,
                                label=f"{cls.name}.{node.name}")


@pytest.mark.parametrize("name,base", sorted(EXCEPTIONS.items()))
def test_exception_hierarchy(backend, name, base):
    cls = getattr(backend, name)
    assert issubclass(cls, getattr(backend, base))
    assert issubclass(backend.Error, Exception)
    assert issubclass(backend.Cancelled, backend.Error)


def test_api_version_and_version_dict(backend):
    assert tuple(backend.API_VERSION)[0] == REQUIRED_API[0]
    info = backend.version()
    assert isinstance(info["version"], str)
    assert isinstance(info["api"], tuple) and len(info["api"]) == 2
    assert info["api"] == tuple(backend.API_VERSION)
    for key in ("orca_tag", "orca_commit", "source_url"):
        assert isinstance(info[key], str)
    assert isinstance(info["patches"], list)
    build = info["build"]
    assert build["platform"] in ("macos-arm64", "windows-x64", "linux-x64")
    assert isinstance(build["compiler"], str) and isinstance(build["date"], str)
    assert isinstance(build["deps"], dict)


def test_version_rule_accepts_the_backend(backend):
    """04 section 10: major equal, minor at least the required one."""
    api = tuple(backend.version()["api"])
    assert api[0] == REQUIRED_API[0] and api[1] >= REQUIRED_API[1]


def test_enums(backend):
    e = backend.enums()
    assert set(e) >= {"move_type", "role"}
    for table in e.values():
        assert all(isinstance(k, str) and isinstance(v, int) for k, v in table.items())
    assert max(e["move_type"].values()) < 16
    assert max(e["role"].values()) < 32
    assert "Extrude" in e["move_type"] and "Travel" in e["move_type"]
    assert "None" in e["role"] and "Mixed" in e["role"]
    assert len(set(e["move_type"].values())) == len(e["move_type"])
    assert len(set(e["role"].values())) == len(e["role"])


def test_licenses(backend):
    lic = backend.licenses()
    assert set(lic) >= {"LICENSE", "THIRD_PARTY_LICENSES", "NOTICE", "SOURCE"}
    assert all(isinstance(v, str) for v in lic.values())


def test_profiles_archive_and_resources_dir(backend):
    path = backend.profiles_archive()
    assert isinstance(path, str) and os.path.isfile(path)
    assert zipfile.is_zipfile(path)
    assert os.path.isdir(backend.resources_dir())


@pytest.mark.parametrize("level", [0, 1, 3, 5])
def test_set_log_accepts_levels(backend, level):
    backend.set_log(level)
    backend.set_log(1, None)  # restore the documented default


def test_set_log_rejects_bad_arguments(backend):
    with pytest.raises((TypeError, ValueError)):
        backend.set_log("loud")
    with pytest.raises((TypeError, ValueError, OverflowError)):
        backend.set_log(99)


def test_cancel_token(backend):
    token = backend.CancelToken()
    assert token.cancelled is False
    token.cancel()
    assert token.cancelled is True
    token.cancel()
    assert token.cancelled is True
