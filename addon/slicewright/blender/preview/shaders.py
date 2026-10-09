# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview shaders: GLSL sources and ``GPUShaderCreateInfo`` builders (03 sections 7.1, 7.3, 7.9).

Written from the 03 spec and the Phase 0 spike, not from libvgcode. Facts the sources rely on
(all verified on Metal, OpenGL and Vulkan by the spike):

* vertex pulling: the vertex shader fetches a move and its predecessor from per-chunk textures
  with ``texelFetch`` using ``gl_InstanceID`` plus a per-chunk offset push constant;
* the template ``corner`` attribute is **U32** (a U8 attribute aborts Metal);
* ``t_meta`` is RG32F holding float bits; it is decoded with ``floatBitsToUint(...) & 0x7FFFFF``;
* the role mask is an **INT** push constant, cast to ``uint`` before shifting;
* rejected instances emit a position outside the clip volume.

``create_*`` need a GPU context (a GUI Blender); the sources and constants import anywhere.
"""
from __future__ import annotations

from typing import Mapping

TYPEDEF = """
struct Palette {
  vec4 role_colors[32];
  vec4 option_colors[16];
  vec4 range_colors[11];
  vec4 range_minmax;
  vec4 slot_colors[16];
};
"""

# View modes (``u_view_mode``): how a segment is coloured.
VIEW_ROLE, VIEW_RANGE, VIEW_SLOT, VIEW_COVERAGE = 0, 1, 2, 99
# Passes (``u_pass``): which move type a path draw accepts.
PASS_EXTRUDE, PASS_TRAVEL = 0, 1

_HEADER = """
const uint T_EXTRUDE = {Extrude}u;
const uint T_TRAVEL = {Travel}u;
ivec2 tc(int j) {{ return ivec2(j & 8191, j >> 13); }}
"""

PATH_VERT = """
void main() {
  int j = u_first + gl_InstanceID;
  vec4 B = texelFetch(t_pos, tc(j), 0);
  vec4 A = texelFetch(t_pos, tc(j - 1), 0);
  uvec2 m = floatBitsToUint(texelFetch(t_meta, tc(j), 0).rg) & 0x7FFFFFu;
  uint role = m.x & 31u;
  uint type = (m.x >> 5u) & 15u;
  int layer = int(m.y);
  bool travel = (u_pass == 1);
  bool rejected = (type != (travel ? T_TRAVEL : T_EXTRUDE)) ||
                  (!travel && (((uint(u_role_mask) >> role) & 1u) == 0u));
  v_col = vec4(0.0);
  v_right = vec3(0.0);
  v_toeye = vec3(0.0, 0.0, 1.0);
  v_side = 0.0;
  if (rejected) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0);   /* outside the clip volume */
    return;
  }
  uint ci = corner;
#ifdef LINES
  vec3 p = (ci == 0u) ? A.xyz : B.xyz;
  gl_Position = u_vp * vec4(p, 1.0);
  v_toeye = normalize(u_eye - p);   /* also keeps u_eye alive: OpenGL drops unused uniforms and
                                       uniform_float then raises "uniform not found" */
#else
  vec3 d = B.xyz - A.xyz;
  float len = length(d);
  vec3 dir = (len > 1e-6) ? d / len : vec3(1.0, 0.0, 0.0);
  float hw = 0.5 * B.w;
  vec3 mid = 0.5 * (A.xyz + B.xyz);
  vec3 toeye = normalize(u_eye - mid);
  vec3 right = cross(dir, toeye);
  float rl = length(right);
  right = (rl > 1e-5) ? right / rl : vec3(0.0, 0.0, 1.0);
  float along = float((ci >> 1u) & 1u);
  float side = float(ci & 1u) * 2.0 - 1.0;
  vec3 p = mix(A.xyz - dir * hw, B.xyz + dir * hw, along) + right * (side * hw);
  gl_Position = u_vp * vec4(p, 1.0);
  v_right = right;
  v_toeye = toeye;
  v_side = side;
