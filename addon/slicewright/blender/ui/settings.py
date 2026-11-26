# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings pages in the N-panel "Slicer" tab (03 section 2.4).

Printer and process settings edit the scene's edit buffers; filament settings edit the active slot's
*overrides*, where an unset key shows the preset's value next to an "Override" button. The drawing
functions only call ``UILayout`` methods, so tests drive them with a recording layout.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import bpy

from ...core import choice_layout as cl
from ...core import config_codec as cc
from ...core import settings_layout as sl
from ...core import settings_view as sv
from ...core import text as core_text
from ...core.config_codec import split_vector
from ...names import PACKAGE_ID, TAB_NAME, panel_order
from ..labels import draw_wrapped
from .. import config_pg, library, registry, settings_rules, user_presets
from . import choice

ROLE_ITEMS = [("printer", "Printer", "Machine settings"), ("process", "Process", "Print settings"),
              ("filament", "Filament", "Material settings of the active slot")]
WM_OPEN = f"{PACKAGE_ID}_settings_open"       # WindowManager: ids of the open sections and groups (per session, not saved)
_pages: dict[str, list[sl.Page]] = {}
_visible: dict[tuple, dict[str, list]] = {}      # (role, level, develop) -> page name -> visible groups; schema-only data
_index: list = []                                # [SearchIndex] for the current schema
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
    _visible.clear()
    _index.clear()


def visible_for(role: str, level: str, develop: bool) -> dict[str, list]:
    """Every page's groups and keys that pass the detail level, built once per (role, level) and kept."""
    cache_key = (role, level, develop)
    found = _visible.get(cache_key)
    if found is None:
        schema = config_pg.schema()
        found = {p.name: sv.visible_keys(p, schema, level, develop) for p in pages_for(role)}
        _visible[cache_key] = found
    return found


def search_index() -> sv.SearchIndex:
    if not _index:
        _index.append(sv.SearchIndex(config_pg.schema()))
    return _index[0]


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
    resolved = lib.resolve(kind, preset_id) if lib is not None and preset_id else None
    if resolved is None:
        return ""
    value = resolved.config.get(key)
    spec = config_pg.specs().get(key)
    if value is None:
        return "" if spec is None else cc.format_value(spec, spec.default)
    return cc.flatten(spec.schema_type if spec else "", value)


def display_value(key: str, text: str) -> str:
    """``text`` (a stored value) as the user sees it: an enum's option label instead of its internal value."""
    spec = config_pg.specs().get(key)
    if spec is not None and spec.kind == cc.ENUM:
        for value, label, _tip in spec.enum_items:
            if value == text:
                return label
    return text


# -- drawing -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Look:
    """What a row needs to know about the room it gets (all pure data, from ``core.settings_view``)."""
    cell: float = sv.cell_chars(cl.DEFAULT_WIDTH_CHARS, 1)       # characters across one row, buttons excluded
    seg_width: float = sv.choice_width(cl.DEFAULT_WIDTH_CHARS, 1)  # the width the segmented-choice rule assumes
    pinned: frozenset = frozenset()
    modified: frozenset = frozenset()


DEFAULT_LOOK = Look()


def _star(row, key: str, pinned: bool) -> None:
    op = row.operator(f"{PACKAGE_ID}.settings_favourite", text="", icon="SOLO_ON" if pinned else "SOLO_OFF",
                      emboss=False)
    op.key = key


