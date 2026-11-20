# SPDX-License-Identifier: GPL-3.0-or-later
"""Side check: do bpy.app.timers fire in `Blender -b`?
    Blender -b --factory-startup --python spikes/gpu/timers_bg.py -- <variant>
variants: end (script just ends), sleep (script sleeps 2 s), modal (calls wm.redraw_timer / event pump)."""
import sys
import time
import bpy

variant = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "end"
fired = []


def cb():
    fired.append(time.time())
    print("TIMER FIRED", len(fired), flush=True)
    return 0.1 if len(fired) < 3 else None


bpy.app.timers.register(cb, first_interval=0.2)
print("registered; is_registered =", bpy.app.timers.is_registered(cb), "background =", bpy.app.background, flush=True)
if variant == "sleep":
    time.sleep(2.0)
    print("after sleep, fired =", len(fired), flush=True)
elif variant == "pump":
    # try to make Blender process events/timers from inside the script
    for _ in range(20):
        time.sleep(0.1)
        try:
            bpy.ops.wm.redraw_timer(type='DRAW', iterations=1)
        except Exception as e:
            print("redraw_timer err:", e)
            break
    print("after pump, fired =", len(fired), flush=True)
elif variant == "loop":
    # the documented workaround: never return; drive our own loop and call the callback manually
    t_end = time.time() + 1.0
    while time.time() < t_end:
        time.sleep(0.05)
    print("after loop, fired =", len(fired), flush=True)
print("script end, fired =", len(fired), flush=True)
