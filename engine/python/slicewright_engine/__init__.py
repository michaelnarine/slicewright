# SPDX-License-Identifier: AGPL-3.0-only
"""Slicewright engine: OrcaSlicer's libslic3r in-process. The interface is docs/design/04-engine-api.md.

This package is a thin layer over the native extension ``_native``. Everything is implemented natively; the
layer only gives the module-level functions real Python signatures (``inspect.signature`` sees ``(*args,
**kwargs)`` on nanobind functions, and the contract suite checks signatures against the 04 stub). A function
the native module does not provide yet is simply absent, so ``hasattr(sc, name)`` stays truthful while the API
is filled in layer by layer.
"""
from __future__ import annotations

import inspect as _inspect

from . import _native

_E = _inspect.Parameter.empty

# name -> parameters; a (name, default) pair has a default value. Mirrors the stub in 04 section 3.
_FUNCTIONS = {
    "version": (),
    "enums": (),
    "config_schema": (),
    "tab_layout": (),
    "profiles_archive": (),
    "resources_dir": (),
    "licenses": (),
    "set_log": ("level", ("path", None)),
    "compose_config": ("printer", "process", "filaments", ("project", None)),
    "normalize_config": ("flat",),
    "eval_condition": ("expr", "config"),
}

# Classes and constants are re-exported as they are.
_PASSTHROUGH = (
    "API_VERSION",
    "CancelToken",
    "ConditionContext",
    "SliceJob",
    "SliceResult",
    "Error",
    "Cancelled",
    "Busy",
    "StateError",
    "ConfigError",
    "ValidationError",
    "SliceError",
    "ArrangeError",
    "EngineError",
)


def _wrap(name, params):
    native = getattr(_native, name)
    sig = _inspect.Signature([
        _inspect.Parameter(p if isinstance(p, str) else p[0], _inspect.Parameter.POSITIONAL_OR_KEYWORD,
                           default=_E if isinstance(p, str) else p[1])
        for p in params
    ])

    def fn(*args, **kwargs):
        return native(*args, **kwargs)

    fn.__name__ = fn.__qualname__ = name
    fn.__module__ = __name__
    fn.__doc__ = native.__doc__
    fn.__signature__ = sig
    return fn


__all__ = []
for _name, _params in _FUNCTIONS.items():
    if hasattr(_native, _name):
        globals()[_name] = _wrap(_name, _params)
        __all__.append(_name)
for _name in _PASSTHROUGH:
    if hasattr(_native, _name):
        globals()[_name] = getattr(_native, _name)
        __all__.append(_name)
del _name, _params
