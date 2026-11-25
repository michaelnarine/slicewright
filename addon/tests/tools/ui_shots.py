# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-side driver of the UI screenshot tool (see ``README.md`` next to it; start it with ``ui_shots.py``'s launcher
``run_ui_shots.py``, not by hand).

Runs inside a REAL (non-background) Blender window, drives the add-on through its main UI states from one timer and
writes a PNG of the actual window per step plus a cropped ``*_sidebar.png`` of the Slicer sidebar, and ``manifest.json``.

    Blender <scratch.blend> --python ui_shots.py -- --out DIR [--only a,b] [--engine real|fake] [--scale 1.0]

Safety: everything it writes goes to ``--out`` or to the scratch profile the launcher set (``BLENDER_USER_RESOURCES``).
Preferences edits are in memory only (``use_preferences_save`` is switched off). The one printer it creates is fake
(host 192.0.2.1, secret "EXAMPLE") held by an in-memory secret store, never the keychain. A step that cannot be reached
is recorded as skipped with the reason; the run always ends by quitting Blender.

Not a test: pytest does not collect this directory (``conftest.py`` ignores it) and it needs a GUI Blender.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import subprocess
import sys
import time
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.dirname(HERE)
ADDON_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(ADDON_DIR)
for _p in (ADDON_DIR, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


OUT = os.path.abspath(arg("--out", "ui-shots-out"))
ONLY = [s for s in (arg("--only", "") or "").split(",") if s]
ENGINE = arg("--engine", "real")
SCALE = float(arg("--scale", "1.0"))
SIZE = arg("--size", "")
WATCHDOG_S = float(arg("--watchdog", "420"))
os.makedirs(OUT, exist_ok=True)

MANIFEST = {"tool": "ui_shots", "blender": bpy.app.version_string, "engine": ENGINE, "git_sha": "", "timestamp": "",
            "window_size": SIZE, "ui_scale": SCALE, "steps": []}


def log(*a):
    print("[ui_shots]", *a, flush=True)


class Skip(Exception):
    """Raised by a step that cannot be reached; the message is the reason."""


def wait_for(predicate, what, timeout=90.0):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise Skip(f"timed out waiting for {what}")
        yield 0.1


# --------------------------------------------------------------------------- window / viewport helpers

def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    win = window()
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    return win, area, area.spaces.active


def redraw_all():
    for win in bpy.context.window_manager.windows:
        for a in win.screen.areas:
            a.tag_redraw()


def maximise_viewport():
    """The 3D viewport fills the window (no timeline, outliner or properties editor)."""
    win, area, _ = view3d()
    if len(win.screen.areas) > 1:
        with bpy.context.temp_override(window=win, screen=win.screen, area=area):
            bpy.ops.screen.screen_full_area()


def tidy_viewport():
    _, area, space = view3d()
    space.show_region_ui = True
    space.show_region_toolbar = False
    space.show_region_tool_header = False
    space.show_gizmo = False
    ov = space.overlay
    ov.show_cursor = False
    ov.show_text = False
    ov.show_stats = False
    ov.show_extras = False
    ov.show_relationship_lines = False


def set_view(location, rot_deg, distance):
    from mathutils import Euler
    _, _, space = view3d()
    r3d = space.region_3d
    r3d.view_perspective = "PERSP"
    r3d.view_location = location
    r3d.view_rotation = Euler([math.radians(a) for a in rot_deg], "XYZ").to_quaternion()
    r3d.view_distance = distance


def region_rect(kind):
    _, area, _ = view3d()
    for r in area.regions:
        if r.type == kind:
            return r.x, r.y, r.width, r.height
    return None


# --------------------------------------------------------------------------- sidebar panel open / closed

PANELS: list = []
ORIG_OPTS: dict = {}
CURRENT_OPEN = [None]


ORIG_ID: dict = {}
GENERATION = [0]


def _key(cls):
    return ORIG_ID.get(cls, cls.bl_idname).lower().split("_pt_")[-1]


def record_panels(register):
    """Run ``register()`` while noting, in order, the Slicer-tab panel classes it registers (their drawing order)."""
    from slicewright.names import TAB_NAME
    real = bpy.utils.register_class

    def spy(cls):
        real(cls)
        if getattr(cls, "bl_category", None) == TAB_NAME and issubclass(cls, bpy.types.Panel) and cls not in PANELS:
            PANELS.append(cls)
            ORIG_OPTS[cls] = set(getattr(cls, "bl_options", set()))
            ORIG_ID[cls] = cls.bl_idname
    bpy.utils.register_class = spy
    try:
        register()
    finally:
        bpy.utils.register_class = real
    PANELS.sort(key=lambda c: getattr(c, "bl_order", 0))     # Blender draws by ``bl_order`` (names.PANEL_ORDER), then registration order
    log("panels:", [_key(c) for c in PANELS])


def set_open(keys):
    """Open exactly the panels named in ``keys`` (None: each panel's own default) by re-registering them in order; the
    open state of a Blender panel is not scriptable, but a new panel starts from ``DEFAULT_CLOSED``. A generator: the
    region must draw once without the panels so it forgets their old state."""
    if keys is None and CURRENT_OPEN[0] is None:
        return
    if keys is not None and CURRENT_OPEN[0] == set(keys):
        return
    for c in reversed(PANELS):
        try:
            bpy.utils.unregister_class(c)
        except Exception as exc:  # noqa: BLE001
            log("unregister failed", c.__name__, exc)
    redraw_all()
    yield 0.5
    GENERATION[0] += 1
    for c in PANELS:
        c.bl_idname = f"{ORIG_ID[c]}_g{GENERATION[0]}"      # a new id: the region keeps the old open/closed state by id
        opts = set(ORIG_OPTS[c])
        if keys is not None and "HIDE_HEADER" not in opts:
            opts.discard("DEFAULT_CLOSED")
            if _key(c) not in keys:
                opts.add("DEFAULT_CLOSED")
        c.bl_options = opts
        bpy.utils.register_class(c)
    log("set_open", sorted(keys) if keys is not None else None)
    CURRENT_OPEN[0] = None if keys is None else set(keys)


# --------------------------------------------------------------------------- screenshots

def crop_png(src, dst, rect):
    import numpy as np
    x, y, w, h = [int(v) for v in rect]
    img = bpy.data.images.load(src)
    try:
        img.colorspace_settings.name = "Non-Color"
        iw, ih = img.size
        px = np.empty(iw * ih * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(ih, iw, 4)
        x0, y0 = max(0, x), max(0, y)
        sub = px[y0:min(ih, y + h), x0:min(iw, x + w)]
        out = bpy.data.images.new("ui_shots_crop", sub.shape[1], sub.shape[0], alpha=True)
        out.colorspace_settings.name = "Non-Color"
        out.pixels.foreach_set(np.ascontiguousarray(sub).ravel())
        out.filepath_raw = dst
        out.file_format = "PNG"
        out.save()
        bpy.data.images.remove(out)
    finally:
        bpy.data.images.remove(img)


def shot(name, description, panels=None, settle=0.8, header=False):
    """Write ``<name>.png`` (the whole window) and ``<name>_sidebar.png`` (the Slicer sidebar only)."""
    STATE["desc"] = description
    yield from set_open(panels)
    redraw_all()
    yield settle
    redraw_all()
    yield 0.4
    win = window()
    rect = region_rect("UI")
    sysp = bpy.context.preferences.system
    log("sidebar", rect, "dpi", sysp.dpi, "ui_scale", bpy.context.preferences.view.ui_scale, "pixel_size", sysp.pixel_size)
    path = os.path.join(OUT, f"{name}.png")
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.screen.screenshot(filepath=path)
    files = [os.path.basename(path)]
    try:
        img = bpy.data.images.load(path)
        iw = img.size[0]
        bpy.data.images.remove(img)
        rect = region_rect("UI")
        if rect and rect[2] > 20:
            dst = os.path.join(OUT, f"{name}_sidebar.png")
            crop_png(path, dst, rect)
            files.append(os.path.basename(dst))
        if header:
            hr = region_rect("HEADER")
            if hr:
                dst = os.path.join(OUT, f"{name}_header.png")
                crop_png(path, dst, (hr[0], hr[1], hr[2], hr[3]))
                files.append(os.path.basename(dst))
    except Exception:  # noqa: BLE001
        log("crop failed", traceback.format_exc())
    return files


# --------------------------------------------------------------------------- scene helpers

def torus(name, R=25.0, r=9.0, nu=48, nv=20, loc=(0, 0, 9.0)):
    me = bpy.data.meshes.new(name)
    verts, faces = [], []
    for i in range(nu):
        a = 2 * math.pi * i / nu
        for j in range(nv):
            b = 2 * math.pi * j / nv
            verts.append(((R + r * math.cos(b)) * math.cos(a), (R + r * math.cos(b)) * math.sin(a), r * math.sin(b)))
    for i in range(nu):
        for j in range(nv):
            faces.append((i * nv + j, ((i + 1) % nu) * nv + j, ((i + 1) % nu) * nv + (j + 1) % nv, i * nv + (j + 1) % nv))
    me.from_pydata(verts, [], faces)
    me.update()
    return link(name, me, loc)


def cube(name, size, loc):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(me)
    bm.free()
    return link(name, me, loc)


def cylinder(name, r, h, loc):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=40, radius1=r, radius2=r, depth=h)
    bm.to_mesh(me)
    bm.free()
    return link(name, me, loc)


def link(name, me, loc):
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    return ob


def clear_objects():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)


