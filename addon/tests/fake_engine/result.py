# SPDX-License-Identifier: GPL-3.0-or-later
"""SliceResult for the fake (04 section 5)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import weakref
import zlib
import zipfile

import numpy as np


def png_bytes(rgba: np.ndarray) -> bytes:
    """Encode an (H, W, 4) uint8 array as a PNG (stdlib only)."""
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def format_time(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m}m"
    if m:
        return f"{m}m{sec}s"
    return f"{sec}s"


def _freeze(arrays: dict) -> dict:
    for a in arrays.values():
        a.flags.writeable = False
    return arrays


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


class SliceResult:
    """Immutable result: read-only arrays plus the G-code file it was built from."""

    def __init__(self, *, gcode_path: str, moves: dict, layers: dict, gcode_line_ends,
                 stats: dict, warnings: list, objects: list, wipe_tower, config: dict,
                 thumbnails: list | None = None, owns_file: bool = True) -> None:
        self.gcode_path = gcode_path
        self.moves = _freeze(moves)
        self.layers = _freeze(layers)
        self.gcode_line_ends = _freeze({"e": gcode_line_ends})["e"]
        self.stats = stats
        self.warnings = warnings
        self.objects = objects
        self.wipe_tower = wipe_tower
        self._config = config
        self._thumbnails = list(thumbnails or [])
        if owns_file:
            weakref.finalize(self, _remove, gcode_path)

    def write_gcode(self, path: str) -> None:
        shutil.copyfile(self.gcode_path, path)

    def write_gcode_3mf(self, path: str, plate_meta: dict | None = None) -> None:
        name = (plate_meta or {}).get("plate_name", "Plate 1")
        with open(self.gcode_path, "rb") as f:
            gcode = f.read()
        slice_info = (f'<?xml version="1.0"?>\n<config><plate><metadata key="index" value="1"/>'
                      f'<metadata key="prediction" value="{int(self.stats["time_s"]["normal"])}"/>'
                      f'</plate></config>\n')
        model_settings = (f'<?xml version="1.0"?>\n<config><plate><metadata key="plater_id" '
                          f'value="1"/><metadata key="plater_name" value="{name}"/></plate></config>\n')
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
            z.writestr("Metadata/plate_1.gcode", gcode)
            z.writestr("Metadata/plate_1.gcode.md5", hashlib.md5(gcode).hexdigest())
            z.writestr("Metadata/slice_info.config", slice_info)
            z.writestr("Metadata/model_settings.config", model_settings)
            z.writestr("Metadata/project_settings.config", json.dumps(self._config, indent=1))
            if self._thumbnails:
                biggest = max(self._thumbnails, key=lambda a: a.shape[0] * a.shape[1])
                z.writestr("Metadata/plate_1.png", png_bytes(biggest))

    def output_filename(self, input_basename: str) -> str:
        base = os.path.splitext(os.path.basename(input_basename))[0]
        types = [t.strip('"') for t in self._config["filament_type"].split(";")]
        values = {
            "input_filename_base": base,
            "layer_height": self._config["layer_height"],
            "filament_type[0]": types[0],
            "print_time": format_time(self.stats["time_s"]["normal"]),
        }
        out = self._config["filename_format"]
        for key, value in values.items():
            out = out.replace("{" + key + "}", value)
        return out