def draw_key(layout, cfg, key: str, *, override: bool, inherited: str = "",
             state: tuple[bool, bool] = (True, True), look: Look = DEFAULT_LOOK) -> None:
    """One setting as one cell of its layout: ``[star] label | value [reset]``. The label sits left of the value when it
    fits and above it when it does not (``sv.row_style``); a short choice is a row of buttons under its label. In an
    override buffer an unset key shows the inherited value and "Override"."""
    visible, enabled = state
    if not visible:
        return
    spec = config_pg.specs()[key]
    cell = layout.column(align=True)
    cell.enabled = enabled
    pinned = key in look.pinned
    if override and not cfg.is_property_set(key):
        head = cell.row(align=True)
        _star(head, key, pinned)
        head.label(text=spec.label)
        line = cell.row(align=True)
        line.label(text=display_value(key, inherited) or "(empty)", icon="BLANK1")
        op = line.operator(f"{PACKAGE_ID}.settings_override", text="Override")
        op.key, op.action = key, "SET"
        return
    changed = override or key in look.modified          # a set override is a change by definition

    def trailing(r):
        if changed:
            op = r.operator(f"{PACKAGE_ID}.settings_override", text="", icon="LOOP_BACK")
            op.key, op.action = key, "RESET"
        else:
            r.label(text="", icon="BLANK1")                  # keeps the value column aligned between rows
    if choice.style_of(cfg, key, look.seg_width) == "segmented":
        choice.draw_segmented(cell, cfg, key, spec.label, header=trailing, lead=lambda r: _star(r, key, pinned))
        return
    row = cell.row(align=True)
    _star(row, key, pinned)
    body = row.column(align=True)
    prop = cfg.bl_rna.properties.get(key)
    style = sv.row_style(spec.label, prop is not None and prop.type == "BOOLEAN", look.cell)
    if style.kind == sv.NATIVE:
        body.use_property_split = True
        body.use_property_decorate = False
        body.prop(cfg, key, text=spec.label)
    elif style.kind == sv.INLINE:
        body.prop(cfg, key, text=spec.label)
    elif style.kind == sv.SPLIT:
        split = body.split(factor=style.factor, align=True)
        split.label(text=spec.label)
        split.prop(cfg, key, text="")
    else:
        draw_wrapped(body, spec.label)
        body.prop(cfg, key, text="")
    trailing(row)


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


def rule_state(cfg, role: str, override: bool, preset_id: str) -> RowState:
    """Show/enable rules evaluated over the buffer; an unset override reads the preset's own value."""
    specs = config_pg.specs()

    def get(key: str) -> str | None:
        spec = specs.get(key)
        if spec is None:
            return None
        if override and not cfg.is_property_set(key):
            return preset_value(role, preset_id, key)
        return cc.format_value(spec, getattr(cfg, key))
    return settings_rules.row_state(settings_rules.Cfg(get))


class RoleContext:
    """The buffer one role edits, with what is derived from it once per redraw (rules, modified keys)."""

    def __init__(self, pg, role: str):
        self.pg, self.role = pg, role
        self.cfg, self.override, self.preset_id = buffer_for(pg, role)
        self.ok = self.cfg is not None and bool(self.preset_id)
        self._state: RowState | None = None
        self._rows: list | None = None

    @property
    def state(self) -> RowState:
        if self._state is None:
            self._state = rule_state(self.cfg, self.role, self.override, self.preset_id) if self.ok else _always
        return self._state

    @property
    def rows(self) -> list:
        """The unsaved edits against the preset, ``(key, label, preset text, edited text)``."""
        if self._rows is None:
            self._rows = user_presets.changes(self.pg, self.role) if self.ok else []
        return self._rows

    @property
    def modified(self) -> frozenset:
        return frozenset(r[0] for r in self.rows)


class View:
    """One redraw of the Settings panel: the width, the open sections, the search words, the pinned keys."""

    def __init__(self, pg, prefs: Any, width_chars: float | None = None):
        self.pg, self.prefs = pg, prefs
        self.level, self.develop = prefs.settings_mode, prefs.show_develop
        self.text = pg.settings_filter.strip()
        self.searching = bool(self.text)
        width = choice.available_chars() if width_chars is None else width_chars
        self.width = width
        self.columns = sv.columns_for(width)
        self.cell = sv.cell_chars(width, self.columns)
        self.seg_width = sv.choice_width(width, self.columns)
        wm = getattr(bpy.context, "window_manager", None)
        self.open_text = getattr(wm, WM_OPEN, "") if wm is not None else ""
        self.pinned_text = pg.favourites
        self.roles: dict[str, RoleContext] = {}

    def role(self, role: str) -> RoleContext:
        found = self.roles.get(role)
        if found is None:
            found = self.roles[role] = RoleContext(self.pg, role)
        return found

    def is_open(self, ident: str) -> bool:
        return self.searching or sv.contains(self.open_text, ident)

    def look(self, ctx: RoleContext) -> Look:
        return Look(self.cell, self.seg_width, frozenset(sv.parse_set(self.pinned_text)), ctx.modified)


