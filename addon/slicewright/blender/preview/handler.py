# SPDX-License-Identifier: GPL-3.0-or-later
"""The viewport draw handler and the scene-level glue around it (03 section 7.10).

``register``/``unregister`` add and remove exactly one ``POST_VIEW`` handler and one ``load_pre`` hook
(the add-on's single ``load_pre`` handler calls it). The draw callback returns at once when the scene has no preview, and an
exception in it is logged once per session instead of every redraw.
"""
from __future__ import annotations

import logging

import bpy

from ... import prefs
from .. import registry, timers
from ...core import preview_budget as budget
from ...core import preview_nav as nav
from . import params, props as preview_props, runtime
from .controller import PreviewController
from .renderer import Renderer

log = logging.getLogger("slicewright.preview")
_draw_handle = None
_error_logged = False


def _device_budget_mb() -> int:
    """The VRAM budget in MB: the preference, or the 03 7.8 default for this GPU."""
    chosen = prefs.preview_vram_mb()
    if chosen > 0:
        return chosen
    import gpu
    dev = gpu.platform.device_type_get()
    return budget.default_budget_mb(dev, budget.system_ram_gb(), apple=dev == "APPLE")


def show_result(scene, result, budget_mb: int | None = None, chunk: int | None = None) -> PreviewController:
    """Preview ``result`` in ``scene``: upload starts on the next timer ticks.

    Called by the orchestration when a slice finishes (and by the dev G-code loader)."""
    from ...core import preview_data as pd
    clear(scene)
    enums = registry.state.status.module.enums()
    rt = runtime.PreviewRuntime(result, Renderer(enums))
    mb = _device_budget_mb() if budget_mb is None else budget_mb
    ctl = PreviewController(rt, rt.renderer, timers.runner, mb * budget.MB, enums,
                            chunk or pd.CHUNK_MOVES, name=f"preview:{scene.name}")
    runtime.put(scene, rt)
    p = getattr(scene, preview_props.PROP_NAME)
    p.set_scrub(nav.full(rt.layers))
    if p.view_type != "feature" and not ctl.set_view(p.view_type, _fixed_range(p)):
        p.view_type = "feature"
    ctl.start()
    return ctl


def clear(scene) -> None:
    """Drop the scene's preview and free its GPU resources."""
    rt = runtime.get(scene)
    if rt is not None and rt.controller is not None:
        rt.controller.release()
    runtime.put(scene, None)


def _fixed_range(p):
    return (p.range_min, p.range_max) if p.range_fixed else None


def _view_changed(scene) -> None:
    rt = runtime.get(scene)
    if rt is None or rt.controller is None:
        return
    p = getattr(scene, preview_props.PROP_NAME)
    if not rt.controller.set_view(p.view_type, _fixed_range(p)):
        p.view_type = "feature"            # over budget: only the feature view exists


def _draw() -> None:
    global _error_logged
    ctx = bpy.context
    rt = runtime.get(ctx.scene)
    if rt is None or rt.controller is None or rt.renderer is None:
        return
    sw = getattr(ctx.scene, "slicewright", None)
    if sw is not None and sw.mode != "PREVIEW":
        return
    try:
        mv, pj = _matrices()
        p = getattr(ctx.scene, preview_props.PROP_NAME)
        dp = params.draw_params(p, rt, (ctx.region.width, ctx.region.height), prefs.preview_lines_threshold())
        rt.renderer.draw(pj @ mv, tuple(mv.inverted().translation), dp)
    except Exception:  # noqa: BLE001 - never raise out of a draw handler
        if not _error_logged:
            _error_logged = True
            log.exception("preview draw failed (reported once)")


def _matrices():
    import gpu
    return gpu.matrix.get_model_view_matrix(), gpu.matrix.get_projection_matrix()


def _load_pre() -> None:
    """A file load invalidates GPU resources and cancels any upload (03 7.10)."""
    runtime.release_all()


def register() -> None:
    global _draw_handle, _error_logged
    _error_logged = False
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')
    if _load_pre not in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.append(_load_pre)      # the add-on's one load_pre handler calls it
    runtime.on_view_change(_view_changed)


def unregister() -> None:
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    while _load_pre in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.remove(_load_pre)
    runtime.release_all()
    runtime.off_view_change(_view_changed)
