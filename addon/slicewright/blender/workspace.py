# SPDX-License-Identifier: GPL-3.0-or-later
"""The "Slicer" workspace tab (03 section 1.5): two operators, a menu entry and the panels that go with it.

Blender has no API to create a workspace (``bpy.data.workspaces`` has no ``new()``), so *Add Slicer workspace*
duplicates "Layout" (``workspace.duplicate``), renames and tags the copy, switches the window to it, joins its areas
into one and splits that into the layout of ``core/workspace_plan.py``. Area geometry only updates between redraws,
so the layout is built by a generator stepped from a timer (run to the end at once in background mode, where there is
no window to lay out).

Only a workspace carrying the tag (an ID property set at creation) is ever renamed, switched away from or removed.
Nothing here writes the startup file or the preferences: the workspace lives in the open .blend, and the owner can
choose *Save Startup File* themselves.
"""
from __future__ import annotations

import bpy

from ..core import workspace_plan as plan
from ..core.logs import get_logger
from ..names import PACKAGE_ID, TAB_NAME, panel_order
from . import registry

REQUIRES_ENGINE = True

log = get_logger()
CONTEXT = "world"                     # the Properties editor tab that holds the wide settings (see the class below)
PanelLabel = "Slicewright settings"
BUILT = "slicewright_built"      # second property: 1 once the layout is complete


# --------------------------------------------------------------------------- queries

def workspace_names() -> list[str]:
    return [w.name for w in bpy.data.workspaces]


def is_ours(ws) -> bool:
    """The add-on made this workspace (its tag is set)."""
    try:
        return ws.get(plan.TAG) == plan.TAG_VALUE
    except (AttributeError, TypeError):
        return False


def tagged_names() -> list[str]:
    return [w.name for w in bpy.data.workspaces if is_ours(w)]


def find() -> "bpy.types.WorkSpace | None":
    return next((w for w in bpy.data.workspaces if is_ours(w)), None)


# --------------------------------------------------------------------------- building

def _window(context):
    win = getattr(context, "window", None)
    if win is not None:
        return win
    wm = context.window_manager
    return wm.windows[0] if len(wm.windows) else None


def _region(area):
    return next((r for r in area.regions if r.type == "WINDOW"), area.regions[0])


def _rects(screen) -> list[tuple]:
    return [(a.x, a.y, a.width, a.height) for a in screen.areas]


def _find_area(screen, pointer):
    return next((a for a in screen.areas if a.as_pointer() == pointer), None)