def draw_header(layout, view: View, ident: str, title: str, summary: str, is_open: bool, *, flagged: bool = False,
                nested: bool = False) -> None:
    """A collapsible header: the triangle and title (left) toggle ``ident``, the summary sits at the right. A title too
    long for one line is wrapped beside a triangle button instead of being cut. ``nested`` is a group inside a section
    (indented, a smaller triangle)."""
    if nested:
        icon = "DISCLOSURE_TRI_DOWN" if is_open else "DISCLOSURE_TRI_RIGHT"
    else:
        icon = "TRIA_DOWN" if is_open else "TRIA_RIGHT"
    mark = "DOT" if flagged else "NONE"
    row = layout.row(align=False)
    if nested:
        row.separator(factor=1.0)
    fit = view.width - 8 - len(summary) - (3 if flagged else 0) - (2 if nested else 0)
    if len(title) <= fit:
        left = row.row(align=True)
        left.alignment = "LEFT"
        op = left.operator(f"{PACKAGE_ID}.settings_section", text=title, icon=icon, emboss=False)
        op.section = ident
        right = row.row(align=True)
        right.alignment = "RIGHT"
        right.label(text=summary, icon=mark)
        return
    op = row.operator(f"{PACKAGE_ID}.settings_section", text="", icon=icon, emboss=False)
    op.section = ident
    col = row.column(align=True)
    for line in core_text.wrap(title, max(int(view.width - 12), 10)):
        col.label(text=line)
    col.label(text=summary, icon=mark)


def _rows_layout(layout, view: View):
    if view.columns > 1:
        return layout.grid_flow(row_major=True, columns=view.columns, even_columns=True, even_rows=False, align=False)
    return layout.column(align=False)


def draw_rows(layout, view: View, ctx: RoleContext, group: sl.Group, keys: list[str]) -> int:
    """The rows of one group (its custom drawer first, if it has one); returns how many settings were drawn."""
    look = view.look(ctx)
    drawer = CUSTOM_DRAWERS.get(group.custom or "")
    if drawer is not None:
        drawer(layout, ctx.cfg, keys, override=ctx.override, state=(True, True), look=look)
        return len(keys)
    grid = _rows_layout(layout, view)
    for key in keys:
        draw_key(grid, ctx.cfg, key, override=ctx.override,
                 inherited=preset_value(ctx.role, ctx.preset_id, key) if ctx.override else "",
                 state=ctx.state(key), look=look)
    return len(keys)


def draw_section(layout, view: View, ctx: RoleContext, page: sl.Page, plan: sv.PagePlan) -> int:
    """One page as a boxed collapsible section; its groups are collapsible too. Closed sections draw only the header."""
    sid = sv.section_id(ctx.role, page.name)
    is_open = view.is_open(sid)
    box = layout.box()
    draw_header(box, view, sid, page.name, sv.summary(plan.count, plan.modified, view.searching), is_open,
                flagged=bool(plan.modified))
    if not is_open:
        return 0
    drawn = 0
    box.separator(factor=0.3)
    if plan.flat and plan.groups:
        return drawn + draw_rows(box, view, ctx, plan.groups[0].group, plan.groups[0].keys)
    for n, gp in enumerate(plan.groups):
        if not gp.keys and CUSTOM_DRAWERS.get(gp.group.custom or "") is None:
            continue
        gid = sv.group_id(ctx.role, page.name, gp.index)
        group_open = view.is_open(gid)
        draw_header(box, view, gid, gp.group.title or page.name,
                    sv.summary(gp.count, gp.modified, view.searching), group_open, flagged=bool(gp.modified),
                    nested=True)
        if group_open:
            drawn += draw_rows(box, view, ctx, gp.group, gp.keys)
    return drawn


def draw_favourites(layout, view: View) -> int:
    """The pinned settings of every role, with the same controls as their pages. Returns how many rows were drawn."""
    schema = config_pg.schema()
    keys = sv.pinned(view.pinned_text, schema)
    if view.searching:
        index, words = search_index(), sv.words_of(view.text)
        keys = [k for k in keys if index.match(k, words)]
        if not keys:
            return 0
    by_role: dict[str, list[str]] = {}
    for key in keys:
        role = sl.role_of_key(schema, key)
        if role is not None:
            by_role.setdefault(role, []).append(key)
    modified = sum(1 for role, ks in by_role.items() if view.role(role).ok
                   for k in ks if k in view.role(role).modified)
    box = layout.box()
    is_open = view.is_open(sv.FAVOURITES_ID)
    draw_header(box, view, sv.FAVOURITES_ID, "Favourites", sv.summary(len(keys), modified, view.searching), is_open,
                flagged=bool(modified))
    if not is_open:
        return 0
    box.separator(factor=0.3)
    if not keys:
        draw_wrapped(box, "Star a setting to pin it here.", "SOLO_OFF")
        return 0
    drawn = 0
    for role in sl.ROLES:
        ks = by_role.get(role)
        if not ks:
            continue
        ctx = view.role(role)
        if len(by_role) > 1:
            head = box.row()
            head.active = False
            head.label(text=role.title())
        if not ctx.ok:
            box.label(text="No preset chosen" if role != "filament" else "No filament slot", icon="INFO")
            continue
        look = view.look(ctx)
        grid = _rows_layout(box, view)
        for key in ks:
            draw_key(grid, ctx.cfg, key, override=ctx.override,
                     inherited=preset_value(role, ctx.preset_id, key) if ctx.override else "",
                     state=ctx.state(key), look=look)
            drawn += 1
    return drawn


