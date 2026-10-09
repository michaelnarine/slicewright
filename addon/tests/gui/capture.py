# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture a screenshot of the add-on's panels in a real (GUI) Blender, then quit.

    Blender --factory-startup --window-geometry 0 0 1100 900 --python addon/tests/gui/capture.py \
        -- <out.png> <scenario>

Scenarios are functions registered in ``SCENARIOS`` that put the UI in a state to photograph. The
fake engine stands in for the real one. Blender has no API to activate a sidebar tab, so the panels'
``bl_category`` is switched to the always-visible "Item" tab before registration; nothing else differs.
The script always quits (also on error), so no window is left open.
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.dirname(os.path.dirname(HERE))
for p in (ADDON, os.path.join(ADDON, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import bpy  # noqa: E402

OUT, SCENARIO = (sys.argv[sys.argv.index("--") + 1:] + ["", ""])[:2]
os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
bpy.context.preferences.view.show_splash = False
SCENARIOS = {}


def scenario(fn):
    SCENARIOS[fn.__name__] = fn
    return fn


def wait_for_library():
    from slicewright.blender import library, timers
    library.request()
    timers.runner.run_until_idle()


@scenario
def picker():
    """Library loaded, an Acme printer with its default process and filaments chosen."""
    wait_for_library()
    pg = bpy.context.scene.slicewright
    pg.pick_vendor = "Acme"
    pg.pick_nozzle = "0.6"


@scenario
def settings():
    """The process settings page "Strength" with an Acme printer selected."""
    wait_for_library()
    pg = bpy.context.scene.slicewright
    pg.printer_id = "sys:Acme/Acme Maker 1 0.4 nozzle"
    pg.settings_role, pg.settings_page = "process", "Strength"
    pg.process_edits.sparse_infill_density = 20.0


def open_sidebar():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.show_region_ui = True
                area.tag_redraw()
                with bpy.context.temp_override(window=window, area=area):
                    bpy.ops.screen.screen_full_area()       # the viewport gets the whole window
                return


def finish(error=None):
    if error:
        sys.stderr.write(error)
    sys.stderr.flush()
    bpy.ops.wm.quit_blender()


def main():
    import slicewright
    from slicewright.blender import ui
    for cls in ui.engine_classes + ui.classes:
        cls.bl_category = "Item"
        if SCENARIO == "settings" and cls.__name__ != "SLICEWRIGHT_PT_settings":
            cls.bl_options = {"DEFAULT_CLOSED"}             # leave room for the page being photographed
    slicewright.register()
    SCENARIOS[SCENARIO]()
    open_sidebar()
    steps = {"n": 0}

    def tick():
        steps["n"] += 1
        if steps["n"] < 4:                 # let the window draw a few frames first
            return 0.3
        try:
            bpy.ops.screen.screenshot(filepath=OUT, check_existing=False)
            print("captured", OUT)
        except Exception:  # noqa: BLE001
            finish(traceback.format_exc())
            return None
        bpy.app.timers.register(lambda: finish(), first_interval=0.5)
        return None

    bpy.app.timers.register(tick, first_interval=0.5)


try:
    main()
except Exception:  # noqa: BLE001
    finish(traceback.format_exc())