def _build(win, ws):
    """Generator: switch ``win`` to ``ws``, reduce its areas to one, split into the layout, set the editor types.
    Yields seconds to wait (Blender needs redraws between steps)."""
    win.workspace = ws
    yield 0.1
    if win.workspace != ws:
        raise RuntimeError("the window did not switch to the new workspace")
    screen = win.screen
    # 1. one area
    for _ in range(12):
        areas = list(screen.areas)
        pairs = plan.join_plan([(a.x, a.y, a.width, a.height) for a in areas])
        if not pairs:
            break
        src, dst = areas[pairs[0][0]], areas[pairs[0][1]]
        with bpy.context.temp_override(window=win, screen=screen, area=src, region=_region(src)):
            bpy.ops.screen.area_join(source_xy=(src.x + 10, src.y + 10), target_xy=(dst.x + 10, dst.y + dst.height // 2))
        yield 0.05
    if len(screen.areas) != 1:
        raise RuntimeError(f"could not reduce the workspace to one area ({len(screen.areas)} left)")
    # 2. split
    pointers = {plan.ROOT: screen.areas[0].as_pointer()}
    for step in plan.LAYOUT:
        target = _find_area(screen, pointers[step.target])
        before = {a.as_pointer() for a in screen.areas}
        with bpy.context.temp_override(window=win, screen=screen, area=target, region=_region(target)):
            bpy.ops.screen.area_split(direction=step.direction, factor=step.factor)
        yield 0.1
        new = [a for a in screen.areas if a.as_pointer() not in before]
        if len(new) != 1:
            raise RuntimeError("a split did not make exactly one area")
        old, new = target, new[0]
        for _ in range(20):                       # the new area has no position until the next redraw
            if new.width > 0:
                break
            yield 0.05
        key = (lambda a: a.x) if step.direction == plan.VERTICAL else (lambda a: a.y)
        low, high = sorted((old, new), key=key)
        pointers[step.low], pointers[step.high] = low.as_pointer(), high.as_pointer()
    # 3. editors
    for key, kind in plan.AREA_TYPES.items():
        area = _find_area(screen, pointers[key])
        area.type = kind
    yield 0.1
    configure(win, screen, pointers)
    yield 0.1


def frame_plate(r3d, scene) -> None:
    """Look at the bed centre (world origin) from the front-left, far enough to see the whole bed."""
    import math
    from mathutils import Euler
    from . import bed_source
    bed = bed_source.current_bed(scene)
    extent = bed.extent_mm if bed is not None else 256.0
    per_mm = 1.0 / (scene.unit_settings.scale_length * 1000.0) if scene.unit_settings.scale_length > 0 else 1.0
    r3d.view_perspective = "PERSP"
    r3d.view_location = (0.0, 0.0, 0.0)
    r3d.view_rotation = Euler((math.radians(60), 0.0, math.radians(25)), "XYZ").to_quaternion()
    r3d.view_distance = extent * 1.7 * per_mm


def configure(win, screen, pointers) -> None:
    """Editor settings of the new areas: sidebar open in the viewport, the Scene tab in the Properties editor."""
    area = _find_area(screen, pointers["viewport"])
    area.spaces.active.show_region_ui = True
    frame_plate(area.spaces.active.region_3d, bpy.context.scene)
    props = _find_area(screen, pointers["settings"]).spaces.active
    props.context = CONTEXT.upper()


def _drive(gen, on_done=None) -> None:
    """Step ``gen`` from a timer (immediately to the end in background mode). Errors are logged, never raised."""
    def finish(exc=None):
        if exc is not None:
            log.error("Slicer workspace: %s", exc)
        if on_done:
            on_done(exc)

    if bpy.app.background:                    # no event loop, so no timer: run it to the end now
        try:
            for _ in gen:
                pass
        except Exception as exc:  # noqa: BLE001
            finish(exc)
        else:
            finish()
        return

    def tick():
        try:
            return next(gen)
        except StopIteration:
            finish()
        except Exception as exc:  # noqa: BLE001
            finish(exc)
        return None

    bpy.app.timers.register(tick, first_interval=0.05)


def _tag(ws) -> None:
    ws[plan.TAG] = plan.TAG_VALUE


def create(context) -> "tuple[bpy.types.WorkSpace | None, str]":
    """The synchronous part of *Add*: duplicate, rename, tag. Returns (workspace, problem)."""
    win = _window(context)
    names, tagged = workspace_names(), tagged_names()
    decision = plan.add_decision(names, tagged)
    if decision.action == plan.REFUSE:
        return None, decision.message
    if decision.action == plan.ACTIVATE:
        return find(), ""
    base = plan.base_workspace(names, tagged, context.workspace.name if context.workspace else "")
    if base not in names or win is None:
        return None, "No workspace to copy from"
    source = bpy.data.workspaces[base]
    before = set(names)
    win_ws = win.workspace
    if win_ws != source and not bpy.app.background:
        win.workspace = source            # workspace.duplicate copies the window's workspace
    with bpy.context.temp_override(window=win, workspace=source, screen=source.screens[0]):
        result = bpy.ops.workspace.duplicate()
    if result != {"FINISHED"}:
        return None, "Blender could not duplicate the workspace"
    new = [w for w in bpy.data.workspaces if w.name not in before]
    if len(new) != 1:
        return None, "Blender made no new workspace"
    ws = new[0]
    ws.name = plan.NAME
    _tag(ws)
    if win_ws != source and not bpy.app.background:
        win.workspace = win_ws
    return ws, ""


def remove(context, on_done=None) -> int:
    """Remove the add-on's workspace(s). Returns how many were (or will be) removed. Never touches other workspaces."""
    targets = plan.remove_targets(workspace_names(), tagged_names())
    if not targets:
        return 0
    win = _window(context)
    names = workspace_names()

    def run():
        for name in targets:
            ws = bpy.data.workspaces.get(name)
            if ws is None or not is_ours(ws):
                continue
            if win is not None and not bpy.app.background:
                win.workspace = ws                    # workspace.delete removes the window's workspace and moves it on
                yield 0.1
                with bpy.context.temp_override(window=win, workspace=ws, screen=win.screen):
                    bpy.ops.workspace.delete()
                yield 0.1
            else:
                with bpy.context.temp_override(window=win, workspace=ws, screen=ws.screens[0]):
                    bpy.ops.workspace.delete()
        yield 0.0

    _drive(run(), on_done)
    return len(targets)


# --------------------------------------------------------------------------- operators

class SLICEWRIGHT_OT_workspace_add(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.workspace_add"
    bl_label = "Add Slicer Workspace"
    bl_description = ("Add a \"Slicer\" workspace tab (viewport, outliner, wide settings, Info) to this file and switch to it. "
                      "An existing workspace of your own is never changed")

    def execute(self, context):
        ws, problem = create(context)
        if ws is None:
            self.report({"WARNING"}, problem)
            return {"CANCELLED"}
        win = _window(context)
        if ws.get(BUILT) is not None:             # ours from before (built, or a build that stopped): just go there
            win.workspace = ws
            return {"FINISHED"}
        if bpy.app.background:                    # no window to lay out: the tagged copy of Layout is all there is
            return {"FINISHED"}
        ws[BUILT] = 0                             # set to 1 when the layout is complete

        def done(exc):
            if exc is None and bpy.data.workspaces.get(ws.name) is not None:
                ws[BUILT] = 1
        _drive(_build(win, ws), done)
        self.report({"INFO"}, "Slicer workspace added")
        return {"FINISHED"}


class SLICEWRIGHT_OT_workspace_remove(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.workspace_remove"
    bl_label = "Remove Slicer Workspace"
    bl_description = "Remove the Slicer workspace that Slicewright added. Workspaces you made yourself are never removed"

    @classmethod
    def poll(cls, context):
        return find() is not None

    def execute(self, context):
        remove(context)
        return {"FINISHED"}


# --------------------------------------------------------------------------- UI

def draw_buttons(layout) -> None:
    ours = find() is not None
    row = layout.row(align=True)
    row.operator(f"{PACKAGE_ID}.workspace_add", text="Go to Slicer Workspace" if ours else "Add Slicer Workspace",
                 icon="WORKSPACE")
    if ours:
        row.operator(f"{PACKAGE_ID}.workspace_remove", text="", icon="TRASH")


def draw_menu(self, context) -> None:
    self.layout.separator()
    draw_buttons(self.layout)


class SLICEWRIGHT_PT_workspace(bpy.types.Panel):
    bl_idname = "SLICEWRIGHT_PT_workspace"
    bl_label = "Workspace"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB_NAME
    bl_order = panel_order("workspace")
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        from .labels import draw_wrapped
        draw_buttons(self.layout)
        draw_wrapped(self.layout, plan.HELP, "INFO")


class SLICEWRIGHT_PT_settings_wide(bpy.types.Panel):
    """The settings panel in the Properties editor's Scene tab, in the Slicer workspace only (a custom Properties
    tab cannot be registered, so it is a panel of the Scene tab). The region width decides how many columns it uses."""
    bl_idname = "SLICEWRIGHT_PT_settings_wide"
    bl_label = PanelLabel
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = CONTEXT

    @classmethod
    def poll(cls, context):
        from . import library
        ws = context.workspace
        return ws is not None and is_ours(ws) and library.get() is not None

    def draw(self, context):
        from .. import prefs
        from .ui import settings
        settings.draw_settings(self.layout, context.scene.slicewright, prefs.get())


classes = (SLICEWRIGHT_OT_workspace_add, SLICEWRIGHT_OT_workspace_remove, SLICEWRIGHT_PT_workspace,
           SLICEWRIGHT_PT_settings_wide)
MENUS = ("TOPBAR_MT_window", "TOPBAR_MT_workspace_menu")


def register() -> None:
    registry.register_classes(classes)
    for name in MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None and draw_menu not in getattr(menu.draw, "_draw_funcs", ()):
            menu.append(draw_menu)


def unregister() -> None:
    for name in MENUS:
        menu = getattr(bpy.types, name, None)
        while menu is not None and draw_menu in getattr(menu.draw, "_draw_funcs", ()):
            menu.remove(draw_menu)
    registry.unregister_classes(classes)