def draw_preset_bar(layout, pg, role: str, preset_id: str, rows: list | None = None) -> None:
    """Which preset is edited, how many unsaved changes it has, and what can be done about them."""
    lib = library.get()
    box = layout.box()
    name = user_presets.name_of(preset_id)
    is_user = preset_id.startswith("user:")
    kind = user_presets.KIND_OF_ROLE[role]
    missing = lib is not None and lib.resolve(kind, preset_id) is None
    row = box.row(align=True)
    row.label(text=name, icon="USER" if is_user else "PRESET")
    if missing:
        box.label(text="Preset not found here; showing the copy embedded in the file", icon="ERROR")
        op = box.operator(f"{PACKAGE_ID}.preset_restore_embedded", icon="FILE_TICK")
        op.role = role
        return
    if rows is None:
        rows = user_presets.changes(pg, role)
    if is_user:
        for action, icon in (("DUPLICATE", "DUPLICATE"), ("RENAME", "GREASEPENCIL"), ("DELETE", "TRASH")):
            op = row.operator(f"{PACKAGE_ID}.preset_manage", text="", icon=icon)
            op.role, op.action = role, action
    if rows:
        box.label(text=f"{len(rows)} unsaved change{'s' if len(rows) != 1 else ''}", icon="DOT")
    line = box.row(align=True)
    op = line.operator(f"{PACKAGE_ID}.preset_save", text="Save", icon="FILE_TICK")
    op.role = role
    op = line.operator(f"{PACKAGE_ID}.preset_diff", text="Changes", icon="VIEWZOOM")
    op.role = role
    op = line.operator(f"{PACKAGE_ID}.preset_revert", text="Revert", icon="LOOP_BACK")
    op.role = role


def draw_settings(layout, pg, prefs: Any, width_chars: float | None = None) -> None:
    """The whole settings panel body: role, preset, search, the Favourites section and one section per page.
    ``width_chars`` overrides the region width (tests, a wider area)."""
    layout.prop(pg, "settings_role", expand=True)
    view = View(pg, prefs, width_chars)
    ctx = view.role(pg.settings_role)
    if pg.settings_role == "filament" and len(pg.filaments) > 1:
        layout.prop(pg, "settings_slot", text="Slot")
    if not ctx.ok:
        layout.label(text="Choose a preset first", icon="INFO")
        return
    draw_preset_bar(layout, pg, pg.settings_role, ctx.preset_id, ctx.rows)
    layout.separator(factor=0.4)
    row = layout.row(align=True)
    row.prop(pg, "settings_filter", text="", icon="VIEWZOOM")
    if view.searching:
        row.operator(f"{PACKAGE_ID}.settings_filter_clear", text="", icon="X")
    else:
        op = row.operator(f"{PACKAGE_ID}.settings_section", text="", icon="COLLAPSEMENU")
        op.section = ""
    layout.separator(factor=0.6)
    drawn = draw_favourites(layout, view)
    pages = pages_for(pg.settings_role)
    if not pages:
        layout.label(text="No settings for this role", icon="INFO")
        return
    visible = visible_for(pg.settings_role, view.level, view.develop)
    modified = ctx.modified
    index = search_index() if view.searching else None
    for page in pages:
        plan = sv.plan_page(page, visible[page.name], modified, index, view.text)
        if not plan.groups:
            continue
        layout.separator(factor=0.25)
        draw_section(layout, view, ctx, page, plan)
        drawn += plan.count
    if view.searching and not drawn:
        layout.label(text=f"No settings match '{view.text}'", icon="INFO")


class SLICEWRIGHT_PT_settings(bpy.types.Panel):
    bl_idname = f"{PACKAGE_ID.upper()}_PT_settings"
    bl_label = "Settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME
    bl_order = panel_order("settings")
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return library.get() is not None

    def draw(self, context):
        from ... import prefs
        draw_settings(self.layout, context.scene.slicewright, prefs.get())
