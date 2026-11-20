# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 0 spike (a): chunked vertex-pulling toolpath renderer on Blender's `gpu` module.

Written from docs/design/03-blender-addon.md section 7 (chunk layout 7.2, range math 7.4,
shader sketch 7.9). Not derived from libvgcode. Throwaway spike code.
"""
import math
import os
import time
import zlib
import struct

import numpy as np

W = 8192            # texture width in texels (03 7.2)
M_PER_LAYER = 50000  # synthetic moves per layer
N_ROLES = 14
TYPE_EXTRUDE, TYPE_TRAVEL = 0, 1

# --------------------------------------------------------------------------------------
# Synthetic data


def generate(n_moves, seed=1):
    """Deterministic spiralling multi-layer toolpath. The first K moves are identical for any
    n_moves >= K (per-layer seeding), so a 3M-move reference scene is a prefix of the 10M one."""
    n_layers = n_moves // M_PER_LAYER
    N = n_layers * M_PER_LAYER
    x = np.empty(N, np.float32)
    y = np.empty(N, np.float32)
    z = np.empty(N, np.float32)
    width = np.empty(N, np.float32)
    height = np.empty(N, np.float32)
    role = np.empty(N, np.uint8)
    mtype = np.empty(N, np.uint8)
    layer = np.empty(N, np.uint32)
    speed = np.empty(N, np.float16)
    R = 50.0
    n_ow, n_iw = 800, 780
    K = M_PER_LAYER - (n_ow + n_iw + 3)
    rmax = R - 1.5
    for l in range(n_layers):
        rng = np.random.default_rng(seed * 100003 + l)
        sl = slice(l * M_PER_LAYER, (l + 1) * M_PER_LAYER)
        rot = l * 0.7
        px = np.empty(M_PER_LAYER)
        py = np.empty(M_PER_LAYER)
        ro = np.empty(M_PER_LAYER, np.uint8)
        ty = np.zeros(M_PER_LAYER, np.uint8)
        wd = np.full(M_PER_LAYER, 0.45)
        i = 0
        # travel to outer wall start
        a = rot + np.linspace(0, 2 * math.pi, n_ow + 1)
        px[0], py[0] = R * math.cos(rot), R * math.sin(rot)
        ty[0] = TYPE_TRAVEL
        ro[0] = 0
        i = 1
        px[i:i + n_ow] = R * np.cos(a[1:]); py[i:i + n_ow] = R * np.sin(a[1:]); ro[i:i + n_ow] = 0
        wd[i:i + n_ow] = 0.45 + 0.04 * np.sin(np.arange(n_ow) * 0.05)
        i += n_ow
        a = rot + np.linspace(0, 2 * math.pi, n_iw + 1)
        ri = R - 0.5
        px[i] = ri * math.cos(rot); py[i] = ri * math.sin(rot); ty[i] = TYPE_TRAVEL; ro[i] = 1
        i += 1
        px[i:i + n_iw] = ri * np.cos(a[1:]); py[i:i + n_iw] = ri * np.sin(a[1:]); ro[i:i + n_iw] = 1
        i += n_iw
        # travel to centre then spiral infill
        px[i] = 0.0; py[i] = 0.0; ty[i] = TYPE_TRAVEL; ro[i] = 3
        i += 1
        k = np.arange(1, K + 1, dtype=np.float64)
        r = rmax * np.sqrt(k / K)
        pitch = 0.45
        th = rot + 2 * math.pi * r / pitch
        px[i:] = r * np.cos(th); py[i:] = r * np.sin(th)
        top = l >= n_layers - 5 or l < 3
        ro[i:] = 5 if top else 3          # top/bottom solid vs sparse infill
        ro[i:][r < 10.0] = 4              # internal solid core
        wd[i:] = 0.45 + 0.03 * rng.standard_normal(K).clip(-1, 1)
        # a few travel jumps inside the infill (every ~6000 moves)
        jump = i + np.arange(6000, K, 6000)
        ty[jump] = TYPE_TRAVEL
        x[sl] = px; y[sl] = py
        z[sl] = (l + 1) * 0.2
        width[sl] = wd
        height[sl] = 0.3 if l == 0 else 0.2
        role[sl] = ro
        mtype[sl] = ty
        layer[sl] = l
        spd = np.where(ro == 0, 60.0, np.where(ro == 1, 120.0, np.where(ro == 4, 150.0, 200.0)))
        speed[sl] = spd.astype(np.float16)
    mtype[0] = TYPE_TRAVEL  # move 0 is the start position
    first = np.arange(n_layers, dtype=np.int64) * M_PER_LAYER
    last = first + M_PER_LAYER - 1
    return dict(x=x, y=y, z=z, width=width, height=height, role=role, mtype=mtype, layer=layer,
                speed=speed, layers_first=first, layers_last=last, n=N, n_layers=n_layers)


def chunk_bounds(n, C):
    s = np.arange(0, n, C)
    e = np.minimum(s + C - 1, n - 1)
    return list(zip(s.tolist(), e.tolist()))


# --------------------------------------------------------------------------------------
# Range math (03 7.4) - pure python, no GPU needed


def global_range(soa, lo, hi, p, uploaded_last):
    g_first = int(soa["layers_first"][lo])
    g_last = min(int(soa["layers_first"][hi]) + p, int(soa["layers_last"][hi]), uploaded_last)
    return g_first, g_last


def plan(bounds, g_first, g_last):
    """Return [(chunk_index, u_first, instance_count)] for non-empty chunk ranges only."""
    out = []
    for c, (s, e) in enumerate(bounds):
        f = max(g_first, s)
        l = min(g_last, e)
        if l < f:
            continue                      # never draw_instanced(instance_count=0)
        out.append((c, f - s + 1, l - f + 1))
    return out


# --------------------------------------------------------------------------------------
# Shaders (written from 03 7.9)

TYPEDEF = """
struct Palette {
  vec4 role_colors[32];
  vec4 option_colors[16];
  vec4 range_colors[11];
  vec4 range_minmax;
  vec4 slot_colors[16];
};
"""

VERT_T = """
ivec2 tc(int j) { return ivec2(j & 8191, j >> 13); }
void main() {
  int j = u_first + gl_InstanceID;
  vec4 B = texelFetch(t_pos, tc(j), 0);
  vec4 A = texelFetch(t_pos, tc(j - 1), 0);
#ifdef META_F32
  uvec2 m = floatBitsToUint(texelFetch(t_meta, tc(j), 0).rg) & 0x7FFFFFu;
#else
  uvec2 m = texelFetch(t_meta, tc(j), 0).rg;
#endif
  uint role = m.x & 31u;
  uint type = (m.x >> 5) & 15u;
  int layer = int(m.y);
  bool rejected = (u_view_mode != 98) && ((type != 0u) || (((uint(u_role_mask) >> role) & 1u) == 0u) ||
                  (layer < u_layer_lo) || (layer > u_layer_hi));
  v_col = vec4(0.0);
  v_right = vec3(0.0);
  v_toeye = vec3(0.0, 0.0, 1.0);
  v_side = 0.0;
  if (rejected) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0);   /* outside the clip volume: all 6 verts coincide */
    return;
  }
  vec3 d = B.xyz - A.xyz;
  float len = length(d);
  vec3 dir = (len > 1e-6) ? d / len : vec3(1.0, 0.0, 0.0);
  float hw = 0.5 * B.w;
  vec3 mid = 0.5 * (A.xyz + B.xyz);
  vec3 toeye = normalize(u_eye - mid);
  vec3 right = cross(dir, toeye);
  float rl = length(right);
  right = (rl > 1e-5) ? right / rl : vec3(0.0, 0.0, 1.0);
  uint ci = CORNER_EXPR;
