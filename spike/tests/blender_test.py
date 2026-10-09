# SPDX-License-Identifier: AGPL-3.0-only
"""Run inside Blender: blender -b --factory-startup --python blender_test.py -- <site-dir> <cube.stl> <profile-dir> <outdir>

50 consecutive slices with RSS per slice, then Geometry Nodes, remesh and a Cycles CPU render in the same
session, then more slices. Also records which TBB / allocator libraries the process has loaded."""
import json
import os
import platform
import subprocess
import sys
import time
import traceback

argv = sys.argv[sys.argv.index("--") + 1:]
site, cube, profdir, outdir = argv[:4]
os.makedirs(outdir, exist_ok=True)
sys.path.insert(0, site)

report = {"platform": platform.platform(), "python": sys.version, "stages": {}}


def rss_mb():
    if sys.platform.startswith("linux"):
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
    if sys.platform == "darwin":
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())])
        return int(out.strip()) / 1024
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return pmc.WorkingSetSize / 1048576


def loaded_modules():
    mods = []
    if sys.platform.startswith("linux"):
        with open("/proc/self/maps") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 6 and ".so" in parts[-1]:
                    mods.append(parts[-1])
    elif sys.platform == "darwin":
        import ctypes
        libsys = ctypes.CDLL(None)
        libsys._dyld_image_count.restype = ctypes.c_uint32
        libsys._dyld_get_image_name.restype = ctypes.c_char_p
        libsys._dyld_get_image_name.argtypes = [ctypes.c_uint32]
        for i in range(libsys._dyld_image_count()):
            mods.append(libsys._dyld_get_image_name(i).decode())
    else:
        import ctypes
        from ctypes import wintypes
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        arr = (wintypes.HMODULE * 2048)()
        needed = wintypes.DWORD()
        psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        psapi.EnumProcessModules(k32.GetCurrentProcess(), arr, ctypes.sizeof(arr), ctypes.byref(needed))
        buf = ctypes.create_unicode_buffer(1024)
        for i in range(needed.value // ctypes.sizeof(wintypes.HMODULE)):
            psapi.GetModuleFileNameExW(k32.GetCurrentProcess(), arr[i], buf, 1024)
            mods.append(buf.value)
    return sorted(set(mods))


KEYS = ("tbb", "malloc", "jemalloc", "tcmalloc", "mimalloc", "slicewright", "libstdc", "libc++", "msvcp", "vcruntime", "gmp", "mpfr", "embree", "openimagedenoise", "oidn")


def interesting(mods):
    return [m for m in mods if any(k in os.path.basename(m).lower() for k in KEYS)]


def stage(name):
    def deco(fn):
        t0 = time.time()
        try:
            res = fn()
            report["stages"][name] = {"ok": True, "seconds": round(time.time() - t0, 2), "detail": res}
        except Exception as e:  # noqa: BLE001
            report["stages"][name] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "trace": traceback.format_exc()}
        print("STAGE", name, json.dumps(report["stages"][name], default=str)[:800], flush=True)
        return fn
    return deco


profiles = [os.path.join(profdir, n) for n in ("machine.json", "process.json", "filament.json")]
import bpy  # noqa: E402

report["blender"] = bpy.app.version_string
report["modules_before_import"] = interesting(loaded_modules())
se = None


@stage("import")
def _():
    global se
    import slicewright_engine as m
    se = m
    return {"version": m.version(), "file": m.__file__}


report["modules_after_import"] = interesting(loaded_modules())
slice_rss = []
stats0 = {}


def one_slice(i, tag="s"):
    out = os.path.join(outdir, "%s%02d.gcode" % (tag, i))
    r = se.slice_stl(cube, profiles, out)
    return r


@stage("slice_50")
def _():
    global stats0
    for i in range(1, 51):
        r = one_slice(i)
        if i == 1:
            stats0 = r
        slice_rss.append(rss_mb())
    win = slice_rss[9:50]
    return {"stats_first": stats0, "rss_after_slice": [round(x, 1) for x in slice_rss],
            "rss_growth_10_to_50_mb": round(slice_rss[49] - slice_rss[9], 2),
            "rss_range_10_to_50_mb": round(max(win) - min(win), 2)}


@stage("geometry_nodes")
def _():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    obj = bpy.context.active_object
    ng = bpy.data.node_groups.new("gn", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi = ng.nodes.new("NodeGroupInput")
    go = ng.nodes.new("NodeGroupOutput")
    sub = ng.nodes.new("GeometryNodeSubdivisionSurface")
    sub.inputs["Level"].default_value = 2
    dual = ng.nodes.new("GeometryNodeDualMesh")
    ng.links.new(gi.outputs[0], sub.inputs["Mesh"])
    ng.links.new(sub.outputs[0], dual.inputs[0])
    ng.links.new(dual.outputs[0], go.inputs[0])
    mod = obj.modifiers.new("gn", "NODES")
    mod.node_group = ng
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    n = len(ev.data.vertices)
    assert n > 1000, n
    return {"verts": n}


@stage("remesh")
def _():
    bpy.ops.mesh.primitive_monkey_add()
    obj = bpy.context.active_object
    m = obj.modifiers.new("rm", "REMESH")
    m.mode = "VOXEL"
    m.voxel_size = 0.02
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    n = len(ev.data.polygons)
    assert n > 1000, n
    return {"polys": n}


@stage("cycles_cpu_render")
def _():
    sc = bpy.context.scene
    bpy.ops.object.camera_add(location=(0, -6, 2), rotation=(1.2, 0, 0))
    sc.camera = bpy.context.active_object
    bpy.ops.object.light_add(type="POINT", location=(3, -3, 4))
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 16
    sc.cycles.use_denoising = False
    sc.render.resolution_x = sc.render.resolution_y = 96
    path = os.path.join(outdir, "render.png")
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    assert os.path.getsize(path) > 100
    return {"png_bytes": os.path.getsize(path)}


@stage("cycles_cpu_render_denoise")
def _():
    sc = bpy.context.scene
    sc.cycles.use_denoising = True
    sc.cycles.samples = 8
    path = os.path.join(outdir, "render_dn.png")
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return {"png_bytes": os.path.getsize(path)}


@stage("slice_after_blender_tbb")
def _():
    rs = []
    for i in range(1, 6):
        r = one_slice(i, "post")
        rs.append(r["layers"])
        slice_rss.append(rss_mb())
    return {"layers": rs, "stats_equal_to_first": rs[0] == stats0.get("layers")}


@stage("gcode_hashes")
def _():
    import hashlib
    h = {}
    for n in ("s01.gcode", "s50.gcode", "post01.gcode"):
        p = os.path.join(outdir, n)
        if os.path.exists(p):
            lines = [l for l in open(p, errors="replace") if not l.startswith(";") or "TIME" not in l]
            h[n] = hashlib.sha256("".join(lines).encode()).hexdigest()[:16]
    return h


report["modules_final"] = interesting(loaded_modules())
report["rss_series"] = [round(x, 1) for x in slice_rss]
with open(os.path.join(outdir, "report.json"), "w") as f:
    json.dump(report, f, indent=1, default=str)
print("REPORT_BEGIN")
print(json.dumps(report, indent=1, default=str))
print("REPORT_END")
ok = all(s.get("ok") for s in report["stages"].values())
sys.stdout.flush()
os._exit(0 if ok else 3)
