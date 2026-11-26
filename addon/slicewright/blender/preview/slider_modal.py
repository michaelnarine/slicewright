# SPDX-License-Identifier: GPL-3.0-or-later
"""Mouse handling of the Preview sliders (03 section 7.4a): one passive modal operator per 3D view in Preview.

Why a modal operator and not keymap items: a modal handler sits in front of the area's keymaps and of the active tool's
keymap, so a press on a slider is ours before Select Box or Tweak sees it, while everything else returns
``PASS_THROUGH`` and goes on to the viewport untouched (selection, navigation, tools, the arrow-key preview steps).
Only a press on a slider part, the mouse wheel over one, and the events of a drag in progress are swallowed.

Lifetime, so nothing leaks:

* a persistent timer (``ensure``, every 0.4 s) starts one modal per 3D view that shows sliders (``sliders.shown``);
* the modal ends itself at its next event when its view no longer shows sliders (left Preview, option off, the preview
  cleared), when the add-on was unregistered or reloaded (a generation number), or when its area is gone;
* Blender itself drops it when the area or window closes or a file is loaded; ``cancel`` and the periodic ``prune``
  keep our bookkeeping in step, and ``load_pre`` clears it;
* unregistering the class makes Blender remove any running handler of it.
"""
from __future__ import annotations

import logging

import bpy

from ...core import preview_slider as ps
from ...names import PACKAGE_ID
from .. import registry
from . import runtime, sliders

log = logging.getLogger("slicewright.preview")

ENSURE_INTERVAL = 0.4
_running: set[tuple[int, int]] = set()        # (window, area) pointers that have our modal
_generation = 0                               # bumped by unregister: older modals end at their next event
_ensure_error_logged = False


def running() -> set:
    return set(_running)


def _key(window, area) -> tuple[int, int]:
    return (window.as_pointer(), area.as_pointer())


def _live_keys() -> set:
    wm = bpy.context.window_manager
    return {_key(w, a) for w in wm.windows for a in w.screen.areas if a.type == "VIEW_3D"}


def prune() -> None:
    """Forget bookkeeping for views that no longer exist (an area closed, a file loaded)."""
    _running.intersection_update(_live_keys())


def _wanted(window, area) -> bool:
    space = next((sp for sp in area.spaces if sp.type == "VIEW_3D"), None)
    return space is not None and sliders.shown(window.scene, space)


def ensure() -> float:
    """Timer: start the modal in every 3D view that should have one. Returns the next interval."""
    global _ensure_error_logged
    try:
        wm = bpy.context.window_manager
        prune()
        for win in wm.windows:
            for area in win.screen.areas:
                if area.type != "VIEW_3D" or _key(win, area) in _running or not _wanted(win, area):
                    continue
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is None:
                    continue
                with bpy.context.temp_override(window=win, area=area, region=region):
                    bpy.ops.slicewright.layer_slider("INVOKE_DEFAULT")
    except Exception:  # noqa: BLE001 - a timer must never raise
        if not _ensure_error_logged:
            _ensure_error_logged = True
            log.exception("slider modal start failed (reported once)")
    return ENSURE_INTERVAL


def reset() -> None:
    """``load_pre``: every window and area of the old file is going away."""
    _running.clear()
    sliders.ui.reset()


class SLICEWRIGHT_OT_layer_slider(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.layer_slider"
    bl_label = "Preview sliders"
    bl_description = "Mouse handling of the Preview's layer and move sliders (started by itself in Preview)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == "VIEW_3D" and context.region is not None
                and context.region.type == "WINDOW" and context.window is not None)

    def invoke(self, context, event):
        key = _key(context.window, context.area)
        if key in _running:
            return {'CANCELLED', 'PASS_THROUGH'}
        self._key, self._gen = key, _generation
        _running.add(key)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    # -- ending ---------------------------------------------------------------------------------------------------

    def _end(self, context=None) -> None:
        _running.discard(self._key)
        if sliders.ui.area == self._key[1]:
            sliders.ui.reset()
            runtime.tag_redraw()

    def cancel(self, context):
        self._end(context)

    def _alive(self, context) -> bool:
        if self._gen != _generation or context.area is None or context.area.type != "VIEW_3D":
            return False
        return sliders.view_state(context) is not None

    # -- events ---------------------------------------------------------------------------------------------------

    def modal(self, context, event):
        ui = sliders.ui
        if not self._alive(context):
            self._end(context)
            return {'CANCELLED', 'PASS_THROUGH'}
        view = sliders.view_state(context)
        area_ptr = self._key[1]
        x, y = event.mouse_region_x, event.mouse_region_y
        region = context.region
        inside = 0 <= x < region.width and 0 <= y < region.height
        follow = bool(view.props.slider_follow)
        g, n, last = view.geometry, view.n, view.move_last

        if ui.drag is not None and ui.area == area_ptr:
            return self._dragging(context, event, view, x, y, follow)

        hit = ps.hit_test(g, x, y, view.scrub, n, last) if inside else None
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            if (ui.hover, ui.area) != (hit, area_ptr if hit else 0):
                ui.hover, ui.area = hit, (area_ptr if hit else 0)
                context.area.tag_redraw()
            return {'PASS_THROUGH'}
        if hit is None:
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.value in {'PRESS', 'DOUBLE_CLICK'}:
            drag, scrub = ps.begin_drag(g, hit, x, y, view.scrub, n, last, follow)
            if drag is None:
                return {'PASS_THROUGH'}
            ui.area, ui.drag, ui.hover = area_ptr, drag, hit
            sliders.write(view, scrub)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} and not (event.ctrl or event.alt or event.oskey):
            sign = 1 if event.type == 'WHEELUPMOUSE' else -1
            if hit in ps.LAYER_KINDS:
                which = ps.nearest_thumb(g, y, view.scrub, n)
                scrub = ps.step_layers(view.scrub, which, sign * ps.step_amount(event.shift), n, follow)
            else:
                scrub = ps.step_in_layer(view.scrub, sign * ps.step_amount(event.shift, "move"), last)
            sliders.write(view, scrub)
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def _dragging(self, context, event, view, x, y, follow):
        ui = sliders.ui
        drag = ui.drag
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            sliders.write(view, drag.start)
            self._finish_drag(context)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._finish_drag(context)
            return {'RUNNING_MODAL'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            sliders.write(view, ps.drag_to(view.geometry, drag, x, y, view.scrub, view.n, view.move_last, follow))
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def _finish_drag(self, context) -> None:
        sliders.ui.drag = None
        sliders.ui.hover = None
        context.area.tag_redraw()


classes = (SLICEWRIGHT_OT_layer_slider,)


def register() -> None:
    global _ensure_error_logged
    _ensure_error_logged = False
    _running.clear()
    if reset not in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.append(reset)
    registry.register_timer(ensure, first_interval=0.5)


def unregister() -> None:
    global _generation
    _generation += 1
    if bpy.app.timers.is_registered(ensure):
        bpy.app.timers.unregister(ensure)
    while reset in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.remove(reset)
    _running.clear()
    sliders.ui.reset()
