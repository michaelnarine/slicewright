# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI Blender: every preview shader compiles and the template batches build on this backend."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402


def main():
    import gpu
    import numpy as np
    from fake_engine import api
    from slicewright.blender.preview import shaders, templates
    from slicewright.core import preview_palette

    enums = api.enums()
    for name, fn in (("path_tubes", lambda: shaders.create_path_shader(enums["move_type"], False)),
                     ("path_lines", lambda: shaders.create_path_shader(enums["move_type"], True)),
                     ("markers", lambda: shaders.create_marker_shader(enums["move_type"]))):
        try:
            sh = fn()
            g.check(f"shader_{name}", sh is not None)
        except Exception as exc:  # noqa: BLE001
            g.check(f"shader_{name}", False, error=str(exc)[:2000])
    for kind in ("strip", "lines"):
        try:
            batch, vbo = templates.make_template(kind)
            g.check(f"template_{kind}", batch is not None)
        except Exception as exc:  # noqa: BLE001
            g.check(f"template_{kind}", False, error=str(exc))
    pal = preview_palette.palette_array(enums["role"], enums["move_type"], 0.0, 100.0)
    ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', pal.size, pal.reshape(-1)))
    g.check("palette_ubo", ubo is not None and pal.shape == (preview_palette.N_ROWS, 4))
    g.log("backend", gpu.platform.backend_type_get(), np.__version__)


g.run(main)