def put_on_plate(*objs):
    win, area, _ = view3d()
    bpy.ops.object.select_all(action="DESELECT") if bpy.context.object else None
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[-1]
    with bpy.context.temp_override(window=win, screen=win.screen, area=area):
        bpy.ops.slicewright.plate_add()


def select_only(*objs):
    for o in bpy.context.scene.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = objs[-1]


BED = (0.0, 0.0)                     # world (0, 0) is the centre of the bed (core.frame)
STATE: dict = {}


def slice_now(sw):
    from slicewright.blender.preview import runtime
    win, area, _ = view3d()
    with bpy.context.temp_override(window=win, screen=win.screen, area=area):
        bpy.ops.slicewright.slice("INVOKE_DEFAULT")
    yield from wait_for(lambda: sw.job_state in ("done", "failed"), "the slice")
    if sw.job_state != "done":
        raise Skip(f"slice failed: {sw.job_message}")
    yield from wait_for(lambda: runtime.get(bpy.context.scene) is not None
                        and runtime.get(bpy.context.scene).controller.done, "the preview upload")


# --------------------------------------------------------------------------- steps

def step_setup():
    import slicewright
    from slicewright import prefs
    from slicewright.blender import library, picker, registry, secrets_rt
    from slicewright.core.network import secrets as core_secrets
    from slicewright.core.network.access import Access
    record_panels(slicewright.register)
    bpy.context.preferences.use_preferences_save = False
    bpy.context.preferences.view.ui_scale = SCALE
    entry = bpy.context.preferences.addons.new()                 # in-memory only: the printers live in the add-on prefs
    entry.module = prefs._package()
    STATE["prefs"] = bpy.context.preferences.addons[prefs._package()].preferences
    items = {}
    store = type("Store", (core_secrets.SecretStore,), {
        "name": "ui_shots in-memory store", "available": lambda s: True, "get": lambda s, r: items.get(r),
        "set": lambda s, r, v: items.__setitem__(r, v), "delete": lambda s, r: items.pop(r, None) is not None})()
    secrets_rt.set_manager(core_secrets.SecretManager(store, core_secrets.FileSecretStore(os.path.join(OUT, ".nosecrets.json"))))
    if not registry.state.status.ok:
        raise Skip(f"engine not available: {registry.state.status.summary}")
    clear_objects()
    bpy.ops.slicewright.use_mm_scene()
    maximise_viewport()
    tidy_viewport()
    library.request()
    yield from wait_for(lambda: library.status()[0] == "ready", "the printer library", 180.0)
    sw = bpy.context.scene.slicewright
    printers = library.get().printers()
    pick = next((e for e in printers if e.name == "Bambu Lab A1 0.4 nozzle"), None) or printers[0]
    picker.select_printer(sw, pick)
    STATE["printer"] = pick.name
    log("printer:", pick.name, "process:", sw.process_id)
    set_view((BED[0], BED[1], 0), (60, 0, 25), 430)
    yield 0.5


