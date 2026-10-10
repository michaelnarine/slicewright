# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI Blender benchmark for plan M6 acceptance: a 10M-move ``from_gcode`` print previewed in the
real viewport. Local use (software rasterisers make FPS meaningless); not part of CI.

    Blender --factory-startup --python bench_preview.py -- --out DIR [--moves 10000000] [--visible 5000000]

Measures: parse time and RSS, per-tick and per-chunk build time while the upload runs on the tick
runner, VRAM (planned bytes), FPS with ~5M visible segments (static, and scrubbing a layer) through
``redraw_timer`` on the real window, and textures created while scrubbing. Thresholds are the
Phase 0 spike's: >= 30 FPS at 5M, chunk build <= 40 ms per tick, no textures while scrubbing.
"""
import os
import resource
import statistics
import sys
import time

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

N_MOVES = int(g.arg("--moves", 10_000_000))
VISIBLE = int(g.arg("--visible", 5_000_000))


def rss_gb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1e9 if sys.platform == "darwin" else r / 1e6


def fps(iterations):
    import bpy
    win, area, region, _ = g.view3d()
    with bpy.context.temp_override(window=win, area=area, region=region):
        t = time.perf_counter()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=iterations)
        return iterations / (time.perf_counter() - t)


def steps():
    import bpy
    sys.path.insert(0, os.path.join(g.TESTS_DIR, "fixtures", "gcode"))
    import make_large
    import slicewright
    from slicewright.blender import registry, timers
    from slicewright.blender.preview import handler, props as pprops, runtime
    from slicewright.core import preview_budget

    slicewright.register()
    g.frame_part(distance=210.0, z=8.0)
    cap = g.ViewportCapture()
    sc = bpy.context.scene
    p = getattr(sc, pprops.PROP_NAME)
    info = g.RESULTS["info"]
    path = os.path.join(g.OUT, "bench.gcode")
    t0 = time.perf_counter()
    make_large.write(path, N_MOVES)
    info["generate_s"] = round(time.perf_counter() - t0, 1)
    t0 = time.perf_counter()
    result = registry.state.status.module.from_gcode(path)
    info["from_gcode_s"] = round(time.perf_counter() - t0, 1)
    os.remove(path)
    n = len(result.moves["type"])
    info.update(moves=n, layers=len(result.layers["z"]), rss_gb_after_parse=round(rss_gb(), 2))
    yield 0.2

    sc.slicewright.mode = 'PREVIEW'
    ctl = handler.show_result(sc, result)          # budget: preference or the GPU default (03 7.8)
    rt = runtime.get(sc)
    r = rt.renderer
    info.update(chunks=len(ctl.bounds), budget_mb=ctl.plan.budget // preview_budget.MB,
                planned_vram_mb=round(ctl.plan.bytes_planned / 1e6, 1), with_values=ctl.plan.with_values)
    ticks = []
    while not timers.runner.idle:                  # exactly what Blender's timer does, timed per call
        t0 = time.perf_counter()
        timers.runner.tick()
        ticks.append((time.perf_counter() - t0) * 1e3)
    chunk_ms = [c.timing["total_ms"] for c in r.chunks]
    info["first_tick_ms_with_shader_compile"] = round(ticks[0], 1)
    ticks = ticks[1:] or ticks                      # the first tick also compiles the shaders (once)
    info.update(tick_ms_max=round(max(ticks), 1), tick_ms_median=round(statistics.median(ticks), 1),
                ticks=len(ticks), chunk_build_ms_median=round(statistics.median(chunk_ms), 1),
                chunk_build_ms_max=round(max(chunk_ms), 1))
    g.check("upload_complete", ctl.done and r.uploaded_last == n - 1, error=str(ctl.error))
    p90 = sorted(ticks)[int(0.9 * (len(ticks) - 1))]
    info["tick_ms_p90"] = round(p90, 1)
    # the machine is shared and noisy: gate on the 90th percentile, report the max
    g.check("chunk_build_le_40ms_per_tick", p90 <= 40.0, tick_ms_p90=info["tick_ms_p90"], tick_ms_max=info["tick_ms_max"])

    # ~VISIBLE segments: layers 0..hi
    per_layer = n // len(result.layers["z"])
    p.layer_hi = min(VISIBLE // per_layer - 1, len(result.layers["z"]) - 1)
    p.quality = "TUBES"
    yield 0.3
    fps(10)                                        # warm up
    static = fps(90)
    tex0, calls0 = r.stats["textures_created"], r.stats["draw_calls"]
    p.scrub_moves = True
    frame_t = []
    for k in range(120):
        p.move_pos = (k * 397) % per_layer
        t0 = time.perf_counter()
        fps(1)
        frame_t.append(time.perf_counter() - t0)
    scrub = 1.0 / statistics.mean(frame_t)
    info.update(visible_moves=int(result.layers["last"][p.layer_hi]) + 1, fps_static=round(static, 1),
                fps_scrub_mean=round(scrub, 1), fps_scrub_p95_frame=round(1.0 / sorted(frame_t)[int(0.95 * len(frame_t))], 1),
                draw_calls_per_frame=round((r.stats["draw_calls"] - calls0) / 120, 1))
    g.check("fps_5M_ge_30", static >= 30 and scrub >= 30, fps_static=info["fps_static"], fps_scrub=info["fps_scrub_mean"])
    g.check("scrubbing_creates_no_textures", r.stats["textures_created"] == tex0,
            created=r.stats["textures_created"] - tex0)
    p.scrub_moves = False
    cap.shot(os.path.join(g.OUT, "bench_5M.png"))
    p.layer_hi = len(result.layers["z"]) - 1
    yield 0.2
    info["fps_all_moves_tubes"] = round(fps(30), 1)
    p.quality = "LINES"
    info["fps_all_moves_lines"] = round(fps(30), 1)
    info["rss_gb_peak"] = round(rss_gb(), 2)
    cap.close()
    handler.clear(sc)
    slicewright.unregister()


g.run_steps(steps)