#endif
  vec3 col = pal.role_colors[role].rgb;
  if (travel) {
    col = pal.option_colors[T_TRAVEL].rgb;
  } else if (u_view_mode == 1) {
    float val = texelFetch(t_val, tc(j), 0).g;
    float t = clamp((val - pal.range_minmax.x) / max(pal.range_minmax.y - pal.range_minmax.x, 1e-6),
                    0.0, 1.0) * 10.0;
    int i0 = int(floor(t));
    int i1 = min(i0 + 1, 10);
    col = mix(pal.range_colors[i0].rgb, pal.range_colors[i1].rgb, t - float(i0));
  } else if (u_view_mode == 2) {
    int slot = int(texelFetch(t_val, tc(j), 0).g + 0.5) & 15;
    col = pal.slot_colors[slot].rgb;
  }
  if (!travel && layer < u_grey_below) {
    col = vec3(dot(col, vec3(0.3, 0.59, 0.11)) * 0.45 + 0.1);
  }
  v_col = vec4(col, 1.0);
}
"""

PATH_FRAG = """
void main() {
  if (u_view_mode == 99) {            /* coverage-count debug: additive 1/16 per fragment */
    fragColor = vec4(0.0625, 0.0625, 0.0625, 1.0);
    return;
  }
#ifdef LINES
  fragColor = v_col;
#else
  float nz = sqrt(max(0.0, 1.0 - v_side * v_side));
  vec3 N = normalize(v_right * v_side + v_toeye * nz);
  float ndl = max(dot(N, v_toeye), 0.0);
  float diff = 0.3 + 0.7 * ndl;
  float spec = 0.25 * pow(ndl, 24.0);
  fragColor = vec4(v_col.rgb * diff + vec3(spec), 1.0);
#endif
}
"""

# Markers: one instance per marker; ``t_idx`` (R32F) lists texels of marker moves.
MARKER_VERT = """
void main() {
  int k = u_first + gl_InstanceID;
  int j = int(texelFetch(t_idx, tc(k), 0).r + 0.5);
  vec4 B = texelFetch(t_pos, tc(j), 0);
  uvec2 m = floatBitsToUint(texelFetch(t_meta, tc(j), 0).rg) & 0x7FFFFFu;
  uint type = (m.x >> 5u) & 15u;
  vec2 q = vec2(float(corner & 1u) * 2.0 - 1.0, float((corner >> 1u) & 1u) * 2.0 - 1.0);
  vec4 clip = u_vp * vec4(B.xyz, 1.0);
  gl_Position = clip + vec4(q * u_marker_px / u_viewport * clip.w, -2.0e-4 * clip.w, 0.0);
  v_uv = q;
  v_col = vec4(pal.option_colors[type].rgb, 1.0);
}
"""

MARKER_FRAG = """
void main() {
  float r = dot(v_uv, v_uv);
  if (r > 1.0) { discard; }
  fragColor = vec4(v_col.rgb * (r > 0.55 ? 0.35 : 1.0), 1.0);
}
"""


# The nozzle marker: one quad at ``u_pos`` (the scrub position), drawn on top.
NOZZLE_VERT = """
void main() {
  vec2 q = vec2(float(corner & 1u) * 2.0 - 1.0, float((corner >> 1u) & 1u) * 2.0 - 1.0);
  vec4 clip = u_vp * vec4(u_pos, 1.0);
  gl_Position = clip + vec4(q * u_marker_px / u_viewport * clip.w, -4.0e-4 * clip.w, 0.0);
  v_uv = q;
  v_col = vec4(1.0, 1.0, 1.0, 1.0);
}
"""


def header(type_ids: Mapping[str, int]) -> str:
    return _HEADER.format(Extrude=type_ids["Extrude"], Travel=type_ids["Travel"])


def path_sources(type_ids: Mapping[str, int], lines: bool) -> tuple[str, str]:
    """(vertex, fragment) source for the tube (``lines=False``) or line (LOD, travel) variant."""
    define = "#define LINES\n" if lines else ""
    return define + header(type_ids) + PATH_VERT, define + PATH_FRAG


def marker_sources(type_ids: Mapping[str, int]) -> tuple[str, str]:
    return header(type_ids) + MARKER_VERT, MARKER_FRAG


def _base_info(gpu, samplers: tuple[str, str, str]):
    info = gpu.types.GPUShaderCreateInfo()
    info.typedef_source(TYPEDEF)
    for slot, name in enumerate(samplers):
        info.sampler(slot, 'FLOAT_2D', name)
    info.uniform_buf(0, "Palette", "pal")
    info.push_constant('MAT4', "u_vp")
    info.push_constant('INT', "u_first")
    info.vertex_in(0, 'UINT', "corner")
    info.fragment_out(0, 'VEC4', "fragColor")
    return info


def create_path_shader(type_ids: Mapping[str, int], lines: bool = False):
    """The extrusion/travel shader. Push constants: u_vp, u_eye, u_first, u_grey_below,
    u_role_mask (INT), u_view_mode, u_pass. Samplers: t_pos, t_meta, t_val."""
    import gpu
    info = _base_info(gpu, ("t_pos", "t_meta", "t_val"))
    info.push_constant('VEC3', "u_eye")
    info.push_constant('INT', "u_grey_below")
    info.push_constant('INT', "u_role_mask")
    info.push_constant('INT', "u_view_mode")
    info.push_constant('INT', "u_pass")
    iface = gpu.types.GPUStageInterfaceInfo("slw_path_iface_lines" if lines else "slw_path_iface")
    iface.flat('VEC4', "v_col")
    iface.flat('VEC3', "v_right")
    iface.flat('VEC3', "v_toeye")
    iface.smooth('FLOAT', "v_side")
    info.vertex_out(iface)
    vert, frag = path_sources(type_ids, lines)
    info.vertex_source(vert)
    info.fragment_source(frag)
    return gpu.shader.create_from_info(info)


def create_marker_shader(type_ids: Mapping[str, int]):
    """Marker quads. Push constants: u_vp, u_first, u_viewport (VEC2), u_marker_px (FLOAT).
    Samplers: t_pos, t_meta, t_idx."""
    import gpu
    info = _base_info(gpu, ("t_pos", "t_meta", "t_idx"))
    info.push_constant('VEC2', "u_viewport")
    info.push_constant('FLOAT', "u_marker_px")
    iface = gpu.types.GPUStageInterfaceInfo("slw_marker_iface")
    iface.smooth('VEC2', "v_uv")
    iface.flat('VEC4', "v_col")
    info.vertex_out(iface)
    vert, frag = marker_sources(type_ids)
    info.vertex_source(vert)
    info.fragment_source(frag)
    return gpu.shader.create_from_info(info)


def create_nozzle_shader():
    """Push constants: u_vp, u_pos (VEC3), u_viewport (VEC2), u_marker_px (FLOAT)."""
    import gpu
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('MAT4', "u_vp")
    info.push_constant('VEC3', "u_pos")
    info.push_constant('VEC2', "u_viewport")
    info.push_constant('FLOAT', "u_marker_px")
    info.vertex_in(0, 'UINT', "corner")
    info.fragment_out(0, 'VEC4', "fragColor")
    iface = gpu.types.GPUStageInterfaceInfo("slw_nozzle_iface")
    iface.smooth('VEC2', "v_uv")
    iface.flat('VEC4', "v_col")
    info.vertex_out(iface)
    info.vertex_source(NOZZLE_VERT)
    info.fragment_source(MARKER_FRAG)
    return gpu.shader.create_from_info(info)
