# SPDX-License-Identifier: GPL-3.0-or-later
"""Palette values (03 Appendix A) and shader source assembly (no GPU needed)."""
from __future__ import annotations

import re

import numpy as np

from fake_engine.api import MOVE_TYPES, ROLES
from slicewright.blender.preview import shaders, templates
from slicewright.core import preview_palette as pp


def test_every_engine_role_and_option_type_has_a_style():
    assert set(ROLES) <= set(pp.ROLE_STYLE)
    marker_and_travel = {"Travel", "Wipe", "Retract", "Unretract", "Seam", "Tool_change",
                         "Color_change", "Pause_print", "Custom_gcode"}
    assert marker_and_travel <= set(pp.OPTION_STYLE) and marker_and_travel <= set(MOVE_TYPES)


def test_palette_array_layout_and_appendix_a_values():
    pal = pp.palette_array(ROLES, MOVE_TYPES, 10.0, 90.0)
    assert pal.shape == (76, 4) and pal.dtype == np.float32
    lin = pp.srgb_to_linear
    assert np.allclose(pal[pp.ROLE_ROW + ROLES["ExternalPerimeter"], :3], (1.0, lin(0x7D / 255), lin(0x38 / 255)))
    assert np.allclose(pal[pp.OPTION_ROW + MOVE_TYPES["Seam"], :3], (lin(0xE6 / 255),) * 3)
    assert np.allclose(pal[pp.OPTION_ROW + MOVE_TYPES["Travel"], :3], pp.rgb_linear(0x38489B))
    assert np.allclose(pal[pp.RANGE_ROW + 10, :3], pp.rgb_linear(0x942616))
    assert tuple(pal[pp.MINMAX_ROW, :2]) == (10.0, 90.0)
    assert (pal[pp.ROLE_ROW + 25] == (0.5, 0.5, 0.5, 1.0)).all()       # unused role id stays grey
    assert len(pp.RANGE_COLORS) == 11 and len(pp.SLOT_COLORS) == 16


def test_srgb_linear_conversion_known_values_and_round_trip():
    assert pp.srgb_to_linear(0.0) == 0.0 and abs(pp.srgb_to_linear(1.0) - 1.0) < 1e-12
    assert abs(pp.srgb_to_linear(0.5) - 0.21404114) < 1e-6             # the classic mid-grey value
    assert abs(pp.srgb_to_linear(0.04045) - 0.04045 / 12.92) < 1e-12   # linear toe
    for i in range(0, 256, 5):
        c = i / 255
        assert abs(pp.linear_to_srgb(pp.srgb_to_linear(c)) - c) < 1e-9
    assert pp.rgb_linear(0xFFFFFF) == (1.0, 1.0, 1.0) or np.allclose(pp.rgb_linear(0xFFFFFF), 1.0)


def test_palette_is_linear_not_raw_srgb_so_the_view_transform_does_not_wash_it_out():
    """The shader's output is encoded by the viewport view transform, so the UBO must hold linear values.
    Raw sRGB bytes here were the cause of the almost-white path colours."""
    pal = pp.palette_array(ROLES, MOVE_TYPES)
    sparse = pal[pp.ROLE_ROW + ROLES["InternalInfill"], :3]           # 0xB03029, a dark red
    assert np.allclose(sparse, pp.rgb_linear(0xB03029))
    assert sparse[0] < pp.rgb(0xB03029)[0] - 0.1 and sparse[1] < 0.05  # darker than the raw bytes
    # Encoding what the shader emits gives back the intended display colour.
    assert np.allclose([pp.linear_to_srgb(float(c)) for c in sparse], pp.rgb(0xB03029), atol=1e-6)
    for name, rid in ROLES.items():
        if name in pp.ROLE_STYLE:
            disp = [pp.linear_to_srgb(float(c)) for c in pal[pp.ROLE_ROW + rid, :3]]
            assert np.allclose(disp, pp.rgb(pp.ROLE_STYLE[name][1]), atol=1e-5)


def test_range_color_is_the_display_colour_of_the_linear_blend():
    assert np.allclose(pp.range_color(0.0), pp.rgb(pp.RANGE_COLORS[0]))
    assert np.allclose(pp.range_color(1.0), pp.rgb(pp.RANGE_COLORS[-1]))
    assert np.allclose(pp.range_color(0.1), pp.rgb(pp.RANGE_COLORS[1]))
    mid = pp.range_color(0.05)                       # halfway between stops 0 and 1, blended the way the shader does
    lin = np.add(pp.rgb_linear(pp.RANGE_COLORS[0]), pp.rgb_linear(pp.RANGE_COLORS[1])) / 2
    assert np.allclose(mid, [pp.linear_to_srgb(float(c)) for c in lin])
    assert pp.range_color(-3) == pp.range_color(0) and pp.range_color(9) == pp.range_color(1)


def test_shader_sources_embed_the_engine_type_ids_by_name():
    vert, frag = shaders.path_sources(MOVE_TYPES, lines=False)
    assert f"T_EXTRUDE = {MOVE_TYPES['Extrude']}u" in vert and "LINES" not in vert.split("void")[0]
    lvert, lfrag = shaders.path_sources(MOVE_TYPES, lines=True)
    assert lvert.startswith("#define LINES") and lfrag.startswith("#define LINES")
    mvert, _ = shaders.marker_sources(MOVE_TYPES)
    assert f"T_TRAVEL = {MOVE_TYPES['Travel']}u" in mvert


def test_sources_follow_the_spike_findings():
    vert, _ = shaders.path_sources(MOVE_TYPES, lines=False)
    assert "floatBitsToUint" in vert and "0x7FFFFFu" in vert          # RG32F meta decode
    assert "uint(u_role_mask)" in vert                                  # INT push constant, cast
    assert "uint corner" not in vert                                    # declared by vertex_in
    assert "gl_InstanceID" in vert and "u_first" in vert
    assert re.search(r"gl_Position = vec4\(2\.0, 2\.0, 2\.0, 1\.0\)", vert)   # rejection = clipped
    assert templates.corner_values(4).dtype == np.uint32               # U32, never U8
    assert templates.STRIP_CORNERS == 4


def test_both_path_fragment_variants_apply_the_saturation_in_the_eye_w_to_the_final_colour():
    """A stale preview is desaturated (03 section 6.3). The saturation rides in ``u_eye.w`` (a separate push
    constant would exceed the 128 bytes every backend guarantees) and the fragment stage of the tube and the line
    variant must both read it, or OpenGL drops the uniform and ``uniform_float`` raises."""
    for lines in (False, True):
        vert, frag = shaders.path_sources(MOVE_TYPES, lines=lines)
        assert "mix(vec3(luma), rgb, u_eye.w)" in frag
        assert frag.index("u_eye.w") > frag.index("vec3 rgb")             # applied after the colour is final
        assert "u_eye.xyz" in vert and "u_eye -" not in vert              # the vertex stage reads the position only