def s_prepare_empty():
    yield from ()
    set_view((BED[0], BED[1], 0), (60, 0, 25), 430)
    return (yield from shot("prepare_empty", "Add-on loaded, empty plate, printer chosen", panels=None))


def s_prepare_cube():
    c = cube("Cube", 30.0, (BED[0], BED[1], 15.0))
    put_on_plate(c)
    set_view((BED[0], BED[1], 10), (60, 0, 25), 330)
    return (yield from shot("prepare_cube", "One 30 mm cube on the plate, selected, Slicer tab", panels=None))


def _plate_view():
    set_view((BED[0], BED[1] - 40, 0), (78, 0, 8), 330)


def s_plate_mesh():
    from slicewright import prefs
    bpy.context.scene.slicewright.mode = "PREPARE"
    clear_objects()
    c = cube("Cube", 30.0, (BED[0] + 20, BED[1], 15.0))
    put_on_plate(c)
    prefs.get().show_plate_mesh = True
    _plate_view()
    return (yield from shot("plate_mesh", "Prepare: the dark build plate slab under the grid, low angle (edge thickness)",
                            panels={"plate"}))


def s_plate_mesh_off():
    from slicewright import prefs
    prefs.get().show_plate_mesh = False
    _plate_view()
    r = yield from shot("plate_mesh_off", "Prepare: Show build plate switched off (grid only)", panels={"plate"})
    prefs.get().show_plate_mesh = True
    return r


