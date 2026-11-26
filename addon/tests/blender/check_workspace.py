# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the Slicer workspace operators (03 section 1.3) against the fake engine.

Background Blender has no window to lay out, so these checks cover the workspace datablock (created, tagged, not
duplicated, never confusing a user's own workspace with ours, menus come and go with the add-on, no file written).
The layout itself and the removal are verified in a real window by the screenshot tool (``slicer_workspace``).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class WorkspaceBase(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright.blender import workspace
        from slicewright.core import workspace_plan
        self.addon, self.ws, self.plan = slicewright, workspace, workspace_plan
        bl_common.fresh_scene()
        # fresh_scene(use_empty) keeps the UI workspaces of the factory file
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)

    def finish(self):
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    def names(self):
        return [w.name for w in bpy.data.workspaces]

    def add(self):
        return bpy.ops.slicewright.workspace_add()


class TestWorkspace(WorkspaceBase):
    def test_add_creates_one_tagged_workspace_and_leaves_the_others(self):
        before = set(self.names())
        self.assertEqual(self.add(), {"FINISHED"})
        self.assertEqual(set(self.names()) - before, {"Slicer"})
        ws = bpy.data.workspaces["Slicer"]
        self.assertTrue(self.ws.is_ours(ws))
        self.assertEqual(self.ws.tagged_names(), ["Slicer"])
        self.assertFalse(any(self.ws.is_ours(w) for w in bpy.data.workspaces if w.name != "Slicer"))
        self.assertEqual(before - set(self.names()), set())

    def test_add_twice_does_not_make_a_second_workspace(self):
        self.add()
        count = len(bpy.data.workspaces)
        self.assertEqual(self.add(), {"FINISHED"})
        self.assertEqual(len(bpy.data.workspaces), count)
        self.assertEqual(self.names().count("Slicer"), 1)
        self.assertFalse(any(n.startswith("Slicer.") for n in self.names()))

    def test_a_workspace_named_slicer_of_the_users_is_never_touched(self):
        bpy.ops.workspace.duplicate()
        mine = next(w for w in bpy.data.workspaces if w.name == "Layout.001")
        mine.name = "Slicer"
        mine["note"] = "mine"
        count = len(bpy.data.workspaces)
        self.assertEqual(self.add(), {"CANCELLED"})
        self.assertEqual(len(bpy.data.workspaces), count)
        self.assertFalse(self.ws.is_ours(mine))
        self.assertEqual(mine["note"], "mine")
        self.assertFalse(bpy.ops.slicewright.workspace_remove.poll())          # nothing of ours to remove
        self.assertEqual(self.ws.remove(bpy.context), 0)
        self.assertIn("Slicer", self.names())

    def test_remove_targets_only_the_tagged_workspace(self):
        self.add()
        self.assertTrue(bpy.ops.slicewright.workspace_remove.poll())
        self.assertEqual(self.ws.remove(bpy.context), 1)
        others = [w for w in bpy.data.workspaces if w.name != "Slicer"]
        self.assertTrue(others and not any(self.ws.is_ours(w) for w in others))

    def test_the_menus_and_panels_come_and_go_with_the_add_on(self):
        for name in self.ws.MENUS:
            self.assertIn(self.ws.draw_menu, getattr(bpy.types, name).draw._draw_funcs)
        self.assertTrue(hasattr(bpy.types, "SLICEWRIGHT_PT_settings_wide"))
        self.add()
        self.addon.unregister()                                   # the workspace outlives the add-on, without errors
        for name in self.ws.MENUS:
            self.assertNotIn(self.ws.draw_menu, getattr(bpy.types, name).draw._draw_funcs)
        self.assertFalse(hasattr(bpy.types, "SLICEWRIGHT_PT_settings_wide"))
        self.assertIn("Slicer", self.names())
        self.addon.register()                                     # and is recognised again after enabling
        self.assertEqual(self.ws.tagged_names(), ["Slicer"])

    def test_the_wide_settings_panel_sits_in_the_world_tab_and_is_off_in_other_workspaces(self):
        cls = bpy.types.SLICEWRIGHT_PT_settings_wide
        self.assertEqual((cls.bl_space_type, cls.bl_region_type, cls.bl_context), ("PROPERTIES", "WINDOW", "world"))
        layout = bpy.context.window.workspace
        self.assertFalse(self.ws.is_ours(layout))

    def test_nothing_is_written_to_the_user_profile_or_preferences(self):
        prefs_before = bpy.context.preferences.is_dirty
        self.add()
        self.assertEqual(bpy.context.preferences.is_dirty, prefs_before)

    def test_a_saved_file_with_the_workspace_opens_without_the_add_on(self):
        self.add()
        path = os.path.join(tempfile.mkdtemp(prefix="sw-ws-"), "ws.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        self.addon.unregister()
        bpy.ops.wm.open_mainfile(filepath=path)
        self.assertIn("Slicer", self.names())
        self.assertEqual(self.ws.tagged_names(), ["Slicer"])            # the tag survives the save
        self.addon.register()
        self.assertEqual(self.add(), {"FINISHED"})                      # re-adding finds ours, no second copy
        self.assertEqual(self.names().count("Slicer"), 1)


if __name__ == "__main__":
    bl_common.run(__name__)
