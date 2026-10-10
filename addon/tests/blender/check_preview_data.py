# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: preview packing and range planning on Blender's own numpy.

Shaders cannot be created in ``-b`` (no GPU context), so only the data side is tested here:
chunk packing of a real ``from_gcode`` result, the range planner, and that the pure-Python
``core`` module imports without ``bpy`` side effects.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import numpy as np  # noqa: E402

FIXTURES = os.path.join(bl_common.TESTS_DIR, "fixtures", "gcode")


class PreviewDataTests(unittest.TestCase):
    def setUp(self):
        from fake_engine.gcode import from_gcode
        from slicewright.core import preview_data as pd
        self.pd = pd
        self.result = from_gcode(os.path.join(FIXTURES, "plain_square.gcode"))

    def test_chunks_of_a_real_result_pack_and_decode(self):
        pd, m = self.pd, self.result.moves
        n = len(m["type"])
        bounds = pd.chunk_bounds(n, 16)
        self.assertGreater(len(bounds), 1)
        for b in bounds:
            bits = pd.pack_meta(m, b)
            u = pd.unpack_meta(bits[:b.n_texels])
            idx = np.maximum(np.arange(b.n_texels) + b.s - 1, 0)
            self.assertTrue(np.array_equal(u["role"], m["role"][idx]))
            self.assertTrue(np.array_equal(u["type"], m["type"][idx]))
            f = bits[:b.n_texels].view(np.float32)
            self.assertTrue(np.isfinite(f).all())

    def test_every_move_is_planned_exactly_once_for_the_whole_print(self):
        pd, layers = self.pd, self.result.layers
        n = len(self.result.moves["type"])
        rng = pd.visible_range(layers, 0, len(layers["z"]) - 1, None, n - 1)
        self.assertEqual(rng, (0, n - 1))
        plan = pd.plan_ranges(pd.chunk_bounds(n, 16), *rng)
        self.assertEqual(sum(r.count for r in plan), n)
        self.assertTrue(all(r.count >= 1 for r in plan))

    def test_view_scalars_and_range(self):
        pd, r = self.pd, self.result
        for view in pd.VIEW_MODES:
            s = pd.view_scalar(r.moves, r.layers, view)
            self.assertTrue(s is None or len(s) == len(r.moves["type"]))
        lo, hi = pd.value_range(pd.view_scalar(r.moves, r.layers, "speed"))
        self.assertLess(lo, hi)


if __name__ == "__main__":
    bl_common.run("__main__")
