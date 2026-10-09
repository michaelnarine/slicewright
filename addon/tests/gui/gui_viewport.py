# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI Blender, end to end: the add-on registered, a G-code file previewed through the operator,
uploaded by the real tick timer, drawn by the real ``POST_VIEW`` handler into the viewport.

    Blender --factory-startup [--gpu-backend ...] --python gui_viewport.py -- --out DIR [--moves 2200000]

Checks: upload completes on timer ticks (one chunk per tick), view changes recreate only ``t_val``
textures, scrubbing creates no textures, the VRAM budget truncates and says so, unregister removes
the draw handler. Writes ``viewport_*.png`` (viewport region captured through ``POST_PIXEL``).
"""
import os
import sys
import time

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

N_MOVES = int(g.arg("--moves", 2_200_000))


def wait_done(ctl, limit=600.0):
    t0 = time.perf_counter()
    while not ctl.done and ctl.error is None and time.perf_counter() - t0 < limit:
        yield 0.1


def steps():
    import bpy
    import numpy as np
    sys.path.insert(0, os.path.join(g.TESTS_DIR, "fixtures", "gcode"))
    import make_large
    import slicewright
    from slicewright.blender import timers
    from slicewright.blender.preview import handler, props as pprops, runtime

    slicewright.register()
    g.frame_part()
    cap = g.ViewportCapture()
    sc = bpy.context.scene
    p = getattr(sc, pprops.PROP_NAME)
    path = os.path.join(g.OUT, "part.gcode")
    make_large.write(path, N_MOVES)
    t0 = time.perf_counter()
    bpy.ops.slicewright.preview_load_gcode(filepath=path)
    g.RESULTS["info"]["parse_s"] = round(time.perf_counter() - t0, 1)
    os.remove(path)
    rt = runtime.get(sc)
    ctl = rt.controller
    r = rt.renderer
    g.check("operator_created_a_preview", ctl is not None and sc.slicewright.mode == "PREVIEW")
    steps_before = timers.runner.steps
    yield from wait_done(ctl)
    chunks = len(ctl.bounds)
    g.check("upload_finishes_on_timer_ticks", ctl.done and ctl.error is None and chunks >= 2,
            chunks=chunks, runner_steps=timers.runner.steps - steps_before, error=str(ctl.error))
    g.check("upload_is_one_step_per_chunk", timers.runner.steps - steps_before == chunks
            and r.uploaded_last == rt.n_moves - 1, ticks=timers.runner.ticks)
    tex0 = r.stats["textures_created"]
    assert cap.shot(os.path.join(g.OUT, "viewport_roles.png")), "draw handler did not run"
    roles = cap.last
    bg = roles[5, 5, :3].astype(int)                          # the viewport background
    lit = int((np.abs(roles[..., :3].astype(int) - bg).max(axis=2) > 40).sum())
    g.check("viewport_shows_the_toolpath", lit > 5000, lit_pixels=lit, region=list(roles.shape[:2]))

    p.view_type = "speed"
    yield 0.1
    yield from wait_done(ctl)
    cap.shot(os.path.join(g.OUT, "viewport_speed.png"))
    speed = cap.last
    made = r.stats["textures_created"] - tex0
    g.check("view_change_recreates_only_t_val", made == chunks and not np.array_equal(roles, speed),
            textures_created=made, chunks=chunks)

    tex1 = r.stats["textures_created"]
    nlayers = len(rt.layers["z"])
    p.layer_hi = nlayers // 2
    p.scrub_moves = True
    for k in range(40):                                         # scrub through a layer
        p.move_pos = k * 400
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    bpy.ops.slicewright.preview_step(target="TOP", delta=-1)
    p.show_travel, p.show_retracts, p.show_seams, p.grey_below = True, True, True, True
    cap.shot(os.path.join(g.OUT, "viewport_scrub.png"))
    g.check("scrubbing_creates_no_textures", r.stats["textures_created"] == tex1,
            created=r.stats["textures_created"] - tex1, draws=r.stats["draw_calls"])
    g.check("scrub_image_differs", not np.array_equal(cap.last, speed))

    # ---- VRAM budget: a tiny budget truncates the preview and forbids the range views
    p.view_type = "feature"
    ctl2 = handler.show_result(sc, rt.result, budget_mb=20, chunk=1 << 18)
    yield from wait_done(ctl2)
    cap.shot(os.path.join(g.OUT, "viewport_truncated.png"))
    g.check("budget_truncates_and_says_so", ctl2.plan.truncated and 0 < len(ctl2.renderer.chunks) < len(ctl2.bounds)
            and any("budget" in m for m in ctl2.messages), messages=ctl2.messages,
            drawn_moves=ctl2.uploaded_moves, total=ctl2.rt.n_moves)
    p.view_type = "speed"
    g.check("over_budget_view_falls_back_to_feature", p.view_type == "feature")

    cap.close()
    handler.clear(sc)
    slicewright.unregister()
    g.check("unregister_removes_the_draw_handler", handler._draw_handle is None and runtime.get(sc) is None)


g.run_steps(steps)
