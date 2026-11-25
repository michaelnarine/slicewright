# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the Preview option "Model" (Hidden / Ghost / Shown) and its restoration paths, against the fake engine.

Hidden and Ghost switch off the 3D views' object-type visibility filters and record what they switched; no object,
collection or view-layer property is written. The tests check the filters come back on leaving Preview, on a save, on
unregister and after a file load, that a filter the user had turned off is never turned on, and that objects deleted or
added while hiding change nothing.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402

from check_mode import ModeBase  # noqa: E402


def view_spaces():
    """The 3D views of the window's own screen (other workspaces' screens are not shown, so they are left alone)."""
    screen = bpy.context.window_manager.windows[0].screen
    return [sp for area in screen.areas if area.type == "VIEW_3D" for sp in area.spaces if sp.type == "VIEW_3D"]


def filters_on():
    return [sp.show_object_viewport_mesh for sp in view_spaces()]


def object_state():
    return [(o.name, o.hide_get(), o.hide_viewport, o.hide_render, o.hide_select, o.display_type, o.select_get())
            for o in bpy.data.objects]


class ModelVisibilityTests(ModeBase):
    def setUp(self):
        super().setUp()
        from slicewright.blender import model_visibility
        self.mv = model_visibility
        self.assertTrue(view_spaces())
        self.objects_before = object_state()

    def test_the_default_is_hidden_and_the_choices_are_hidden_ghost_shown(self):
        self.assertEqual(self.sw.preview.model_display, "HIDDEN")
        items = [i.identifier for i in self.sw.preview.bl_rna.properties["model_display"].enum_items]
        self.assertEqual(items, ["HIDDEN", "GHOST", "SHOWN"])

    def test_preview_hides_the_model_and_prepare_brings_it_back_without_touching_objects(self):
        self.assertTrue(all(filters_on()))
        self.slice()
        self.assertEqual(self.sw.mode, "PREVIEW")
        self.assertFalse(any(filters_on()))
        self.assertEqual(object_state(), self.objects_before)                     # no object data was written
        bpy.ops.slicewright.mode_prepare()
        self.assertTrue(all(filters_on()))
        self.assertEqual(self.sw.preview.model_record, "")
        bpy.ops.slicewright.mode_preview()
        self.assertFalse(any(filters_on()))

    def test_ghost_hides_the_viewport_copy_and_draws_its_own_triangles(self):
        from slicewright.blender.preview import ghost
        self.slice()
        self.sw.preview.model_display = "GHOST"
        self.assertFalse(any(filters_on()))
        self.assertTrue(self.mv.ghost_wanted(self.scene))
        tris = ghost.world_triangles(bpy.context.evaluated_depsgraph_get(), self.scene)
        self.assertEqual(tris.shape, (36, 3))                                    # 12 triangles of the cube
        self.assertAlmostEqual(float(tris[:, 0].min()), 90.0, places=3)           # in world coordinates
        self.assertAlmostEqual(float(tris[:, 0].max()), 110.0, places=3)

    def test_shown_keeps_the_filters_and_switching_back_hides_again(self):
        self.slice()
        self.sw.preview.model_display = "SHOWN"
        self.assertTrue(all(filters_on()))
        self.sw.preview.model_display = "HIDDEN"
        self.assertFalse(any(filters_on()))
        self.sw.preview.model_display = "SHOWN"
        self.assertTrue(all(filters_on()))

    def test_a_filter_the_user_turned_off_is_never_turned_on(self):
        sp = view_spaces()[0]
        sp.show_object_viewport_mesh = False                                      # the user's own choice
        self.slice()
        self.assertEqual(self.mv._record, set())
        bpy.ops.slicewright.mode_prepare()
        self.assertFalse(sp.show_object_viewport_mesh)
        self.assertEqual(self.sw.preview.model_record, "")

    def test_clearing_the_preview_brings_the_model_back(self):
        from slicewright.blender.preview import handler
        self.slice()
        self.assertFalse(any(filters_on()))
        handler.clear(self.scene)
        self.assertTrue(all(filters_on()))

    def test_save_restores_before_writing_and_hides_again_after(self):
        self.slice()
        self.assertFalse(any(filters_on()))
        path = os.path.join(self.tmp, "saved.blend")
        from slicewright.blender import handlers
        handlers._save_pre()
        self.assertTrue(all(filters_on()))                                        # restored before the write
        self.assertEqual(self.sw.preview.model_record, "")                        # and no record goes into the file
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, check_existing=False)
        handlers._save_post()
        self.assertFalse(any(filters_on()))                                       # hidden again afterwards

    def test_the_real_save_handlers_run_on_a_save(self):
        self.slice()
        path = os.path.join(self.tmp, "real.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)                                # fires save_pre and save_post
        self.assertFalse(any(filters_on()))                                       # post hid them again
        bpy.ops.slicewright.mode_prepare()
        self.assertTrue(all(filters_on()))

    def test_unregister_restores_the_filters(self):
        self.slice()
        self.assertFalse(any(filters_on()))
        self.addon.unregister()
        self.assertTrue(all(filters_on()))
        self.addon.register()                                                     # the cleanup unregisters again

    def test_a_stale_record_in_a_loaded_file_is_put_back(self):
        """A crash leaves the filters off and the record in the file: loading it restores the filters."""
        self.slice()
        self.assertFalse(any(filters_on()))
        bpy.ops.slicewright.mode_prepare()                                        # the file is saved in Prepare ...
        self.sw.mode = "PREPARE"
        for sp in view_spaces():                                                  # ... with the filters left off
            sp.show_object_viewport_mesh = False
        screen = bpy.context.window_manager.windows[0].screen.name
        self.mv._record.update(self.mv.pm.space_key(s, a, i, "show_object_viewport_mesh")
                               for s, a, i, _sp in self.mv._spaces() if s == screen)
        self.mv._store_record(set(self.mv._record))
        path = os.path.join(self.tmp, "crash.blend")
        from slicewright.blender import handlers
        # write without our save handlers, as a crash recovery file is written
        handlers.unregister()
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        handlers.register()
        self.addon.unregister()
        bpy.ops.wm.open_mainfile(filepath=path)
        self.assertFalse(any(filters_on()))                                       # the stale state is in the file
        self.addon.register()
        self.registry.on_load_post()                                              # what Blender's load_post does
        self.assertTrue(all(filters_on()))

    def test_deleting_an_object_while_hiding_changes_nothing_and_leaves_no_residue(self):
        self.slice()
        bpy.data.objects.remove(self.cube, do_unlink=True)
        bpy.context.view_layer.update()
        self.mv.sync()
        self.assertEqual(self.mv.desired(self.scene), (True, ()))                 # no plate types left: nothing to hide
        self.assertTrue(all(filters_on()))                                        # and what we hid is back
        self.assertEqual(self.sw.preview.model_record, "")

    def test_a_new_type_added_while_hiding_is_hidden_too_and_comes_back(self):
        self.slice()
        bpy.ops.object.text_add()
        text = bpy.context.object
        plate = self.sw.plate_collection
        plate.objects.link(text)
        bpy.context.scene.collection.objects.unlink(text)
        self.mv.sync()
        self.assertTrue(all(not sp.show_object_viewport_font for sp in view_spaces()))
        bpy.ops.slicewright.mode_prepare()
        self.assertTrue(all(sp.show_object_viewport_font for sp in view_spaces()))
        self.assertTrue(all(filters_on()))

    def test_a_new_mesh_added_while_hiding_is_hidden_by_the_type_filter(self):
        self.slice()
        extra = bl_common.add_cube("Extra", 10.0, (0.0, 0.0, 5.0), self.sw.plate_collection)
        self.mv.sync()
        self.assertFalse(any(filters_on()))
        self.assertEqual(extra.hide_get(), False)                                 # the object itself is untouched
        bpy.ops.slicewright.mode_prepare()
        self.assertTrue(all(filters_on()))

    def test_undo_cannot_strand_the_filters(self):
        self.slice()
        self.sw.preview.model_record = ""                                         # what an undo of the scene string does
        bpy.ops.slicewright.mode_prepare()
        self.assertTrue(all(filters_on()))                                        # the runtime copy still knows

    def test_a_second_view_opened_in_preview_is_hidden_on_the_next_sync(self):
        self.slice()
        view_spaces()[0].show_object_viewport_mesh = True
        self.mv.sync()
        self.assertFalse(any(filters_on()))


if __name__ == "__main__":
    bl_common.run(__name__)
