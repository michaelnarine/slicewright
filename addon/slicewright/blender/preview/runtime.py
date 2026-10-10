# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-scene preview runtime objects (never in RNA, 03 section 2.1): result, layers, renderer.

``bpy`` is imported lazily so the module is importable in plain Python.
"""
from __future__ import annotations

from typing import Callable


class PreviewRuntime:
    """What a scene's preview holds while a result is shown."""

    def __init__(self, result, renderer=None) -> None:
        self.result = result
        self.layers = result.layers
        self.moves = result.moves
        self.n_moves = len(result.moves["type"])
        self.renderer = renderer
        self.controller = None


_runtimes: dict[str, PreviewRuntime] = {}
_view_listeners: list[Callable] = []


def get(scene) -> PreviewRuntime | None:
    return _runtimes.get(scene.name)


def put(scene, runtime: PreviewRuntime | None) -> None:
    if runtime is None:
        _runtimes.pop(scene.name, None)
    else:
        _runtimes[scene.name] = runtime
    tag_redraw()


def clear_all() -> None:
    _runtimes.clear()


def release_all() -> None:
    """Release every controller's GPU resources and forget the runtimes (load_pre, unregister)."""
    for rt in list(_runtimes.values()):
        if rt.controller is not None:
            rt.controller.release()
    _runtimes.clear()


def off_view_change(fn: Callable) -> None:
    while fn in _view_listeners:
        _view_listeners.remove(fn)


def on_view_change(fn: Callable) -> None:
    """Register ``fn(scene)``, called when the view type or range settings change."""
    if fn not in _view_listeners:
        _view_listeners.append(fn)


def view_changed(scene) -> None:
    for fn in list(_view_listeners):
        fn(scene)


def tag_redraw() -> None:
    try:
        import bpy
        wm = bpy.context.window_manager
    except Exception:  # noqa: BLE001 - no window manager (background, tests)
        return
    for win in getattr(wm, "windows", ()):
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