def s_plate_mesh_preview():
    from slicewright.blender import mode
    why = mode.set_mode(bpy.context.scene, "PREVIEW")
    if why:
        raise Skip(why)
    set_view((BED[0] + 30, BED[1] - 60, 0), (72, 0, 12), 150)
    return (yield from shot("plate_mesh_preview", "Preview over the plate: the toolpaths are not hidden by the slab",
                            panels={"preview"}))


def s_prepare_multi():
    from slicewright.blender import object_settings
    clear_objects()
    a = torus("Torus", loc=(-48, -48, 9.0))
    b = cube("Cube", 28.0, (22, -38, 14.0))
    c = cylinder("Cylinder", 12.0, 34.0, (-18, 32, 17.0))
    put_on_plate(a, b, c)
    for o in (a, b, c):
        o.select_set(True)
    win, area, _ = view3d()
    try:
        with bpy.context.temp_override(window=win, screen=win.screen, area=area):
            bpy.ops.slicewright.arrange()
    except Exception as exc:  # noqa: BLE001
        log("arrange failed", exc)
    sw = bpy.context.scene.slicewright
    keys = object_settings.offer(b, "advanced", False)
    key = "sparse_infill_density" if "sparse_infill_density" in keys else (keys[0] if keys else None)
    if key:
        object_settings.add_override(b, sw, key)
        log("override", key, "on", b.name)
    if "wall_generator" in keys:                 # an enum override: drawn as segmented buttons
        object_settings.add_override(b, sw, "wall_generator")
    select_only(b)
    set_view((BED[0], BED[1], 10), (60, 0, 25), 360)
    return (yield from shot("prepare_multi", "Three objects after Arrange; the cube has a per-object override and is active",
                            panels={"plate", "object", "issues", "printer"}))


def _settings_shot(name, mode, desc):
    prefs = STATE["prefs"]
    prefs.settings_mode = mode
    sw = bpy.context.scene.slicewright
    sw.settings_role = "process"
    return (yield from shot(name, desc, panels={"settings", "printer"}))


def s_settings():
    return (yield from _settings_shot("settings", "advanced", "Settings panel open, Advanced detail (the default)"))


def s_settings_simple():
    return (yield from _settings_shot("settings_simple", "simple", "Settings panel, Simple detail"))


def _segmented_shot(name, page_name, desc, text=""):
    from slicewright.blender.ui import choice, settings
    prefs, sw = STATE["prefs"], bpy.context.scene.slicewright
    prefs.settings_mode = "expert"
    sw.settings_role = "process"
    cfg = sw.process_edits
    log("available chars in the sidebar (default if no region):", choice.available_chars())
    for page in settings.pages_for("process"):
        log("page", page.name, "segmented:",
            [(k, len(choice.option_labels(cfg, k))) for k in page.keys() if choice.style_of(cfg, k) == "segmented"])
    sw.settings_page = page_name
    sw.settings_filter = text
    r = yield from shot(name, desc, panels={"settings", "printer"})
    sw.settings_filter = ""
    prefs.settings_mode = "advanced"
    return r


def s_settings_segmented():
    return (yield from _segmented_shot("settings_segmented", "Others",
                                       "Process page Others: two-option choices as segmented buttons, default sidebar width"))


def s_settings_segmented_three():
    return (yield from _segmented_shot("settings_segmented_three", "Multimaterial",
                                       "Process page Multimaterial filtered to 'wall type': a three-option choice as segmented buttons",
                                       text="wall type"))