#ifdef STRIP
  float along = float((ci >> 1) & 1u);
  float side = float(ci & 1u) * 2.0 - 1.0;
#else
  float along = float((22u >> ci) & 1u);
  float side = float((52u >> ci) & 1u) * 2.0 - 1.0;
#endif
  vec3 p = mix(A.xyz - dir * hw, B.xyz + dir * hw, along) + right * (side * hw);
  gl_Position = u_vp * vec4(p, 1.0);

  vec3 col = pal.role_colors[role].rgb;
  if (u_view_mode == 1) {
    vec2 val = texelFetch(t_val, tc(j), 0).rg;
    float t = clamp((val.y - pal.range_minmax.x) / max(pal.range_minmax.y - pal.range_minmax.x, 1e-6), 0.0, 1.0) * 10.0;
    int i0 = int(floor(t));
    int i1 = min(i0 + 1, 10);
    col = mix(pal.range_colors[i0].rgb, pal.range_colors[i1].rgb, t - float(i0));
  }
  if (layer < u_grey_below) {
    col = vec3(dot(col, vec3(0.3, 0.59, 0.11)) * 0.45 + 0.1);
  }
  v_col = vec4(col, 1.0);
  v_right = right;
  v_toeye = toeye;
  v_side = side;
}
"""

FRAG = """
void main() {
  if (u_view_mode == 99) {            /* coverage-count debug: additive 1/16 per fragment */
    fragColor = vec4(0.0625, 0.0625, 0.0625, 1.0);
    return;
  }
  float nz = sqrt(max(0.0, 1.0 - v_side * v_side));
  vec3 N = normalize(v_right * v_side + v_toeye * nz);
  float diff = 0.3 + 0.7 * max(dot(N, v_toeye), 0.0);
  float spec = 0.25 * pow(max(dot(N, v_toeye), 0.0), 24.0);
  fragColor = vec4(v_col.rgb * diff + vec3(spec), 1.0);
}
"""


def hex_rgb(h):
    return [((h >> 16) & 255) / 255.0, ((h >> 8) & 255) / 255.0, (h & 255) / 255.0, 1.0]


STRIP = os.environ.get("SLW_TEMPLATE", "strip") == "strip"
def vert_source(meta_mode):
    return (("#define STRIP\n" if STRIP else "") + ("#define META_F32\n" if meta_mode == "f32bits" else "") + VERT_T)


ROLE_COLORS = [0xFF7D38, 0xE63946, 0x1E90FF, 0xB03030, 0x9C27B0, 0xFFC107, 0x4CAF50, 0x00BCD4,
               0xB0BEC5, 0x795548, 0x66BB6A, 0x2E7D32, 0xF06292, 0x9E9E9E]


def palette_array():
    pal = np.zeros((76, 4), np.float32)
    for i, c in enumerate(ROLE_COLORS):
        pal[i] = hex_rgb(c)
    for i in range(11):                       # range colours: blue -> green -> red ramp
        t = i / 10.0
        pal[48 + i] = (min(1, 2 * t), 1 - abs(2 * t - 1), max(0, 1 - 2 * t), 1)
    pal[59] = (50.0, 210.0, 0, 0)             # range_minmax (speed)
    for i in range(16):
        pal[60 + i] = (i / 15.0, 0.5, 1 - i / 15.0, 1)
    return pal


def make_shader(meta_mode="f32bits"):
    import gpu
    info = gpu.types.GPUShaderCreateInfo()
    info.typedef_source(TYPEDEF)
    info.sampler(0, 'FLOAT_2D', "t_pos")
    info.sampler(1, 'FLOAT_2D' if meta_mode == "f32bits" else 'UINT_2D', "t_meta")
    info.sampler(2, 'FLOAT_2D', "t_val")
    info.uniform_buf(0, "Palette", "pal")
    info.push_constant('MAT4', "u_vp")
    info.push_constant('VEC3', "u_eye")
    info.push_constant('INT', "u_first")
    info.push_constant('INT', "u_grey_below")
    info.push_constant('INT', "u_layer_lo")
    info.push_constant('INT', "u_layer_hi")
    info.push_constant('INT', "u_role_mask")   # UINT push constants cannot be set from Python on OpenGL (only uniform_int/float exist)
    info.push_constant('INT', "u_view_mode")
    info.vertex_in(0, _CORNER[1], "corner")
    iface = gpu.types.GPUStageInterfaceInfo("slw_iface")
    iface.flat('VEC4', "v_col")
    iface.flat('VEC3', "v_right")
    iface.flat('VEC3', "v_toeye")
    iface.smooth('FLOAT', "v_side")
    info.vertex_out(iface)
    info.fragment_out(0, 'VEC4', "fragColor")
    info.vertex_source(vert_source(meta_mode).replace("CORNER_EXPR", _CORNER_EXPR))
    info.fragment_source(FRAG)
    return gpu.shader.create_from_info(info)


# variant -> (attr comp_type, shader vertex_in type, fetch mode, len, numpy dtype)
_VARIANTS = {"u8": ('U8', 'UINT', 'INT', 1, np.uint8), "u32": ('U32', 'UINT', 'INT', 1, np.uint32),
             "f32": ('F32', 'FLOAT', 'FLOAT', 1, np.float32), "u8x4": ('U8', 'UVEC4', 'INT', 4, np.uint8)}
_CORNER_EXPR = {"u8": "corner", "u32": "corner", "f32": "uint(corner + 0.5)", "u8x4": "corner.x"}[os.environ.get("SLW_CORNER", "u32")]
_CORNER = _VARIANTS[os.environ.get("SLW_CORNER", "u32")]


def make_template():
    import gpu
    fmt = gpu.types.GPUVertFormat()
    fmt.attr_add(id="corner", comp_type=_CORNER[0], len=_CORNER[3], fetch_mode=_CORNER[2])
    nv = 4 if STRIP else 6
    vbo = gpu.types.GPUVertBuf(fmt, nv)
    d = np.zeros((nv, _CORNER[3]), _CORNER[4]); d[:, 0] = np.arange(nv)
    vbo.attr_fill(id="corner", data=d.reshape(-1) if _CORNER[3] == 1 else d)
    return gpu.types.GPUBatch(type='TRI_STRIP' if STRIP else 'TRIS', buf=vbo), vbo


# --------------------------------------------------------------------------------------
# Chunk packing and upload


def pack_chunk(soa, s, e, meta_mode="f32bits"):
    """Pack moves [s, e] into three per-chunk texture arrays (03 7.2). Texel 0 duplicates
    move s-1 (move 0 for the first chunk). Returns (pos, meta, val, n_texels)."""
    n = e - s + 1
    T = n + 1
    rows = -(-T // W)
    lo = max(s - 1, 0)
    pos = np.zeros((rows * W, 4), np.float32)
    meta = np.zeros((rows * W, 2), np.uint32)
    val = np.zeros((rows * W, 2), np.float32)
    # texel j = move s-1+j  ->  moves [s-1, e] (for s == 0, move 0 appears twice)
    if s == 0:
        # texel 0 duplicates move 0 (the first chunk has no predecessor)
        sl = slice(0, e + 1)
        off = 1
    else:
        sl = slice(lo, e + 1)
        off = 0
    pos[off:T, 0] = soa["x"][sl]; pos[off:T, 1] = soa["y"][sl]; pos[off:T, 2] = soa["z"][sl]; pos[off:T, 3] = soa["width"][sl]
    role = np.empty(T, np.uint32); typ = np.empty(T, np.uint32)
    role[off:] = soa["role"][sl]; typ[off:] = soa["mtype"][sl]
    lay = np.empty(T, np.uint32); lay[off:] = soa["layer"][sl]
    h = np.empty(T, np.float32); h[off:] = soa["height"][sl]
    sc = np.empty(T, np.float32); sc[off:] = soa["speed"][sl]
    if off:
        pos[0] = pos[1]; role[0] = role[1]; typ[0] = typ[1]; lay[0] = lay[1]; h[0] = h[1]; sc[0] = sc[1]
    meta[:T, 0] = role | (typ << 5)      # filament/nozzle/flags bits left zero in the spike
    meta[:T, 1] = lay
    if meta_mode == "f32bits":
        # 23 payload bits (mantissa) under a fixed exponent (0x3F800000 = 1.0f): always a normal, finite float,
        # so no backend can flush, canonicalise or convert it. Shader: floatBitsToUint(..) & 0x7FFFFF.
        meta |= np.uint32(0x3F800000)
    val[:T, 0] = h
    val[:T, 1] = sc
    return pos, meta, val, T


class Chunk:
    __slots__ = ("s", "e", "t_pos", "t_meta", "t_val", "timing", "rows")


class Preview:
    def __init__(self, C=1 << 20, meta_mode="f32bits"):
        self.C = C
        self.meta_mode = meta_mode
        self.chunks = []
        self.shader = None
        self.batch = None
        self.vbo = None
        self.ubo = None
        self.tex_created = 0          # every GPUTexture construction goes through make_tex
        self.draw_calls = 0
        self.uploaded_last = -1

    # ---- GPU object creation -----------------------------------------------------------
    def make_tex(self, w, h, fmt, arr):
        import gpu
        t0 = time.perf_counter()
        buf = gpu.types.Buffer('FLOAT', arr.size, arr.view(np.float32).reshape(-1))
        t1 = time.perf_counter()
        tex = gpu.types.GPUTexture((w, h), format=fmt, data=buf)
        t2 = time.perf_counter()
        self.tex_created += 1
        return tex, (t1 - t0) * 1e3, (t2 - t1) * 1e3

    def ensure_gpu(self):
        import gpu
        if self.shader is None:
            self.shader = make_shader(self.meta_mode)
            self.batch, self.vbo = make_template()
            pal = palette_array()
            self.ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', pal.size, pal.reshape(-1)))
            self._pal = pal

    def build_chunk(self, soa, s, e, register=True):
        t0 = time.perf_counter()
        pos, meta, val, T = pack_chunk(soa, s, e, self.meta_mode)
        t1 = time.perf_counter()
        rows = pos.shape[0] // W
        c = Chunk()
        c.s, c.e, c.rows = s, e, rows
        c.t_pos, b1, t_1 = self.make_tex(W, rows, 'RGBA32F', pos)
        c.t_meta, b2, t_2 = self.make_tex(W, rows, 'RG32F' if self.meta_mode == 'f32bits' else 'RG32UI', meta)
        c.t_val, b3, t_3 = self.make_tex(W, rows, 'RG16F', val)
        t2 = time.perf_counter()
        c.timing = dict(pack_ms=(t1 - t0) * 1e3, buffer_ms=b1 + b2 + b3, texture_ms=t_1 + t_2 + t_3,
                        total_ms=(t2 - t0) * 1e3, tex_pos_ms=t_1, tex_meta_ms=t_2, tex_val_ms=t_3)
        if register:
            self.chunks.append(c)
            self.uploaded_last = e
        return c

    # ---- drawing -------------------------------------------------------------------------
    def bounds(self):
        return [(c.s, c.e) for c in self.chunks]

    def draw(self, soa, vp, eye, lo, hi, p, role_mask=0xFFFFFFFF, grey_below=0, view_mode=0,
             plan_override=None, instance_count_override=None):
        """One draw_instanced per non-empty chunk range. Only uniforms and instance counts change."""
        self.ensure_gpu()
        sh = self.shader
        if plan_override is None:
            g_first, g_last = global_range(soa, lo, hi, p, self.uploaded_last)
            pl =[(self.chunks[ci], uf, cnt) for (ci, uf, cnt) in plan(self.bounds(), g_first, g_last)]
        else:
            pl = plan_override               # [(Chunk, u_first, count)]
        sh.uniform_block("pal", self.ubo)
        sh.uniform_float("u_vp", vp)
        sh.uniform_float("u_eye", eye)
        sh.uniform_int("u_grey_below", grey_below)
        sh.uniform_int("u_layer_lo", lo)
        sh.uniform_int("u_layer_hi", hi)
        sh.uniform_int("u_role_mask", _as_i32(role_mask))
        sh.uniform_int("u_view_mode", view_mode)
        n = 0
        for (ch, u_first, cnt) in pl:
            sh.uniform_int("u_first", u_first)
            sh.uniform_sampler("t_pos", ch.t_pos)
            sh.uniform_sampler("t_meta", ch.t_meta)
            sh.uniform_sampler("t_val", ch.t_val)
            self.batch.draw_instanced(sh, instance_start=0,
                                      instance_count=cnt if instance_count_override is None else instance_count_override)
            n += 1
        self.draw_calls += n
        return n


def _as_i32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= (1 << 31) else v


# --------------------------------------------------------------------------------------
# Small helpers: matrices, PNG


def look_at(eye, target, up=(0, 0, 1)):
    from mathutils import Vector, Matrix
    eye, target, up = Vector(eye), Vector(target), Vector(up)
    f = (target - eye).normalized()
    s = f.cross(up).normalized()
    u = s.cross(f)
    m = Matrix(((s.x, s.y, s.z, -s.dot(eye)),
                (u.x, u.y, u.z, -u.dot(eye)),
                (-f.x, -f.y, -f.z, f.dot(eye)),
                (0, 0, 0, 1)))
    return m


def perspective(fov_deg, aspect, near, far):
    from mathutils import Matrix
    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    return Matrix(((f / aspect, 0, 0, 0), (0, f, 0, 0),
                   (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)), (0, 0, -1, 0)))


def ortho(cx, cy, half_w, half_h, near=-1000.0, far=1000.0):
    from mathutils import Matrix
    return Matrix(((1 / half_w, 0, 0, -cx / half_w), (0, 1 / half_h, 0, -cy / half_h),
                   (0, 0, -2 / (far - near), -(far + near) / (far - near)), (0, 0, 0, 1)))


def buf_to_np(buf):
    """Raw memory of a gpu.types.Buffer as a flat numpy array (zero-copy view).

    Spike finding: for multi-dimensional Buffers (e.g. read_color) the buffer protocol reports
    reversed strides ((1, w, w*h) for dims (w, h, 4)), so np.asarray(buf) is garbage and not
    C-contiguous. The memory itself is plain row-major interleaved; transposing the reported view
    walks it linearly."""
    a = np.asarray(memoryview(buf))
    return a.T.reshape(-1) if a.ndim > 1 else a


def read_rgba(fb, w, h):
    return buf_to_np(fb.read_color(0, 0, w, h, 4, 0, 'UBYTE')).reshape(h, w, 4).copy()


def write_png(path, rgba, flip=True):
    """rgba: (h, w, 4) uint8. Minimal PNG writer (no dependencies)."""
    a = np.ascontiguousarray(rgba[::-1] if flip else rgba)
    h, w, _ = a.shape
    raw = b"".join(b"\x00" + a[i].tobytes() for i in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
