# SPDX-License-Identifier: GPL-3.0-or-later
"""Planning a bake (03 section 7.11): what it will cost, whether to ask first, and what to call things. No ``bpy``.

The estimates count what Blender holds once the bake exists (attribute arrays, topology), not the transient numpy arrays,
which a mesh bake keeps to one part. They are rough (a few tens of percent) and err on the high side; they exist so a huge
slice asks before it eats the machine's memory, and so the user is told the vertex count first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from . import bake
from . import preview_palette as palette

CURVES, MESH = "CURVES", "MESH"
KINDS = (CURVES, MESH)
KIND_LABEL = {CURVES: "curves", MESH: "mesh"}

COLLECTION_NAME = "Slicewright Bake"
EXTRAS_NAME = "Plate extras"             # the skirt, the wipe tower and custom G-code belong to no object
MAX_NAME_BYTES = 200                     # Blender names hold 255 bytes; room for the suffixes below

# Custom properties that mark what the add-on made (the only things a re-bake may replace).
TAG = "slicewright_bake"                 # on objects, curves, meshes and the collection: the bake kind
TAG_SOURCE = "slicewright_bake_source"   # on objects and their data: the source object's name ("" for the extras)
TAG_SCENE = "slicewright_bake_scene"     # the scene it was baked from
TAG_FEATURES = "slicewright_features"    # on objects: {feature_type id: name}
TAG_MATERIAL = "slicewright_bake_material"

MB = 1 << 20
CONFIRM_BYTES = 512 * MB                 # above this the user is asked first
REFUSE_BYTES = 4096 * MB                 # above this the bake is not done: narrow it or use curves
DEFAULT_PART_SEGMENTS = 50_000           # mesh segments per datablock: one finalising step is then about 20 ms
MIN_PART_SEGMENTS, MAX_PART_SEGMENTS = 5_000, 2_000_000
STEP_POINTS = 250_000                    # curve points per numpy step
STEP_SEGMENTS = 16_000                   # mesh segments per numpy step

# Bytes per item that Blender keeps (position 12, radius 4, seven attributes of 4; curve offset, type and material 9).
CURVE_BYTES_PER_POINT = 12 + 4 + 4 * len(bake.ATTRIBUTES)
CURVE_BYTES_PER_CURVE = 9 + 4
# A prism of ``s`` sides: 2s vertices (12 B), 4s corners (vertex and edge index, 8 B), 3s edges (8 B), s faces (4 B offset)
# and eight 4 B attributes on each of its s faces. The caps and the selection flags are in the margin.
MESH_BYTES_PER_SIDE = 2 * 12 + 4 * 8 + 3 * 8 + 4 + 8 * 4


@dataclass(frozen=True)
class Estimate:
    kind: str
    segments: int
    paths: int
    vertices: int          # mesh vertices, or curve points
    faces: int             # mesh faces; 0 for curves
    nbytes: int

    @property
    def level(self) -> str:
        """``ok``, ``confirm`` (ask first) or ``refuse``."""
        if self.nbytes > REFUSE_BYTES:
            return "refuse"
        return "confirm" if self.nbytes > CONFIRM_BYTES else "ok"

    def describe(self) -> str:
        """One line for the panel and the popup: counts and memory."""
        if self.kind == CURVES:
            what = f"{self.paths:,} curves, {self.vertices:,} points"
        else:
            what = f"{self.vertices:,} vertices, {self.faces:,} faces"
        return f"{what}, about {format_bytes(self.nbytes)}"


def format_bytes(n: int) -> str:
    return f"{n / (1 << 30):.1f} GB" if n >= (1 << 30) else f"{max(1, round(n / MB))} MB"


def estimate(kind: str, segments: int, paths: int, sides: int = bake.DEFAULT_SIDES) -> Estimate:
    """The cost of baking ``segments`` segments in ``paths`` paths as ``kind``."""
    if kind == CURVES:
        points = segments + paths
        # peak: the numpy arrays and Blender's copy exist together until the last attribute is set
        return Estimate(kind, segments, paths, points, 0,
                        2 * (points * CURVE_BYTES_PER_POINT + paths * CURVE_BYTES_PER_CURVE))
    if kind == MESH:
        faces = segments * sides + 2 * paths
        return Estimate(kind, segments, paths, 2 * sides * segments, faces,
                        segments * sides * MESH_BYTES_PER_SIDE + 2 * paths * sides * 8)
    raise ValueError(f"unknown bake kind {kind!r}")


def verdict_text(est: Estimate) -> str:
    """What to tell the user about ``est``: why to ask, or why not to go on."""
    if est.level == "refuse":
        return (f"This bake is too large ({est.describe()}). Narrow the layer range, switch feature types off, "
                "or bake to curves")
    if est.level == "confirm":
        return f"This is a large bake ({est.describe()}). It can take a while and a lot of memory"
    return est.describe()


# ----------------------------------------------------------------------------- names

def truncate_name(text: str, max_bytes: int = MAX_NAME_BYTES) -> str:
    """``text`` cut to ``max_bytes`` of UTF-8 without splitting a character."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", "ignore")


def source_name(objects, group: int) -> str:
    """The source object's name for ``group`` (an ``object_id``), or :data:`EXTRAS_NAME` for no object."""
    if 0 <= group < len(objects):
        return str(objects[group]) or EXTRAS_NAME
    return EXTRAS_NAME


def object_name(source: str, kind: str, part: int = 0, parts: int = 1) -> str:
    """``Cube Bake (curves)``, ``Cube Bake (mesh)`` and, for a mesh in several datablocks, ``Cube Bake (mesh 02)``."""
    label = KIND_LABEL[kind]
    if parts > 1:
        label = f"{label} {part + 1:02d}"
    return f"{truncate_name(source)} Bake ({label})"


# ----------------------------------------------------------------------------- materials and feature names

def feature_label(key: int, role_names: Mapping[int, str]) -> str:
    """The legend's name for a feature type: ``Outer wall``, ``Travel``."""
    if key == bake.TRAVEL_KEY:
        return palette.OPTION_STYLE["Travel"][0]
    name = role_names.get(key, f"Feature {key}")
    return palette.ROLE_STYLE.get(name, (name, 0))[0]


def feature_color(key: int, role_names: Mapping[int, str]) -> tuple[float, float, float]:
    """The preview's colour of a feature type, as linear-ish RGB floats (the palette's own values)."""
    if key == bake.TRAVEL_KEY:
        return palette.rgb(palette.OPTION_STYLE["Travel"][1])
    name = role_names.get(key, "")
    return palette.rgb(palette.ROLE_STYLE.get(name, (name, 0x808080))[1])


def material_name(key: int, role_names: Mapping[int, str]) -> str:
    return f"Slicewright {feature_label(key, role_names)}"


def feature_names(keys, role_names: Mapping[int, str]) -> dict[str, str]:
    """``{"1": "Inner wall", ...}``: the map stored on a baked object so a node tree can tell features by name."""
    return {str(k): feature_label(k, role_names) for k in keys}


def role_names_of(enums: Mapping[str, Mapping[str, int]]) -> dict[int, str]:
    """Role id to engine role name, from ``enums()``."""
    return {int(v): k for k, v in enums["role"].items()}
