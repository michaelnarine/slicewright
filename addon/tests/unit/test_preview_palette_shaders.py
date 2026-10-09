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
    assert np.allclose(pal[pp.ROLE_ROW + ROLES["ExternalPerimeter"], :3], (1.0, 0x7D / 255, 0x38 / 255))
    assert np.allclose(pal[pp.OPTION_ROW + MOVE_TYPES["Seam"], :3], (0xE6 / 255,) * 3)
    assert np.allclose(pal[pp.OPTION_ROW + MOVE_TYPES["Travel"], :3], pp.rgb(0x38489B))
    assert np.allclose(pal[pp.RANGE_ROW + 10, :3], pp.rgb(0x942616))
    assert tuple(pal[pp.MINMAX_ROW, :2]) == (10.0, 90.0)
    assert (pal[pp.ROLE_ROW + 25] == (0.5, 0.5, 0.5, 1.0)).all()       # unused role id stays grey
    assert len(pp.RANGE_COLORS) == 11 and len(pp.SLOT_COLORS) == 16


def test_range_color_interpolates_between_ramp_stops():
    assert pp.range_color(0.0) == pp.rgb(pp.RANGE_COLORS[0])
    assert pp.range_color(1.0) == pp.rgb(pp.RANGE_COLORS[-1])
    mid = pp.range_color(0.05)
    assert np.allclose(mid, np.add(pp.rgb(pp.RANGE_COLORS[0]), pp.rgb(pp.RANGE_COLORS[1])) / 2)
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
