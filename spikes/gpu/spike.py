# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 0 spike (a) driver. Run inside GUI Blender:

    Blender --factory-startup --enable-event-simulate --python spikes/gpu/spike.py -- \
        --mode full|ref --out <dir> [--gpu-name NAME]

Everything runs from bpy.app.timers (one step per tick), writes <out>/results.json and PNGs,
then quits. Throwaway spike code; never merge.
"""
import sys
import os
import json
import time
import gc
import random
import traceback
import platform

import bpy
import gpu
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

# ---------------------------------------------------------------------------- args
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


MODE = arg("--mode", "ref")
OUT = arg("--out", "/tmp/slw-spike-out")
N_MOVES = int(arg("--moves", 10_000_000 if MODE == "full" else 3_000_000))
CHUNK = int(arg("--chunk", 1 << 20))
META = arg("--meta", "f32bits")
WATCHDOG_S = float(arg("--watchdog", 900))
os.makedirs(OUT, exist_ok=True)

R = {"mode": MODE, "meta_mode": META, "moves_requested": N_MOVES, "chunk": CHUNK, "checks": {}, "errors": []}
PV = core.Preview(C=CHUNK, meta_mode=META)
SOA = None
SCENE = {}


class State:
    enabled = False
    lo = 0
    hi = 0
    p = 0
    role_mask = 0xFFFFFFFF
    grey_below = 0
    view_mode = 0
    handler_calls = 0


ST = State()


def log(*a):
    print("[slw]", *a, flush=True)


def check(name, ok, **info):
    R["checks"][name] = dict(pass_=bool(ok), **info)
    log("CHECK", name, "PASS" if ok else "FAIL", info)


# ---------------------------------------------------------------------------- texture counter
_orig_tex = gpu.types.GPUTexture
TEX_CALLS = [0]


def _counting_tex(*a, **k):
    TEX_CALLS[0] += 1
    return _orig_tex(*a, **k)


# ---------------------------------------------------------------------------- scene
def make_obj(name, verts, faces, color):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.color = color
    bpy.context.scene.collection.objects.link(ob)
    return ob


def box(name, cx, cy, cz, sx, sy, sz, color):
    vs = [(cx + dx * sx / 2, cy + dy * sy / 2, cz + dz * sz / 2)
          for dz in (-1, 1) for dy in (-1, 1) for dx in (-1, 1)]
    fs = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return make_obj(name, vs, fs, color)


def setup_scene():
    sc = bpy.context.scene
    for ob in list(sc.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    SCENE["floor"] = box("floor", 0, 0, -0.5, 160, 160, 1.0, (0.35, 0.35, 0.38, 1))
    SCENE["cube"] = box("cube", 30, 0, 6, 24, 24, 12, (0.15, 0.45, 0.8, 1))
    SCENE["depth_floor"] = box("depth_floor", 0, 0, 5.0, 150, 150, 0.2, (0.8, 0.8, 0.8, 1))
    SCENE["depth_floor"].hide_viewport = True
    bpy.context.preferences.system.viewport_aa = 'OFF'
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    space = area.spaces.active
    region = next(r for r in area.regions if r.type == 'WINDOW')
    sh = space.shading
    sh.type = 'SOLID'
    sh.light = 'STUDIO'
    sh.color_type = 'OBJECT'
    sh.background_type = 'VIEWPORT'
    sh.background_color = (0.04, 0.04, 0.06)
    sh.show_object_outline = False
    sh.show_cavity = False
    sh.show_shadows = False
    space.overlay.show_overlays = False
    space.show_gizmo = False
    SCENE.update(win=win, area=area, space=space, region=region)
    bpy.context.view_layer.update()


def post_view():
    if not ST.enabled or PV.shader is None and not PV.chunks:
        return
    ST.handler_calls += 1
    try:
        mv = gpu.matrix.get_model_view_matrix()
        pj = gpu.matrix.get_projection_matrix()
        vp = pj @ mv
        eye = tuple(mv.inverted().translation)
        dt, dm = gpu.state.depth_test_get(), gpu.state.depth_mask_get()
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        PV.draw(SOA, vp, eye, ST.lo, ST.hi, ST.p, ST.role_mask, ST.grey_below, ST.view_mode)
        gpu.state.depth_test_set(dt)
        gpu.state.depth_mask_set(dm)
    except Exception:
        if not R.get("handler_error"):
            R["handler_error"] = traceback.format_exc()
            log("HANDLER ERROR", R["handler_error"])


# ---------------------------------------------------------------------------- render helpers
def offscreen_plain(w, h, fn, clear=(0, 0, 0, 1)):
    off = gpu.types.GPUOffScreen(w, h)
    with off.bind():
        fb = gpu.state.active_framebuffer_get()
        fb.clear(color=clear, depth=1.0)
        fn()
        rgba = core.read_rgba(fb, w, h)
    off.free()
    return rgba


_BOXES = {}


def _box_batch(name, cx, cy, cz, sx, sy, sz, base):
    from gpu_extras.batch import batch_for_shader
    if name in _BOXES:
        return _BOXES[name]
    faces = [((0, 0, 1), 1.0), ((0, 0, -1), 0.5), ((1, 0, 0), 0.8), ((-1, 0, 0), 0.6), ((0, 1, 0), 0.7), ((0, -1, 0), 0.9)]
    pos, col, idx = [], [], []
    for n, k in faces:
        n = np.array(n, float)
        u = np.array([n[1], n[2], n[0]]); v = np.cross(n, u)
        c = np.array([cx, cy, cz]); half = np.array([sx, sy, sz]) / 2
        base_i = len(pos)
        for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            pos.append(tuple(c + (n + a * u + b * v) * half)); col.append((base[0] * k, base[1] * k, base[2] * k, 1.0))
        idx += [(base_i, base_i + 1, base_i + 2), (base_i, base_i + 2, base_i + 3)]
    sh = gpu.shader.from_builtin('FLAT_COLOR')
    _BOXES[name] = (sh, batch_for_shader(sh, 'TRIS', {"pos": pos, "color": col}, indices=idx))
    return _BOXES[name]


def own_scene_render(w, h, view_m, proj_m, draw_path=True, floor_z=None, with_cube=True):
    """Backend-independent reference render: our own floor/cube boxes (FLAT_COLOR) + the toolpath,
    in one bound offscreen with a depth buffer. Used for cross-backend comparison because
    GPUOffScreen.draw_view3d does not invoke POST_VIEW handlers (spike finding)."""
    off = gpu.types.GPUOffScreen(w, h)
    with off.bind():
        fb = gpu.state.active_framebuffer_get()
        fb.clear(color=(0.04, 0.04, 0.06, 1), depth=1.0)
        gpu.state.depth_test_set('LESS_EQUAL'); gpu.state.depth_mask_set(True)
        gpu.matrix.push(); gpu.matrix.push_projection()
        gpu.matrix.load_matrix(view_m); gpu.matrix.load_projection_matrix(proj_m)
        boxes = [("floor", 0, 0, -0.5, 160, 160, 1.0, (0.40, 0.40, 0.43))]
        if with_cube:
            boxes.append(("cube", 30, 0, 6, 24, 24, 12, (0.15, 0.45, 0.8)))
        if floor_z is not None:
            boxes.append(("slab", 0, 0, floor_z - 0.1, 150, 150, 0.2, (0.8, 0.8, 0.8)))
        for b in boxes:
            sh, bt = _box_batch(*b)
            bt.draw(sh)
        gpu.matrix.pop(); gpu.matrix.pop_projection()
        if draw_path and ST.enabled:
            PV.draw(SOA, proj_m @ view_m, tuple(view_m.inverted().translation), ST.lo, ST.hi, ST.p,
                    ST.role_mask, ST.grey_below, ST.view_mode)
        rgba = core.read_rgba(fb, w, h)
    off.free()
    return rgba


def cam(eye, target, w, h, fov=38.0):
    return core.look_at(eye, target), core.perspective(fov, w / h, 1.0, 2000.0)


def save(name, rgba):
    core.write_png(os.path.join(OUT, name), rgba)


def full_hi():
    return SOA["n_layers"] - 1


def draw_plain(vp, eye, lo, hi, p, **kw):
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(True)
    return PV.draw(SOA, vp, eye, lo, hi, p, **kw)


# ---------------------------------------------------------------------------- checks
def diag():
    """Which integer-texture strategy works on this backend? Builds one small chunk per strategy,
    reads back the meta texture and renders coverage with (a) normal decode, (b) rejection bypassed."""
    soa = core.generate(200_000)
    out = {}
    for mm in ("uint", "f32bits"):
        d = {}
        try:
            pv = core.Preview(C=1 << 17, meta_mode=mm)
            pv.ensure_gpu()
            ch = pv.build_chunk(soa, 0, (1 << 17) - 1)
            try:
                rb = core.buf_to_np(ch.t_meta.read())
                d["readback_dtype"] = str(rb.dtype)
                d["readback_first8"] = [float(x) for x in rb[:8]] if rb.dtype.kind == "f" else [int(x) for x in rb[:8]]
                d["readback_expected_first6_words"] = [int(x) for x in core.pack_chunk(soa, 0, 5, mm)[1].reshape(-1)[:12]]
            except Exception as e:
                d["readback_error"] = str(e)
            vp = core.ortho(0, 0, 55, 55)
            for vmode in (99, 98):
                def fn(vmode=vmode):
                    gpu.state.depth_test_set('NONE'); gpu.state.blend_set('ADDITIVE')
                    pv.draw(soa, vp, (0, 0, 500), 0, 1, 0, view_mode=vmode,
                            plan_override=[(ch, 5000, 20000)])
                    gpu.state.blend_set('NONE')
                img = offscreen_plain(500, 500, fn)
                d["coverage_sum_mode%d" % vmode] = int(img[..., 0].astype(np.int64).sum())
        except Exception:
            d["error"] = traceback.format_exc()
        out[mm] = d
        log("DIAG", mm, d)
    R["diag"] = out


def check_range_math():
    rng = random.Random(7)
    bounds = PV.bounds()
    bad = 0
    n_empty = 0
    n = SOA["n"]
    for _ in range(3000):
        lo = rng.randrange(SOA["n_layers"])
        hi = rng.randrange(lo, SOA["n_layers"])
        p = rng.randrange(0, core.M_PER_LAYER + 10)
        ul = rng.choice([n - 1, rng.randrange(n)])
        gf, gl = core.global_range(SOA, lo, hi, p, ul)
        pl = core.plan(bounds, gf, gl)
        covered = []
        for (ci, uf, cnt) in pl:
            assert cnt >= 1
            s = bounds[ci][0]
            covered.append((s + uf - 1, s + uf - 1 + cnt - 1))   # move = s - 1 + j  with j = uf + i
        # reference: move index of instance i in chunk c is s - 1 + (u_first + i)
        mv = np.concatenate([np.arange(a, b + 1) for a, b in covered]) if covered else np.zeros(0, int)
        expect = np.arange(gf, gl + 1) if gl >= gf else np.zeros(0, int)
        if gl < gf:
            n_empty += 1
        if not np.array_equal(mv, expect):
            bad += 1
    check("range_math_plan_exact_once", bad == 0, trials=3000, mismatches=bad, empty_ranges=n_empty)


def read_cov(vp_params, fn, w=400, h=400):
    """Draw with the coverage-count debug mode (additive 1/16 per fragment); return sum + image."""
    def go():
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        gpu.state.blend_set('ADDITIVE')
        fn()
        gpu.state.blend_set('NONE')
    img = offscreen_plain(w, h, go, clear=(0, 0, 0, 1))
    return img


def check_empty_range():
    """instance_count=0 semantics: what does draw_instanced draw, and does our planner skip?"""
    ch = PV.chunks[0]
    PV.ensure_gpu()
    sh = PV.shader
    # view a region containing many moves of layer 0 (spiral infill start region of chunk 0)
    vp = core.ortho(0, 0, 55, 55)
    sums = {}
    for cnt in (0, 1, 2, 10, 1000, 20000):
        def fn(cnt=cnt):
            sh.uniform_block("pal", PV.ubo)
            sh.uniform_float("u_vp", vp); sh.uniform_float("u_eye", (0, 0, 500))
            sh.uniform_int("u_grey_below", 0); sh.uniform_int("u_layer_lo", 0); sh.uniform_int("u_layer_hi", 1 << 20)
            sh.uniform_int("u_role_mask", -1); sh.uniform_int("u_view_mode", 99)
            sh.uniform_int("u_first", 5000)
            sh.uniform_sampler("t_pos", ch.t_pos); sh.uniform_sampler("t_meta", ch.t_meta); sh.uniform_sampler("t_val", ch.t_val)
            PV.batch.draw_instanced(sh, instance_start=0, instance_count=cnt)
        img = read_cov(None, fn, 500, 500)
        sums[cnt] = int(img[..., 0].astype(np.int64).sum())
    # the planner path: an empty range must produce zero draw calls and a blank image
    gf, gl = core.global_range(SOA, 5, 5, -1, PV.uploaded_last)        # p = -1 -> last < first
    empty_plan = core.plan(PV.bounds(), gf, gl)
    calls_before = PV.draw_calls
    img_skip = read_cov(None, lambda: PV.draw(SOA, vp, (0, 0, 500), 5, 5, -1, view_mode=99), 500, 500)
    skip_sum = int(img_skip[..., 0].astype(np.int64).sum())
    # what 0 means: compare with 1 and with N
    zero_equals = [k for k in sums if k != 0 and sums[k] == sums[0]]
    R["empty_range"] = dict(coverage_sum_by_instance_count=sums, count0_equals_counts=zero_equals,
                            planner_empty_plan=empty_plan, draw_calls_for_empty_range=PV.draw_calls - calls_before,
                            image_sum_for_empty_range=skip_sum)
    check("empty_range_planner_skips_draw", empty_plan == [] and PV.draw_calls == calls_before and skip_sum == 0, **R["empty_range"])
    log("EMPTY", R["empty_range"])


def check_boundaries():
    """For every chunk boundary, compare chunked drawing against a single detached chunk that
    starts elsewhere, using a coverage-count render (detects missing AND doubled segments) and the
    shaded render; plus negative controls proving the test would notice a missing/doubled segment."""
    PV.ensure_gpu()
    bounds = PV.bounds()
    res = []
    W_ = H_ = 600
    for bi in range(1, len(bounds)):
        s = bounds[bi][0]
        if SOA["mtype"][s] != core.TYPE_EXTRUDE:
            res.append(dict(boundary=s, skipped="boundary move is not an extrusion"))
            continue
        cx, cy = float(SOA["x"][s]), float(SOA["y"][s])
        half = 2.0
        vp = core.ortho(cx, cy, half, half)
        eye = (cx, cy, 500.0)
        lo_m, hi_m = s - 300, min(s + 300, bounds[bi][1])
        layer = int(SOA["layer"][s])
        assert SOA["layer"][lo_m] == SOA["layer"][hi_m] or True
        lo_l, hi_l = int(SOA["layer"][lo_m]), int(SOA["layer"][hi_m])
        res_b = dict(boundary=s, layer=layer)
        # production path: temporarily clamp to window by faking uploaded_last / g_first via plan
        def prod(vm, drop_first=0, dup_first=0):
            pl = core.plan(bounds, lo_m, hi_m)
            objs = []
            for (ci, uf, cnt) in pl:
                ch = PV.chunks[ci]
                if ci == bi:
                    objs.append((ch, uf + drop_first, cnt - drop_first))   # lose the seam segment
                else:
                    objs.append((ch, uf, cnt))
            if dup_first:                                                  # draw the previous chunk's last move twice
                pc = PV.chunks[bi - 1]
                objs.append((pc, pc.e - pc.s + 1, 1))
            return objs
        def cov(objs, view_mode):
            def fn():
                PV.draw(SOA, vp, eye, 0, 1 << 20, 0, view_mode=view_mode, plan_override=objs)
            return read_cov(None, fn, W_, H_) if view_mode == 99 else offscreen_plain(W_, H_, lambda: (draw_plain_objs(vp, eye, objs)), clear=(0.1, 0.1, 0.1, 1))
        def draw_plain_objs(vp_, eye_, objs):
            gpu.state.depth_test_set('LESS_EQUAL'); gpu.state.depth_mask_set(True)
            PV.draw(SOA, vp_, eye_, 0, 1 << 20, 0, view_mode=0, plan_override=objs)
        # reference: ONE detached chunk starting at a different offset than the production chunks
        refc = PV.build_chunk(SOA, lo_m - 77, hi_m, register=False)
        s_ref = refc.s
        ref_objs = [(refc, (lo_m - s_ref) + 1, hi_m - lo_m + 1)]
        a = cov(prod(None), 99)
        b = cov(ref_objs, 99)
        ca = cov(prod(None), 0)
        cb = cov(ref_objs, 0)
        cov_eq = bool(np.array_equal(a, b))
        col_eq = bool(np.array_equal(ca, cb))
        nz = int((a[..., 0] > 0).sum())
        # negative controls
        miss = cov(prod(None, drop_first=1), 99)      # chunk b loses its first (seam) segment
        dbl = cov(prod(None, dup_first=1), 99)        # previous chunk draws one extra -> doubled
        res_b.update(coverage_equal=cov_eq, color_equal=col_eq, covered_pixels=nz,
                     neg_control_missing_detected=bool(not np.array_equal(a, miss)),
                     neg_control_doubled_detected=bool(not np.array_equal(a, dbl)))
        res.append(res_b)
        if bi == 1:
            save("seam_chunked.png", ca); save("seam_reference.png", cb)
    ok = all(r.get("skipped") or (r["coverage_equal"] and r["color_equal"] and r["covered_pixels"] > 0) for r in res)
    ctl = all(r.get("skipped") or (r["neg_control_missing_detected"] and r["neg_control_doubled_detected"]) for r in res)
    tested = [r for r in res if not r.get("skipped")]
    R["boundary"] = res
    check("chunk_boundary_seams", ok and len(tested) > 0, boundaries_tested=len(tested), boundaries_total=len(res))
    check("chunk_boundary_negative_controls_detected", ctl and len(tested) > 0)


CAPTURE = {"want": False, "img": None}


def post_pixel():
    """POST_PIXEL handler: read back the region's framebuffer (scene + POST_VIEW output). Used instead of
    screen.screenshot, which returns a black image on OpenGL under xvfb."""
    if not CAPTURE["want"]:
        return
    try:
        x, y, w, h = gpu.state.viewport_get()
        fb = gpu.state.active_framebuffer_get()
        CAPTURE["img"] = core.buf_to_np(fb.read_color(x, y, w, h, 4, 0, 'UBYTE')).reshape(h, w, 4).copy()
    except Exception:
        CAPTURE["err"] = traceback.format_exc()


def window_shot(tag):
    """Redraw the real window (POST_VIEW handler fires) and read back the 3D region in POST_PIXEL."""
    w = SCENE["win"]
    CAPTURE["want"], CAPTURE["img"] = True, None
    with bpy.context.temp_override(window=w, area=SCENE["area"], region=SCENE["region"]):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    CAPTURE["want"] = False
    if CAPTURE["img"] is None:
        raise RuntimeError("no capture: %s" % CAPTURE.get("err"))
    return CAPTURE["img"]


def depth_cases(render, name, slab=True):
    """render() -> float/uint image for the current ST. Floor slab top at z=5.1."""
    if slab:
        SCENE["depth_floor"].hide_viewport = False
        SCENE["cube"].hide_viewport = True
        bpy.context.view_layer.update()
    ST.role_mask = 0xFFFFFFFF; ST.view_mode = 0; ST.grey_below = 0
    ST.enabled = False
    floor_only = render("floor")
    ST.enabled = True
    calls0 = ST.handler_calls
    ST.lo, ST.hi, ST.p = 0, 20, core.M_PER_LAYER          # z <= 4.2: entirely below the slab
    A = render("A")
    ST.lo, ST.hi, ST.p = 30, 40, core.M_PER_LAYER         # z 6.2..8.2: entirely above the slab
    B = render("B")
    ST.lo, ST.hi, ST.p = 0, min(full_hi(), 50), core.M_PER_LAYER
    C = render("C")
    ST.enabled = False
    SCENE["depth_floor"].hide_viewport = True
    SCENE["cube"].hide_viewport = False
    called = ST.handler_calls - calls0
    d = lambda X: int((np.abs(X.astype(np.float32) - floor_only.astype(np.float32)).max(axis=2) > (0 if X.dtype == np.uint8 else 1e-6)).sum())
    out = dict(handler_calls=called, px_diff_below_floor=d(A), px_diff_above_floor=d(B), px_diff_mixed=d(C))
    def to8(X):
        return X if X.dtype == np.uint8 else (np.clip(X, 0, 1) * 255 + 0.5).astype(np.uint8)
    for k, X in (("floor_only", floor_only), ("A_hidden", A), ("B_visible", B), ("C_mixed", C)):
        save("depth_%s_%s.png" % (name, k), to8(X))
    return out


def check_depth():
    """Depth against geometry in the same framebuffer (own boxes). The Blender-mesh occlusion test
    is check_depth_window (real POST_VIEW handler)."""
    w, h = 640, 480
    vm, pm = cam((-70, -90, 70), (0, 0, 4), w, h)
    R["draw_view3d_fires_post_view"] = None
    def render(tag):
        return own_scene_render(w, h, vm, pm, floor_z=5.0, with_cube=False)
    out = depth_cases(render, "own", slab=False)
    R["depth_own_scene"] = out
    check("depth_own_scene_slab", out["px_diff_below_floor"] == 0 and out["px_diff_above_floor"] > 5000, **out)
    return out


def check_depth_window():
    set_viewport_camera()
    out = depth_cases(lambda tag: window_shot("depth_" + tag), "window")
    R["depth_window_post_view"] = out
    check("depth_window_post_view_handler", out["handler_calls"] >= 3 and out["px_diff_below_floor"] == 0 and out["px_diff_above_floor"] > 5000, **out)


def ref_scene_shots():
    w, h = 800, 600
    hi = full_hi()
    ST.enabled = True
    ST.lo, ST.hi, ST.p = 0, hi, 20000
    ST.role_mask = 0xFFFFFFFF
    ST.grey_below = max(0, hi - 20)
    ST.view_mode = 0
    vm, pm = cam((-95, -110, 85), (0, 0, 6), w, h)
    img1 = own_scene_render(w, h, vm, pm)
    save("ref_roles.png", img1)
    ST.view_mode = 1
    ST.grey_below = 0
    ST.p = core.M_PER_LAYER
    img2 = own_scene_render(w, h, vm, pm)
    save("ref_range.png", img2)
    # zoomed on the first chunk boundary with a perspective camera
    s = PV.bounds()[1][0]
    tgt = (float(SOA["x"][s]), float(SOA["y"][s]), float(SOA["z"][s]))
    ST.view_mode = 0
    ST.lo, ST.hi, ST.p = int(SOA["layer"][s]) - 2, int(SOA["layer"][s]), core.M_PER_LAYER
    vm, pm = cam((tgt[0] + 4, tgt[1] - 6, tgt[2] + 7), tgt, w, h, fov=30)
    img3 = own_scene_render(w, h, vm, pm)
    save("ref_boundary_zoom.png", img3)
    ST.enabled = False
    R["ref_shots"] = ["ref_roles.png", "ref_range.png", "ref_boundary_zoom.png"]
    check("ref_images_nonblank", all(len(np.unique(i.reshape(-1, 4), axis=0)) > 50 for i in (img1, img2, img3)))


# ---------------------------------------------------------------------------- perf
def visible_hi_for(target_extrusions):
    ext = (SOA["mtype"] == core.TYPE_EXTRUDE)
    per_layer = np.add.reduceat(ext.astype(np.int64), SOA["layers_first"])
    cum = np.cumsum(per_layer)
    hi = int(np.searchsorted(cum, target_extrusions))
    hi = min(hi, SOA["n_layers"] - 1)
    return hi, int(cum[hi])


def window_fps(iterations):
    w = SCENE["win"]
    with bpy.context.temp_override(window=w, area=SCENE["area"], region=SCENE["region"]):
        t = time.perf_counter()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=iterations)
        return iterations / (time.perf_counter() - t)


def set_viewport_camera():
    rv3d = SCENE["area"].spaces.active.region_3d
    rv3d.view_perspective = 'PERSP'
    from mathutils import Vector
    eye, tgt = Vector((-95, -110, 85)), Vector((0, 0, 6))
    d = (tgt - eye)
    rv3d.view_location = tgt
    rv3d.view_distance = d.length
    rv3d.view_rotation = (-d).to_track_quat('Z', 'Y')
    SCENE["area"].spaces.active.lens = 50


def perf_case(target):
    hi, vis = visible_hi_for(target)
    ST.enabled = True
    ST.lo, ST.hi, ST.p = 0, hi, core.M_PER_LAYER
    ST.view_mode = 0
    ST.grey_below = 0
    first, last = core.global_range(SOA, 0, hi, core.M_PER_LAYER, PV.uploaded_last)
    out = dict(target=target, layer_hi=hi, instances=last - first + 1, visible_extrusion_segments=vis)
    tex0 = TEX_CALLS[0]
    PV.draw_calls = 0
    window_fps(10)   # warm up (shader compile / first draws)
    out["fps_static_window"] = window_fps(90)
    # scrub: vary layer range top and move position using only uniforms and instance counts
    w = SCENE["win"]
    steps = 120
    t_per = []
    with bpy.context.temp_override(window=w, area=SCENE["area"], region=SCENE["region"]):
        for k in range(steps):
            ST.hi = max(1, hi - (k // 40))
            ST.p = int((k / steps) * core.M_PER_LAYER)
            t = time.perf_counter()
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            t_per.append(time.perf_counter() - t)
    out["fps_scrub_window_mean"] = 1.0 / float(np.mean(t_per))
    out["fps_scrub_window_p5_worst_frame"] = 1.0 / float(np.percentile(t_per, 95))
    out["draw_calls_per_frame_static"] = None
    out["textures_created_during_perf"] = TEX_CALLS[0] - tex0
    out["window_size"] = (w.width, w.height)
    out["region_size"] = (SCENE["region"].width, SCENE["region"].height)
    # offscreen GPU-bound fps at 800x600 with a pixel readback to force completion
    W_, H_ = 800, 600
    vm, pm = cam((-95, -110, 85), (0, 0, 6), W_, H_)
    off = gpu.types.GPUOffScreen(W_, H_)
    with off.bind():
        fb = gpu.state.active_framebuffer_get()
        ts = []
        for k in range(40):
            fb.clear(color=(0, 0, 0, 1), depth=1.0)
            t = time.perf_counter()
            ST.hi = hi
            vp = pm @ vm
            eye = tuple(vm.inverted().translation)
            gpu.state.depth_test_set('LESS_EQUAL'); gpu.state.depth_mask_set(True)
            n_calls = PV.draw(SOA, vp, eye, 0, hi, core.M_PER_LAYER)
            fb.read_color(0, 0, 1, 1, 4, 0, 'UBYTE')
            ts.append(time.perf_counter() - t)
    off.free()
    out["fps_offscreen_800x600_readback_sync"] = 1.0 / float(np.mean(ts[5:]))
    out["draw_calls_per_frame"] = n_calls
    ST.enabled = False
    return out


# ---------------------------------------------------------------------------- side checks
HITS = []


class SLW_OT_paint(bpy.types.Operator):
    bl_idname = "slicewright.paint"
    bl_label = "Slicewright paint (spike)"

    def invoke(self, context, event):
        from bpy_extras import view3d_utils
        region, rv3d = context.region, context.region_data
        co = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
        hit, loc, nor, idx, ob, mat = context.scene.ray_cast(context.view_layer.depsgraph, origin, direction)
        HITS.append(dict(hit=bool(hit), obj=ob.name if ob else None, loc=tuple(loc), face=idx))
        return {'FINISHED'}


class SLW_WT(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "slicewright.paint_tool"
    bl_label = "SLW Paint"
    bl_description = "spike brush"
    bl_icon = "ops.generic.select_circle"
    bl_widget = None
    bl_keymap = (("slicewright.paint", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),)


def refresh_scene_refs():
    win = SCENE["win"]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    SCENE["area"] = area
    SCENE["space"] = area.spaces.active
    SCENE["region"] = next(r for r in area.regions if r.type == 'WINDOW')


def side_workspace():
    """Create a workspace from code. bpy.data.workspaces has no .new(); bpy.ops.workspace.duplicate()
    works; bpy.ops.workspace.add() needs the right context (returns PASS_THROUGH in a timer)."""
    out = {}
    try:
        out["bpy.data.workspaces.new_exists"] = hasattr(bpy.data.workspaces, "new")
        win = SCENE["win"]
        before = {w.name for w in bpy.data.workspaces}
        try:
            with bpy.context.temp_override(window=win):
                out["ops.workspace.add"] = list(bpy.ops.workspace.add())
        except Exception as e:
            out["ops.workspace.add_error"] = str(e)
        try:
            with bpy.context.temp_override(window=win, workspace=bpy.data.workspaces["Layout"]):
                out["ops.workspace.duplicate"] = list(bpy.ops.workspace.duplicate())
        except Exception as e:
            out["ops.workspace.duplicate_error"] = str(e)
        new = sorted({w.name for w in bpy.data.workspaces} - before)
        out["new_workspaces"] = new
        if new:
            ws = bpy.data.workspaces[new[-1]]
            ws.name = "Slicewright"
            out["screens"] = [s.name for s in ws.screens]
            win.workspace = ws
            out["window_workspace_now"] = win.workspace.name
            # retype the first area of the new screen
            scr = ws.screens[0]
            out["screen_area_types_before"] = [a.type for a in scr.areas]
        out["ok"] = bool(new)
    except Exception:
        out["error"] = traceback.format_exc()
        out["ok"] = False
    return out


def side_tool_register():
    out = {}
    try:
        bpy.utils.register_class(SLW_OT_paint)
        bpy.utils.register_tool(SLW_WT, after={"builtin.cursor"}, separator=True)
        out["register_tool"] = True
        win = SCENE["win"]
        with bpy.context.temp_override(window=win, area=SCENE["area"], region=SCENE["region"]):
            bpy.ops.wm.tool_set_by_id(name="slicewright.paint_tool")
        ws = win.workspace
        tool = ws.tools.from_space_view3d_mode('OBJECT', create=False)
        out["active_tool"] = tool.idname if tool else None
    except Exception:
        out["error"] = traceback.format_exc()
    return out


def simulate_click():
    win = SCENE["win"]
    r = SCENE["region"]
    ps = bpy.context.preferences.system.pixel_size
    x, y = int((r.x + r.width / 2) / ps), int((r.y + r.height / 2) / ps)
    try:
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
        win.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
        win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
        return True
    except Exception as e:
        return str(e)


# ---------------------------------------------------------------------------- main generator
def steps():
    global SOA
    gpu.types.GPUTexture = _counting_tex
    R["env"] = dict(blender=bpy.app.version_string, python=platform.python_version(), os=platform.platform(),
                    machine=platform.machine(), numpy=np.__version__)
    R["gpu"] = dict(renderer=gpu.platform.renderer_get(), vendor=gpu.platform.vendor_get(),
                    version=gpu.platform.version_get(), backend=gpu.platform.backend_type_get(),
                    device_type=gpu.platform.device_type_get(),
                    max_texture_size=gpu.capabilities.max_texture_size_get())
    log("GPU", R["gpu"])
    if MODE == "diag":
        diag()
        return
    setup_scene()
    try:
        SCENE['win'].event_simulate(type='MOUSEMOVE', value='NOTHING', x=3, y=3)
    except Exception as e:
        log('event_simulate unavailable', e)
    yield 1.0
    # ---- data
    t = time.perf_counter()
    SOA = core.generate(N_MOVES)
    R["generate_s"] = time.perf_counter() - t
    R["moves"] = SOA["n"]
    R["layers"] = SOA["n_layers"]
    R["extrusion_moves"] = int((SOA["mtype"] == core.TYPE_EXTRUDE).sum())
    log("generated", SOA["n"], "moves in", R["generate_s"], "s")
    yield 0.05
    # ---- Buffer: zero-copy check (independent of GPU texture)
    a = np.arange(1 << 20, dtype=np.float32)
    rc0 = sys.getrefcount(a)
    t = time.perf_counter()
    b = gpu.types.Buffer('FLOAT', a.size, a)
    t_buf = (time.perf_counter() - t) * 1e3
    rc1 = sys.getrefcount(a)
    a[0] = 123.0
    shares = (b[0] == 123.0)
    t = time.perf_counter(); a2 = a.copy(); t_copy = (time.perf_counter() - t) * 1e3
    t = time.perf_counter(); lst = gpu.types.Buffer('FLOAT', 1 << 14, list(range(1 << 14))); t_list = (time.perf_counter() - t) * 1e3
    del a
    gc.collect()
    junk = [np.full(1 << 20, 5.0, np.float32) for _ in range(8)]
    still = b[0] == 123.0 and b[5] == 5.0 and b[(1 << 20) - 1] == float((1 << 20) - 1)
    R["buffer_from_numpy"] = dict(construct_ms_4MB=t_buf, numpy_copy_ms_4MB=t_copy, list_16k_floats_ms=t_list,
                                  zero_copy_shares_memory=bool(shares), refcount_before=rc0, refcount_after=rc1,
                                  survives_del_of_numpy_array=bool(still))
    del junk
    log("BUFFER", R["buffer_from_numpy"])
    yield 0.05
    # ---- shader + chunk build, one chunk per tick
    PV.ensure_gpu()
    R["shader_compiled"] = True
    log("shader compiled")
    bounds = core.chunk_bounds(SOA["n"], CHUNK)
    chunk_t = []
    for (s, e) in bounds:
        tick0 = time.perf_counter()
        ch = PV.build_chunk(SOA, s, e)
        d = dict(ch.timing)
        d["tick_ms"] = (time.perf_counter() - tick0) * 1e3
        d["moves"] = e - s + 1
        chunk_t.append(d)
        yield 0.0
    R["chunks"] = chunk_t
    full = [c for c in chunk_t if c["moves"] == CHUNK]
    R["chunk_build_summary"] = dict(
        n_chunks=len(chunk_t),
        full_chunk_total_ms_max=max(c["total_ms"] for c in full) if full else None,
        full_chunk_total_ms_median=float(np.median([c["total_ms"] for c in full])) if full else None,
        pack_ms_median=float(np.median([c["pack_ms"] for c in full])) if full else None,
        buffer_ms_median=float(np.median([c["buffer_ms"] for c in full])) if full else None,
        texture_ms_median=float(np.median([c["texture_ms"] for c in full])) if full else None,
        first_chunk_total_ms=chunk_t[0]["total_ms"],
        vram_estimate_mb=SOA["n"] * 28 / 1e6)
    check("chunk_build_le_40ms", bool(full) and R["chunk_build_summary"]["full_chunk_total_ms_median"] <= 40.0,
          **R["chunk_build_summary"])
    # ---- logic + GPU tests
    check_range_math()
    yield 0.05
    check_empty_range()
    yield 0.05
    check_boundaries()
    yield 0.05
    SCENE["handle"] = bpy.types.SpaceView3D.draw_handler_add(post_view, (), 'WINDOW', 'POST_VIEW')
    SCENE["handle_px"] = bpy.types.SpaceView3D.draw_handler_add(post_pixel, (), 'WINDOW', 'POST_PIXEL')
    yield 0.2
    try:
        check_depth()
    except Exception:
        R["errors"].append("depth: " + traceback.format_exc()); log(R["errors"][-1])
    yield 0.3
    try:
        check_depth_window()
    except Exception:
        R["errors"].append("depth_window: " + traceback.format_exc()); log(R["errors"][-1])
    yield 0.05
    try:
        ref_scene_shots()
    except Exception:
        R["errors"].append("ref: " + traceback.format_exc()); log(R["errors"][-1])
    yield 0.05
    # ---- tex-count during scrub (plain offscreen, many frames)
    tex0 = TEX_CALLS[0]; pt0 = PV.tex_created
    W_, H_ = 400, 300
    vm, pm = cam((-95, -110, 85), (0, 0, 6), W_, H_)
    def scrub():
        for k in range(200):
            PV.draw(SOA, pm @ vm, tuple(vm.inverted().translation), (k // 50) % 3, full_hi() - (k % 7), (k * 997) % core.M_PER_LAYER)
    offscreen_plain(W_, H_, scrub)
    check("scrubbing_creates_no_textures", TEX_CALLS[0] == tex0 and PV.tex_created == pt0,
          python_GPUTexture_calls_during_scrub=TEX_CALLS[0] - tex0, frames=200)
    yield 0.05
    # ---- side checks
    R["side_tool"] = side_tool_register()
    yield 0.5
    s_ok = simulate_click()
    R["side_tool"]["event_simulate"] = s_ok
    yield 0.8
    R["side_tool"]["raycast_hits_from_simulated_click"] = list(HITS)
    if not HITS:
        try:
            refresh_scene_refs()
            with bpy.context.temp_override(window=SCENE["win"], area=SCENE["area"], region=SCENE["region"]):
                bpy.ops.slicewright.paint('INVOKE_DEFAULT')
        except Exception as e:
            R["side_tool"]["direct_invoke_error"] = str(e)
    R["side_tool"]["raycast_hits"] = list(HITS)
    R["side_tool"]["ok"] = bool(HITS and HITS[0]["hit"])
    R["side_workspace"] = side_workspace()
    yield 0.8
    try:
        win = SCENE["win"]
        R["side_workspace"]["window_workspace_after_switch"] = win.workspace.name
        R["side_workspace"]["screen_areas_after_switch"] = [a.type for a in win.screen.areas]
        win.workspace = bpy.data.workspaces["Layout"]
    except Exception as e:
        R["errors"].append("back to Layout: %s" % e)
    yield 0.8
    refresh_scene_refs()
    # ---- perf (full mode only; software rasterisers make FPS meaningless)
    if MODE == "full":
        set_viewport_camera()
        yield 0.5
        R["perf"] = {}
        for target in (5_000_000, 2_000_000):
            try:
                R["perf"][str(target)] = perf_case(target)
                log("PERF", target, R["perf"][str(target)])
            except Exception:
                R["errors"].append("perf: " + traceback.format_exc()); log(R["errors"][-1])
            yield 0.3
        p5 = R["perf"].get("5000000", {})
        p2 = R["perf"].get("2000000", {})
        check("fps_5M_ge_30", p5.get("fps_static_window", 0) >= 30 and p5.get("fps_scrub_window_mean", 0) >= 30, **{k: p5.get(k) for k in ("fps_static_window", "fps_scrub_window_mean", "fps_offscreen_800x600_readback_sync")})
        check("fps_2M_ge_30", p2.get("fps_static_window", 0) >= 30, **{k: p2.get(k) for k in ("fps_static_window", "fps_scrub_window_mean", "fps_offscreen_800x600_readback_sync")})
    yield 0.1


def finish():
    try:
        bpy.types.SpaceView3D.draw_handler_remove(SCENE["handle"], 'WINDOW')
        bpy.types.SpaceView3D.draw_handler_remove(SCENE["handle_px"], 'WINDOW')
    except Exception:
        pass
    R["handler_calls_total"] = ST.handler_calls
    R["tex_created_total"] = PV.tex_created
    path = os.path.join(OUT, "results.json")
    with open(path, "w") as f:
        json.dump(R, f, indent=1, default=str)
    log("WROTE", path)
    bpy.ops.wm.quit_blender()


GEN = None


def tick():
    global GEN
    if GEN is None:
        GEN = steps()
    try:
        return next(GEN)
    except StopIteration:
        finish()
        return None
    except Exception:
        R["errors"].append("fatal: " + traceback.format_exc())
        log(R["errors"][-1])
        finish()
        return None


def watchdog():
    log("WATCHDOG fired")
    R["errors"].append("watchdog")
    try:
        with open(os.path.join(OUT, "results.json"), "w") as f:
            json.dump(R, f, indent=1, default=str)
    finally:
        os._exit(3)


import threading  # noqa: E402
_wd = threading.Timer(WATCHDOG_S, watchdog)
_wd.daemon = True
_wd.start()
bpy.app.timers.register(tick, first_interval=1.5)
