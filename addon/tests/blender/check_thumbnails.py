# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: thumbnails for the G-code header (03 section 6.2) in background mode, against the fake engine.

Background mode has no GPU, so the render is skipped with a clear status and the request carries no thumbnails; the
real render is covered by the GUI test ``gui_thumbnails.py``.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import fake_engine
        import slicewright
        from slicewright import prefs
        from slicewright.blender import registry, slicing, thumbnails
        self.addon, self.registry, self.slicing, self.thumbnails = slicewright, registry, slicing, thumbnails
        self.sc = fake_engine

        class Prefs(prefs._Defaults):
            cache_cap_mb = 0                                   # every slice reaches the engine
            recovery_copy = False
        real_get = prefs.get
        prefs.get = lambda: Prefs
        self.addCleanup(setattr, prefs, "get", real_get)
        self.passed = []
        real_set = fake_engine.SliceJob.set_thumbnails

        def spy(job, images):
            self.passed.append(images)
            return real_set(job, images)
        fake_engine.SliceJob.set_thumbnails = spy
        self.addCleanup(setattr, fake_engine.SliceJob, "set_thumbnails", real_set)
        self.renders = []
        real_render = self.real_render = thumbnails.render_plate

        def counting(meshes, sizes):
            self.renders.append(list(sizes))
            return real_render(meshes, sizes)
        thumbnails.render_plate = counting
        self.addCleanup(setattr, thumbnails, "render_plate", real_render)
        bl_common.fresh_scene()
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        self.sw.printer_edits.printable_area = "0x0,256x0,256x256,0x256"
        self.sw.printer_edits.printable_height = 250.0
        self.cube = bl_common.add_cube("Cube", 20.0, (100.0, 100.0, 10.0))
        self.cube.select_set(True)
        bpy.ops.slicewright.plate_add()

    def finish(self):
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    def slice(self):
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        for _ in range(500):
            if self.slicing.poll_job() is None:
                break
        self.assertEqual(self.sw.job_state, "done")

    def codes(self):
        return [i["code"] for i in self.registry.state.plate_issues]

    # -- background mode ---------------------------------------------------------------------------------

    def test_background_mode_skips_with_a_clear_status_and_passes_no_thumbnails(self):
        self.assertTrue(bpy.app.background)
        self.sw.printer_edits.thumbnails = "48x48/PNG,300x300/PNG"
        self.slice()
        self.assertEqual(self.passed, [])                                  # the request carried none
        self.assertEqual(self.renders, [[(48, 48), (300, 300)]])           # the source was asked, and declined
        note = next(i for i in self.registry.state.plate_issues if i["code"] == "thumbnails_skipped")
        self.assertEqual(note["level"], "info")
        self.assertEqual(note["message"], self.thumbnails.BACKGROUND_REASON)
        self.assertIn("background mode", note["message"])
        self.assertEqual(self.codes().count("thumbnail_missing"), 2)       # and the engine reports what is missing
        self.assertIsNotNone(self.slicing.runner().status.result)
        rows = bpy.context.window_manager.slicewright_issues
        self.assertIn("thumbnails_skipped", [r.code for r in rows])        # it shows in the Issues list

    def test_no_thumbnails_requested_renders_and_passes_nothing(self):
        self.sw.printer_edits.thumbnails = ""
        self.slice()
        self.assertEqual((self.passed, self.renders), ([], []))
        self.assertNotIn("thumbnails_skipped", self.codes())
        self.assertNotIn("thumbnail_missing", self.codes())

    def test_render_plate_refuses_in_background_mode(self):
        from slicewright.core import thumbnails as core
        real = self.real_render                                               # the function the spy wraps
        with self.assertRaises(core.Skipped) as ctx:
            real([], [(8, 8)])
        self.assertEqual(str(ctx.exception), self.thumbnails.BACKGROUND_REASON)

    # -- what is drawn --------------------------------------------------------------------------------------

    def test_plate_meshes_use_the_filament_colour_of_each_object(self):
        from slicewright.blender import slice_inputs
        self.sw.filaments.add().color = (1.0, 0.0, 0.0)
        self.sw.filaments.add().color = (0.0, 0.0, 1.0)
        other = bl_common.add_cube("Other", 10.0, (150.0, 150.0, 5.0))
        other.slicewright.filament = 2
        other.select_set(True)
        bpy.ops.slicewright.plate_add()
        request = slice_inputs.build_request(bpy.context)
        meshes = {o.owner: m for o, m in zip(request.objects, self.thumbnails.plate_meshes(request))}
        self.assertEqual(sorted(meshes), ["Cube", "Other"])
        self.assertEqual(tuple(round(c) for c in meshes["Cube"][2]), (1, 0, 0))     # inherits slot 1
        self.assertEqual(tuple(round(c) for c in meshes["Other"][2]), (0, 0, 1))    # slot 2
        verts, tris, _ = meshes["Cube"]
        self.assertEqual((verts.shape[1], tris.shape[1]), (3, 3))

    def test_a_filament_out_of_range_falls_back_to_the_default_colour(self):
        from slicewright.blender import slice_inputs
        from slicewright.core import thumbnails as core
        self.cube.slicewright.filament = 5
        request = slice_inputs.build_request(bpy.context)
        (mesh,) = self.thumbnails.plate_meshes(request)
        self.assertEqual(mesh[2], core.DEFAULT_COLOUR)


if __name__ == "__main__":
    bl_common.run("__main__")
