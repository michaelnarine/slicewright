# SPDX-License-Identifier: GPL-3.0-or-later
"""The Bake panel's choices (03 section 7.11): what to bake and how.

Stored on the **window manager** like the Send panel's (``send_pg.py``): the choices last for the session and never ride
along in a shared .blend. No ``from __future__ import annotations`` here: Blender evaluates stringified property
annotations without this module's globals (see ``props.py``).
"""
import bpy
from bpy.props import BoolProperty, BoolVectorProperty, EnumProperty, IntProperty

from ..core import bake, bake_plan as bp


class SLICEWRIGHT_PG_Bake(bpy.types.PropertyGroup):
    kind: EnumProperty(
        name="Bake to", default=bp.CURVES,
        items=[(bp.CURVES, "Curves", "Hair curves, one per extrusion path, rendered as tubes by Cycles and EEVEE"),
               (bp.MESH, "Mesh", "A swept polygonal prism for every segment (much heavier than curves)")])
    use_preview: BoolProperty(
        name="Use the preview's range and features", default=True,
        description="Bake what the Preview shows: its layer range, scrub position and visible features")
    layer_lo: IntProperty(name="Bottom layer", min=0, default=0, description="First layer to bake (0-based)")
    layer_hi: IntProperty(name="Top layer", min=0, default=100000, description="Last layer to bake (0-based)")
    roles: BoolVectorProperty(name="Features", size=32, default=(True,) * 32,
                              description="Feature types to bake (extrusions)")
    travel: BoolProperty(name="Travel moves", default=False,
                         description="Also bake travel moves as thin curves or prisms")
    sides: IntProperty(name="Sides", min=bake.MIN_SIDES, max=bake.MAX_SIDES, default=bake.DEFAULT_SIDES,
                       description="Corners of the cross-section of a mesh bake (4 is a box, 6 a hexagon)")
    part_segments: IntProperty(
        name="Segments per object", min=bp.MIN_PART_SEGMENTS, max=bp.MAX_PART_SEGMENTS, default=bp.DEFAULT_PART_SEGMENTS,
        description="A mesh bake is split into objects of at most this many segments so no step freezes the "
                    "interface. Larger parts mean fewer objects but longer pauses")


classes = (SLICEWRIGHT_PG_Bake,)
WM_PROP = "slicewright_bake"


def of(context):
    """The choices of this window manager, or None while the add-on is not registered."""
    return getattr(context.window_manager, WM_PROP, None)