def s_settings_expert():
    r = yield from _settings_shot("settings_expert", "expert", "Settings panel, Expert detail")
    STATE["prefs"].settings_mode = "advanced"
    return r


def s_sliced_preview():
    sw = bpy.context.scene.slicewright
    clear_objects()
    t = torus("Torus", R=24.0, r=9.0, loc=(BED[0], BED[1], 9.0))
    put_on_plate(t)
    yield from slice_now(sw)
    from slicewright.blender.preview import props as pprops, runtime
    p = pprops.of(bpy.context.scene)
    n = len(runtime.get(bpy.context.scene).layers["z"])
    STATE["layers"] = n
    p.layer_hi = n // 2
    _, _, space = view3d()
    space.shading.type = "SOLID"
    vt = os.environ.get("SLW_VIEW_TRANSFORM")          # scratch scene only: "Standard" / "AgX" / "Filmic" to compare
    if vt:
        bpy.context.scene.view_settings.view_transform = vt
    set_view((BED[0] + 30, BED[1], 8), (55, 0, 20), 110)
    return (yield from shot("sliced_preview", f"After a real slice of a torus: Preview mode, scrubbed to layer {n // 2} of {n}",
                            panels={"preview", "export"}))


def _model_shot(model, description):
    from slicewright.blender.preview import props as pprops
    p = pprops.of(bpy.context.scene)
    n = STATE.get("layers", p.layer_hi)
    p.layer_lo, p.layer_hi = 0, max(1, int(n * 0.6))
    p.model_display = model
    _, _, space = view3d()
    space.shading.type = "SOLID"
    set_view((BED[0] + 30, BED[1] - 10, 8), (55, 0, 20), 110)
    yield 0.5
    return (yield from shot(f"preview_model_{model.lower()}", description, panels={"preview"}))


def s_preview_model_hidden():
    return (yield from _model_shot("HIDDEN", "Preview, Model: Hidden (the default): the toolpaths are not blocked"))


def s_preview_model_ghost():
    return (yield from _model_shot("GHOST", "Preview, Model: Ghost: the model drawn faintly behind the toolpaths"))


def s_preview_model_shown():
    r = yield from _model_shot("SHOWN", "Preview, Model: Shown: the solid model hides the toolpaths inside it")
    from slicewright.blender.preview import props as pprops
    pprops.of(bpy.context.scene).model_display = "HIDDEN"
    return r


def s_preview_layers_features():
    from slicewright.blender.preview import props as pprops
    p = pprops.of(bpy.context.scene)
    n = STATE.get("layers", p.layer_hi)
    p.layer_lo = 0
    p.layer_hi = max(1, int(n * 0.85))
    p.show_travel = p.show_retracts = p.show_seams = True
    p.grey_below = True
    set_view((BED[0] + 25, BED[1], 12), (50, 0, 35), 120)
    return (yield from shot("preview_layers_features", "Travel, retractions and seams on, lower layers greyed, layer 85 %",
                            panels={"preview"}))


def s_mode_switch_preview():
    yield from ()
    return (yield from shot("mode_switch_preview", "Prepare | Preview row in the viewport header, Preview active",
                            panels={"preview"}, header=True))


def s_mode_switch_prepare():
    from slicewright.blender import mode
    why = mode.set_mode(bpy.context.scene, "PREPARE")
    if why:
        raise Skip(why)
    r = yield from shot("mode_switch_prepare", "Prepare | Preview row in the viewport header, Prepare active",
                        panels={"plate", "issues"}, header=True)
    mode.set_mode(bpy.context.scene, "PREVIEW")
    return r


def s_export_panel():
    yield from ()
    return (yield from shot("export_panel", "Export panel after a slice (buttons live; nothing exported)",
                            panels={"export", "preview"}))


def _fake_printer():
    prefs = STATE["prefs"]
    if "printer_pg" in STATE:
        return STATE["printer_pg"]
    from slicewright.blender import secrets_rt
    bpy.ops.slicewright.printer_add(kind="octoprint")
    pg = prefs.printers[-1]
    pg.name = "Example OctoPrint"
    pg.host = "192.0.2.1"                         # TEST-NET-1: never routable
    secrets_rt.manager().set(pg.secret_ref, "EXAMPLE")
    pg.secret_saved = True
    STATE["printer_pg"] = pg
    return pg


