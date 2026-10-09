# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings pages in the N-panel "Slicer" tab (03 section 2.4).

Printer and process settings edit the scene's edit buffers; filament settings edit the active slot's
*overrides*, where an unset key shows the preset's value next to an "Override" button. The drawing
functions only call ``UILayout`` methods, so tests drive them with a recording layout.
"""
from __future__ import annotations

from typing import Any, Callable

import bpy

from ...core import config_codec as cc
from ...core import settings_layout as sl
from ...core.config_codec import split_vector
from ...names import PACKAGE_ID, TAB_NAME
from .. import config_pg, library, registry

ROLE_ITEMS = [("printer", "Printer", "Machine settings"), ("process", "Process", "Print settings"),
              ("filament", "Filament", "Material settings of the active slot")]
_pages: dict[str, list[sl.Page]] = {}
_page_items: list[tuple[str, str, str]] = []      # kept alive: Blender holds raw pointers to the strings

# Hook for the show/enable rules (layer 7): (flat config, key) -> (visible, enabled).
RowState = Callable[[str], tuple[bool, bool]]


def _always(_key: str) -> tuple[bool, bool]:
    return True, True


def pages_for(role: str) -> list[sl.Page]:
    """Pages of ``role``, built once per registration from the engine layout and schema."""
    if role not in _pages:
        sc = registry.state.status.module
        _pages[role] = sl.build_pages(role, sc.tab_layout(), config_pg.schema())
    return _pages[role]


def clear() -> None:
    _pages.clear()


def page_items(role: str) -> list[tuple[str, str, str]]:
    found = [(p.name, p.name, "") for p in pages_for(role)] if config_pg.specs() else []
    _page_items[:] = found or [("", "-", "")]
    return _page_items


# -- the buffer a role edits ---------------------------------------------------------------------

def active_slot(pg):
    slots = pg.filaments
    return slots[min(max(pg.settings_slot, 0), len(slots) - 1)] if len(slots) else None


def buffer_for(pg, role: str):
    """``(config PG, is_override_buffer, preset id)`` for ``role``; the PG is ``None`` without a target."""
    if role == "printer":
        return pg.printer_edits, False, pg.printer_id
    if role == "process":
        return pg.process_edits, False, pg.process_id
    slot = active_slot(pg)
    return (slot.overrides if slot else None), True, (slot.preset_id if slot else "")


def preset_value(role: str, preset_id: str, key: str) -> str:
    """The preset's own value for ``key`` as text, for the "inherited" display of an unset override."""
    lib = library.get()
    kind = {"printer": "machine", "process": "process", "filament": "filament"}[role]
    if lib is None or not preset_id:
        return ""
    try:
        value = lib.resolver.resolve(kind, preset_id).config.get(key)
    except Exception:  # noqa: BLE001 - a broken preset just shows no inherited value
        return ""
    spec = config_pg.specs().get(key)
    if value is None:
        return "" if spec is None else cc.format_value(spec, spec.default)
    return cc.flatten(spec.schema_type if spec else "", value)


# -- drawing -------------------------------------------------------------------------------------

def draw_key(layout, cfg, key: str, *, override: bool, inherited: str = "",
             state: tuple[bool, bool] = (True, True)) -> None:
    """One setting row. In an override buffer an unset key shows the inherited value and "Override"."""
    visible, enabled = state
    if not visible:
        return
    spec = config_pg.specs()[key]
    row = layout.row(align=True)
    row.enabled = enabled
    if override and not cfg.is_property_set(key):
        row.label(text=f"{spec.label}: {inherited}")
        op = row.operator(f"{PACKAGE_ID}.settings_override", text="Override")
        op.key, op.action = key, "SET"
        return
    row.prop(cfg, key, text=spec.label)
    if override:
        op = row.operator(f"{PACKAGE_ID}.settings_override", text="", icon="LOOP_BACK")
        op.key, op.action = key, "RESET"


def draw_bed_shape(layout, cfg, keys: list[str], **kw) -> None:
    bounds = sl.bed_bounds(getattr(cfg, "printable_area", ""))
    if bounds:
        layout.label(text=f"Bed {bounds[0]:g} x {bounds[1]:g} mm", icon="MESH_PLANE")
    for key in keys:
        draw_key(layout, cfg, key, **kw)


def draw_extruder_count(layout, cfg, keys: list[str], **kw) -> None:
    count = len(split_vector("floats", getattr(cfg, "nozzle_diameter", ""))) or 1
    layout.label(text=f"Extruders: {count}", icon="OUTLINER_OB_MESH")
    for key in keys:
        draw_key(layout, cfg, key, **kw)


CUSTOM_DRAWERS = {"bed_shape": draw_bed_shape, "extruder_count": draw_extruder_count}


def draw_page(layout, cfg, role: str, page: sl.Page, *, level: str, text: str, develop: bool,
              override: bool, preset_id: str = "", state: RowState = _always) -> int:
    """Draw ``page``; returns the number of rows (keys) drawn."""
    schema = config_pg.schema()
    drawn = 0
    for group, keys in sl.visible_groups(page, schema, level, text, develop):
        box = layout.box()
        box.label(text=group.title or page.name)
        kw = {"override": override}
        drawer = CUSTOM_DRAWERS.get(group.custom or "")
        if drawer is not None:
            drawer(box, cfg, keys, **kw, state=(True, True))
            drawn += len(keys)
            continue
        for key in keys:
            draw_key(box, cfg, key, override=override,
                     inherited=preset_value(role, preset_id, key) if override else "",
                     state=state(key))
            drawn += 1
    return drawn


def draw_settings(layout, pg, prefs: Any) -> None:
    """The whole settings panel body."""
    layout.prop(pg, "settings_role", expand=True)
    cfg, override, preset_id = buffer_for(pg, pg.settings_role)
    if pg.settings_role == "filament" and len(pg.filaments) > 1:
        layout.prop(pg, "settings_slot", text="Slot")
    if cfg is None or not preset_id:
        layout.label(text="Choose a preset first", icon="INFO")
        return
    row = layout.row(align=True)
    row.prop(pg, "settings_page", text="")
    row.prop(pg, "settings_filter", text="", icon="VIEWZOOM")
    page = sl.find_page(pages_for(pg.settings_role), pg.settings_page)
    if page is None:
        layout.label(text="No settings for this role", icon="INFO")
        return
    drawn = draw_page(layout, cfg, pg.settings_role, page, level=prefs.settings_mode,
                      text=pg.settings_filter, develop=prefs.show_develop, override=override,
                      preset_id=preset_id)
    if not drawn and pg.settings_filter.strip():
        layout.label(text="No settings match the filter on this page", icon="INFO")


class SLICEWRIGHT_PT_settings(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_settings"
    bl_label = "Settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME

    @classmethod
    def poll(cls, context):
        return library.get() is not None

    def draw(self, context):
        from ... import prefs
        draw_settings(self.layout, context.scene.slicewright, prefs.get())
