# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI Blender: chunked preview of a ``from_gcode`` print, screenshots and correctness checks.

    Blender --factory-startup [--gpu-backend opengl|vulkan] --python gui_preview.py -- --out DIR \
        [--moves 1000000] [--chunk 262144]

Draws offscreen (``GPUOffScreen`` + ``read_color``: ``POST_VIEW`` handlers do not run offscreen),
checks chunk-boundary seams against a single detached chunk with a coverage-count render, that
empty ranges issue no draw call, and that no texture is created while drawing; writes
``ref_*.png`` for the cross-backend perceptual diff.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

N_MOVES = int(g.arg("--moves", 1_000_000))
CHUNK = int(g.arg("--chunk", 1 << 18))       # small chunks so a short print crosses several boundaries
W, H = 960, 720


def main():
    import gpu
    import numpy as np
    from fake_engine import api
    from slicewright.blender.preview.renderer import DrawParams, Renderer
    from slicewright.blender.preview import shaders
    from slicewright.core import preview_data as pd

    result = g.make_result(N_MOVES)
    moves, layers = result.moves, result.layers
    n = len(moves["type"])
    r = Renderer(api.enums())
    bounds = r.begin(n, layers, CHUNK)
    scalar = pd.view_scalar(moves, layers, "speed")
    r.set_range(*pd.value_range(scalar, moves["type"] == api.MOVE_TYPES["Extrude"]))
    times = []
    for b in bounds:
        times.append(r.build_chunk(moves, b, scalar).timing["total_ms"])
    g.RESULTS["info"].update(moves=n, chunks=len(bounds), build_ms_median=round(statistics.median(times), 2),
                             build_ms_max=round(max(times), 2), layers=len(layers["z"]))
    g.check("chunks_built", len(r.chunks) == len(bounds) > 1)

    view_m = g.look_at((75, -105, 70), (0, 0, 2))
    proj_m = g.perspective(38.0, W / H, 1.0, 2000.0)
    vp, eye = proj_m @ view_m, tuple(view_m.inverted().translation)

    def shot(name, params, clear=(0.04, 0.04, 0.06, 1.0)):
        img = g.offscreen(W, H, lambda: r.draw(vp, eye, params), clear)
        g.write_png(os.path.join(g.OUT, name), img)
        return img

    textures_before = r.stats["textures_created"]
    full = DrawParams(viewport=(W, H))
    roles = shot("ref_roles.png", full)
    covered = int((roles[..., :3].astype(int).sum(axis=2) > 60).sum())
    g.check("roles_render_has_content", covered > 5000, covered_pixels=covered)
    shot("ref_range.png", DrawParams(view_mode=shaders.VIEW_RANGE, lo=3, hi=14, p=12000, grey_below=8,
                                     viewport=(W, H)))
    mask = ~(1 << api.ROLES["Perimeter"]) & 0xFFFFFFFF
    masked = shot("ref_masked.png", DrawParams(role_mask=mask, viewport=(W, H)))
    g.check("role_mask_changes_the_image", not np.array_equal(roles, masked))
    # ---- passes: travel, markers, nozzle marker and the lines LOD
    from mathutils import Vector
    seams = np.flatnonzero(moves["type"] == api.MOVE_TYPES["Seam"])
    g.check("fake_gcode_has_seam_markers", len(seams) >= 3, seams=len(seams))
    seam_pos = moves["position"][int(seams[-1])]
    clip = vp @ Vector((*[float(c) for c in seam_pos], 1.0))
    px, py = int((clip.x / clip.w * 0.5 + 0.5) * W), int((clip.y / clip.w * 0.5 + 0.5) * H)
    kinds = ("Retract", "Unretract", "Seam")
    base = shot("ref_passes_off.png", DrawParams(viewport=(W, H)))
    passes = shot("ref_passes.png", DrawParams(show_travel=True, markers=kinds, marker_px=7.0,
                                               nozzle=tuple(float(c) for c in moves["position"][-1]),
                                               viewport=(W, H)))
    g.check("passes_change_the_image", not np.array_equal(base, passes))
    near = passes[max(py - 1, 0):py + 2, max(px - 1, 0):px + 2, :3].reshape(-1, 3).astype(int)
    seam_rgb = np.array([0xE6, 0xE6, 0xE6])
    g.check("seam_marker_drawn_in_seam_colour", bool((np.abs(near - seam_rgb).max(axis=1) < 16).any()),
            at=[px, py], rgb=near[len(near) // 2].tolist())
    lod = shot("ref_lines.png", DrawParams(lines_lod=True, viewport=(W, H)))
    g.check("lines_lod_draws", int((lod[..., :3].astype(int).sum(axis=2) > 60).sum()) > 2000)
    # a stale or zero mask must hide everything (the spike's GL failure mode)
    nothing = g.offscreen(W, H, lambda: r.draw(vp, eye, DrawParams(role_mask=0)))
    g.check("zero_role_mask_draws_nothing", int((nothing[..., :3].astype(int).sum(axis=2) > 60).sum()) == 0)

    calls = r.stats["draw_calls"]
    empty = r.draw(vp, eye, DrawParams(lo=5, hi=5, p=-1))
    gpu_empty = r.plan(DrawParams(lo=5, hi=4))
    g.check("empty_range_issues_no_draw", empty == 0 and gpu_empty == [] and r.stats["draw_calls"] == calls)

    # ---- chunk boundaries: production chunking vs one detached chunk at a different offset
    ref = Renderer(api.enums())
    ref.layers = layers
    bad, tested = [], 0
    for b in bounds[1:]:
        s = b.s
        if moves["type"][s] != api.MOVE_TYPES["Extrude"]:
            continue
        tested += 1
        cx, cy = float(moves["position"][s][0]), float(moves["position"][s][1])
        vpz = g.ortho(cx, cy, 2.0, 2.0)
        zeye = (cx, cy, 500.0)
        lo_m, hi_m = s - 300, min(s + 300, b.e)
        cov = DrawParams(view_mode=shaders.VIEW_COVERAGE, viewport=(600, 600))

        def render(plan, rr, params=cov):
            def draw():
                gpu.state.blend_set('ADDITIVE')
                rr.draw_plan(plan, vpz, zeye, params)
                gpu.state.blend_set('NONE')
            return g.offscreen(600, 600, draw, (0, 0, 0, 1))

        prod = pd.plan_ranges(bounds, lo_m, hi_m)
        a = render(prod, r)
        detached = pd.ChunkBounds(0, lo_m - 77, hi_m)
        ref.bounds, ref.chunks, ref.uploaded_last = [detached], [], hi_m
        ref.chunks.append(ref.build_chunk(moves, detached, scalar, register=False))
        c = render(pd.plan_ranges([detached], lo_m, hi_m), ref)
        missing = [pd.DrawRange(d.chunk, d.first_move + (d.chunk == b.index), d.u_first + (d.chunk == b.index),
                                d.count - (d.chunk == b.index)) for d in prod]
        n_missing = render(missing, r)
        if not (np.array_equal(a, c) and int((a[..., 0] > 0).sum()) > 0 and not np.array_equal(a, n_missing)):
            bad.append(s)
        if tested == 1:
            g.write_png(os.path.join(g.OUT, "seam_chunked.png"), a * 8)
    g.check("chunk_boundary_seams_exact", tested >= 1 and not bad, boundaries=tested, bad=bad)

    g.check("no_textures_created_while_drawing", r.stats["textures_created"] == textures_before,
            draws=r.stats["draw_calls"], created=r.stats["textures_created"] - textures_before)


g.run(main)