def s_send_panel_gate_off():
    from slicewright.blender import sending
    pg = _fake_printer()
    send = sending.state(bpy.context)
    send.show_all = True
    send.printer = pg.name
    return (yield from shot("send_panel_gate_off", "Send panel with a fake printer, Blender online access off (the default)",
                            panels={"send", "export"}))


def s_send_panel_gate_on():
    from slicewright.core.network import access
    from slicewright.core.network.access import Access
    yield from ()
    pg = _fake_printer()
    access.set_provider(lambda: Access(True))        # only changes what the UI asks; no connection is made
    r = yield from shot("send_panel_gate_on", "Send panel with the online gate simulated ON (nothing is sent)",
                        panels={"send", "export"})
    return r


def s_bake_panel():
    yield from ()
    return (yield from shot("bake_panel", "Bake panel open after a slice (nothing baked)", panels={"bake", "preview"}))


def s_baked_result():
    from slicewright.blender import baking, bake_pg
    sc = bpy.context.scene
    st = bake_pg.of(bpy.context)
    st.kind = "CURVES"
    st.use_preview = False
    st.layer_lo, st.layer_hi = 0, 6
    win, area, _ = view3d()
    with bpy.context.temp_override(window=win, screen=win.screen, area=area):
        res = bpy.ops.slicewright.bake("EXEC_DEFAULT")
    log("bake:", res)
    yield from wait_for(lambda: baking.progress(sc) is None and baking.message(sc) is not None, "the bake", 120.0)
    log("bake message:", baking.message(sc))
    from slicewright.blender import mode
    mode.set_mode(sc, "PREPARE")
    return (yield from shot("baked_result", "After baking layers 0-6 to Curves (Prepare mode, Bake panel)",
                            panels={"bake"}))


def s_issues():
    sw = bpy.context.scene.slicewright
    from slicewright.blender import mode
    mode.set_mode(bpy.context.scene, "PREPARE")
    clear_objects()
    c = cube("Cube", 30.0, (BED[0], BED[1], 15.0))
    far = cube("Far cube", 30.0, (5000.0, 100.0, 15.0))
    put_on_plate(c, far)
    win, area, _ = view3d()
    with bpy.context.temp_override(window=win, screen=win.screen, area=area):
        bpy.ops.slicewright.slice("INVOKE_DEFAULT")
    yield from wait_for(lambda: sw.job_state in ("done", "failed"), "the slice to fail")
    yield 0.5
    set_view((BED[0], BED[1], 10), (60, 0, 25), 360)
    return (yield from shot("issues", f"A slice with an object outside the volume ({sw.job_state}: {sw.job_message[:80]})",
                            panels={"issues"}))


def s_issues_ignored_open():
    yield from s_issues()
    bpy.context.window_manager.slicewright_ignored_open = True
    try:
        return (yield from shot("issues_ignored_open", "The Issues panel with the 'profile settings ignored' list opened",
                                panels={"issues"}))
    finally:
        bpy.context.window_manager.slicewright_ignored_open = False


def s_preferences_printers():
    """The add-on is not installed as an extension, so Preferences > Add-ons cannot list it; this draws the add-on's own
    Printers box (``ui/printers_box.draw``, the code the preferences call) in a mirror panel in the Slicer tab."""
    from slicewright.blender.ui import printers_box
    from slicewright.names import TAB_NAME
    _fake_printer()

    class SLICEWRIGHT_PT_shots_prefs(bpy.types.Panel):
        bl_idname = "SLICEWRIGHT_PT_shots_prefs"
        bl_label = "Preferences: Printers (mirror)"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = TAB_NAME

        def draw(self, context):
            printers_box.draw(self.layout.box(), STATE["prefs"])

    bpy.utils.register_class(SLICEWRIGHT_PT_shots_prefs)
    PANELS.append(SLICEWRIGHT_PT_shots_prefs)
    ORIG_OPTS[SLICEWRIGHT_PT_shots_prefs] = set()
    ORIG_ID[SLICEWRIGHT_PT_shots_prefs] = "SLICEWRIGHT_PT_shots_prefs"
    return (yield from shot("preferences_printers", "Printers box of the add-on preferences (mirror panel; see manifest note)",
                            panels={"shots_prefs"}))


STEPS = [
    ("prepare_empty", s_prepare_empty), ("prepare_cube", s_prepare_cube), ("prepare_multi", s_prepare_multi),
    ("plate_mesh", s_plate_mesh), ("plate_mesh_off", s_plate_mesh_off), ("plate_mesh_preview", s_plate_mesh_preview),
    ("settings", s_settings), ("settings_simple", s_settings_simple), ("settings_expert", s_settings_expert), ("settings_segmented", s_settings_segmented), ("settings_segmented_three", s_settings_segmented_three),
    ("sliced_preview", s_sliced_preview), ("preview_model_hidden", s_preview_model_hidden), ("preview_model_ghost", s_preview_model_ghost),
    ("preview_model_shown", s_preview_model_shown), ("preview_layers_features", s_preview_layers_features),
    ("mode_switch_preview", s_mode_switch_preview), ("mode_switch_prepare", s_mode_switch_prepare),
    ("export_panel", s_export_panel), ("send_panel_gate_off", s_send_panel_gate_off),
    ("send_panel_gate_on", s_send_panel_gate_on), ("bake_panel", s_bake_panel), ("baked_result", s_baked_result),
    ("issues", s_issues), ("issues_ignored_open", s_issues_ignored_open), ("preferences_printers", s_preferences_printers),
]
NEEDS_SLICE = {"plate_mesh_preview", "preview_model_hidden", "preview_model_ghost", "preview_model_shown", "preview_layers_features", "mode_switch_preview", "mode_switch_prepare", "export_panel",
               "send_panel_gate_off", "send_panel_gate_on", "bake_panel", "baked_result"}


def wanted(name):
    return not ONLY or any(name == o or name.startswith(o + "_") for o in ONLY)


def write_manifest():
    MANIFEST["timestamp"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(MANIFEST, f, indent=2)


def main():
    try:
        MANIFEST["git_sha"] = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True,
                                             text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    ok_setup = True
    try:
        yield from step_setup()
    except Skip as exc:
        ok_setup, MANIFEST["setup_error"] = False, str(exc)
    except Exception:  # noqa: BLE001
        ok_setup, MANIFEST["setup_error"] = False, traceback.format_exc()
    slice_ok = [None]
    for name, fn in STEPS:
        if not wanted(name):
            continue
        entry = {"step": name, "status": "ok", "files": [], "description": ""}
        MANIFEST["steps"].append(entry)
        try:
            if not ok_setup:
                raise Skip("setup failed: " + MANIFEST.get("setup_error", "")[:200])
            if name in NEEDS_SLICE and "layers" not in STATE:
                yield from s_sliced_preview_quiet()
            files = yield from fn()
            entry["files"] = files or []
            entry["description"] = STATE.get("desc", "")
        except Skip as exc:
            entry.update(status="skipped", reason=str(exc))
            log("SKIP", name, exc)
        except Exception:  # noqa: BLE001
            entry.update(status="skipped", reason="error: " + traceback.format_exc().strip().splitlines()[-1])
            log("ERROR", name, traceback.format_exc())
        write_manifest()
    write_manifest()


def s_sliced_preview_quiet():
    """A step that needs a slice when the one that makes it was not selected (``--only``)."""
    sw = bpy.context.scene.slicewright
    clear_objects()
    t = torus("Torus", R=24.0, r=9.0, loc=(BED[0], BED[1], 9.0))
    put_on_plate(t)
    yield from slice_now(sw)
    from slicewright.blender.preview import runtime
    STATE["layers"] = len(runtime.get(bpy.context.scene).layers["z"])
    _, _, space = view3d()
    space.shading.type = "SOLID"
    set_view((BED[0] + 30, BED[1], 8), (55, 0, 20), 110)


def start():
    gen = main()
    state = {"done": False}

    def tick():
        try:
            return next(gen)
        except StopIteration:
            pass
        except Exception:  # noqa: BLE001
            log("FATAL", traceback.format_exc())
        finish()
        return None

    def watchdog():
        log("watchdog: quitting")
        MANIFEST["watchdog"] = True
        finish()

    def finish():
        if state["done"]:
            return
        state["done"] = True
        try:
            write_manifest()
        finally:
            bpy.ops.wm.quit_blender()

    bpy.app.timers.register(tick, first_interval=2.5)
    bpy.app.timers.register(watchdog, first_interval=WATCHDOG_S)


start()
