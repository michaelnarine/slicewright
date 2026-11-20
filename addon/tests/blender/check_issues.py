# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the Issues list (03 section 6.5) against the fake engine.

Timers never fire in ``-b`` (03 section 7.9a), so the tests pump ``slicing.poll_job`` directly.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class Layout:
    """A recording stand-in for ``UILayout``: enough for the Issues panel and its list rows."""

    def __init__(self, log):
        self.log, self.alert = log, False

    def row(self, align=False):
        return self

    def box(self):
        return self

    def column(self, align=False):
        return self

    def label(self, text="", icon="NONE"):
        self.log.append(("label", text, icon))

    def operator(self, idname, text="", icon="NONE", emboss=True):
        self.log.append(("operator", idname, text, icon, emboss))
        return type("Op", (), {})()

    def template_list(self, *args, **kw):
        self.log.append(("template_list", args[0], args[4], args[2:4] and args[3], kw.get("rows")))


class IssuesTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright.blender import issues, registry, slicing
        self.addon, self.registry, self.slicing, self.issues = slicewright, registry, slicing, issues
        self.scene = bl_common.fresh_scene()
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        pe = self.sw.printer_edits
        pe.printable_area = "0x0,256x0,256x256,0x256"
        pe.printable_height = 250.0
        self.a = bl_common.add_cube("A", 20.0, (60.0, 60.0, 10.0))
        self.b = bl_common.add_cube("B", 20.0, (150.0, 150.0, 10.0))
        for ob in (self.a, self.b):
            ob.select_set(True)
        bpy.ops.slicewright.plate_add()

    def finish(self):
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    def pump(self, limit=500):
        for _ in range(limit):
            if self.slicing.poll_job() is None:
                return
        self.fail("the slice never finished")

    def rows(self):
        return [(i.level, i.code, i.object_name) for i in self.wm_items()]

    def wm_items(self):
        return getattr(bpy.context.window_manager, self.issues.WM_LIST)

    def move_off_bed(self, ob):
        ob.location.x = 5000.0
        bpy.context.view_layer.update()

    def select_only(self, ob):
        for o in bpy.context.view_layer.objects:
            o.select_set(o is ob)

    # -- the list is filled ---------------------------------------------------------------------------------

    def test_empty_at_the_start(self):
        self.assertEqual(self.registry.state.plate_issues, [])
        self.assertEqual(len(self.wm_items()), 0)

    def test_a_failed_slice_fills_the_list_with_the_validation_error(self):
        self.move_off_bed(self.b)
        bpy.ops.slicewright.slice()
        self.pump()
        self.assertEqual(self.sw.job_state, "failed")
        self.assertEqual(self.rows(), [("error", "object_outside_bed", "B")])
        self.assertEqual([i["code"] for i in self.registry.state.plate_issues], ["object_outside_bed"])
        self.assertEqual(self.issues.scene_name(), self.scene.name)

    def test_a_check_plate_fills_the_list_and_a_slice_replaces_it(self):
        self.move_off_bed(self.b)
        self.assertEqual(bpy.ops.slicewright.check_plate(), {"FINISHED"})
        self.assertEqual(self.rows(), [("error", "object_outside_bed", "B")])
        self.b.location.x = 150.0                                       # fixed: the slice starts from a clean list
        bpy.context.view_layer.update()
        bpy.ops.slicewright.slice()
        self.assertEqual(len(self.wm_items()), 0)                        # cleared at the start of the slice
        self.pump()
        self.assertEqual(self.sw.job_state, "done")
        self.assertEqual(len(self.wm_items()), 0)

    def test_the_check_and_the_slice_sources_merge_and_the_slice_wins_a_duplicate(self):
        self.issues.set_source(self.scene, "check", [
            {"level": "warning", "code": "mesh_open_edges", "message": "A has open edges", "opt_key": None,
             "object_name": "A"},
            {"level": "error", "code": "object_outside_bed", "message": "check text", "opt_key": None,
             "object_name": "B"}])
        self.issues.set_source(self.scene, "slice", [
            {"level": "error", "code": "object_outside_bed", "message": "engine text", "opt_key": None,
             "object_name": "B"}])
        self.assertEqual([i.message for i in self.wm_items()], ["engine text", "A has open edges"])
        self.assertEqual([i["message"] for i in self.registry.state.plate_issues], ["engine text", "A has open edges"])

    def test_engine_warnings_of_a_finished_slice_show_up(self):
        self.issues.set_source(self.scene, "slice", [
            {"level": "warning", "code": "gcode_conflict", "message": "toolpaths touch", "opt_key": None,
             "object_name": None}])
        self.assertEqual(self.rows(), [("warning", "gcode_conflict", "")])

    # -- when it is cleared ---------------------------------------------------------------------------------

    def test_a_cancelled_slice_replaces_the_list_with_what_it_knew(self):
        self.issues.set_source(self.scene, "check", [
            {"level": "warning", "code": "mesh_open_edges", "message": "stale", "opt_key": None,
             "object_name": "A"}])
        bpy.ops.slicewright.slice()
        self.assertEqual(len(self.wm_items()), 0)
        self.pump_n(3)
        bpy.ops.slicewright.slice_cancel()
        self.pump()
        self.assertEqual(self.sw.job_state, "cancelled")
        self.assertEqual(len(self.wm_items()), 0)                        # the old check result is gone

    def pump_n(self, n):
        for _ in range(n):
            self.slicing.poll_job()

    def test_a_file_load_clears_the_list(self):
        self.move_off_bed(self.b)
        bpy.ops.slicewright.check_plate()
        self.assertEqual(len(self.wm_items()), 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
        self.assertEqual(len(self.wm_items()), 0)
        self.assertEqual(self.registry.state.plate_issues, [])
        self.assertIsNone(self.issues.scene_name())

    def test_the_list_is_empty_for_another_scene(self):
        self.move_off_bed(self.b)
        bpy.ops.slicewright.check_plate()
        other = bpy.data.scenes.new("Other")
        self.assertEqual(self.issues.for_scene(self.scene)[0]["code"], "object_outside_bed")
        self.assertEqual(self.issues.for_scene(other), [])

    def test_the_mirror_is_not_saved_in_the_blend_file(self):
        self.move_off_bed(self.b)
        bpy.ops.slicewright.check_plate()
        hooks = self.registry.state.load_post_hooks
        hooks.remove(self.issues._on_file_load)                          # the hook empties it too: prove the file has none
        self.addCleanup(hooks.append, self.issues._on_file_load)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.assertEqual(len(self.wm_items()), 0)

    # -- clicking a row -------------------------------------------------------------------------------------

    def test_clicking_a_row_selects_the_right_object(self):
        self.issues.set_source(self.scene, "check", [
            {"level": "warning", "code": "mesh_open_edges", "message": "A open", "opt_key": None, "object_name": "A"},
            {"level": "warning", "code": "degenerate_object", "message": "B flat", "opt_key": None,
             "object_name": "B"}])
        self.select_only(self.a)
        self.assertEqual(bpy.ops.slicewright.issue_goto(index=1), {"FINISHED"})
        self.assertEqual([o.name for o in bpy.context.selected_objects], ["B"])
        self.assertEqual(bpy.context.view_layer.objects.active, self.b)
        self.assertEqual(bpy.context.window_manager.slicewright_issues_index, 1)
        bpy.ops.slicewright.issue_goto(index=0)
        self.assertEqual([o.name for o in bpy.context.selected_objects], ["A"])

    def test_an_instance_name_selects_its_source_object(self):
        self.issues.set_source(self.scene, "check", [
            {"level": "warning", "code": "large_mesh", "message": "big", "opt_key": None, "object_name": "B#2"}])
        bpy.ops.slicewright.issue_goto(index=0)
        self.assertEqual(bpy.context.view_layer.objects.active, self.b)

    def test_an_instance_of_a_collection_selects_the_instancer(self):
        src = bl_common.add_cube("Bolt", 5.0)
        coll = bpy.data.collections.new("Bolts")
        coll.objects.link(src)
        inst = bpy.data.objects.new("Inst", None)
        inst.instance_type, inst.instance_collection = "COLLECTION", coll
        self.scene.collection.objects.link(inst)
        bpy.data.collections["Print plate"].objects.link(inst)
        # Bolt is also in the scene here, so exact lookup wins; unlink it to model a source only in the collection
        self.scene.collection.objects.unlink(src)
        from slicewright.blender.operators import issues as ops
        self.assertEqual(ops.resolve_object(self.scene, "Bolt#1"), inst)
        self.assertIsNone(ops.resolve_object(self.scene, "Missing#1"))

    def test_a_row_for_an_object_outside_the_view_layer_warns_instead_of_failing(self):
        ghost = bpy.data.objects.new("Ghost", bpy.data.meshes.new("Ghost"))
        self.issues.set_source(self.scene, "check", [
            {"level": "warning", "code": "x", "message": "g", "opt_key": None, "object_name": "Ghost"}])
        self.assertIn(ghost.name, bpy.data.objects)
        self.assertEqual(bpy.ops.slicewright.issue_goto(index=0), {"FINISHED"})

    def test_a_bad_index_is_cancelled(self):
        self.assertEqual(bpy.ops.slicewright.issue_goto(index=0), {"CANCELLED"})
        self.assertEqual(bpy.ops.slicewright.issue_goto(index=-1), {"CANCELLED"})

    def test_a_setting_row_opens_the_settings_page_with_the_key_in_the_filter(self):
        self.sw.process_edits.layer_height = -1.0
        self.assertEqual(bpy.ops.slicewright.slice(), {"CANCELLED"})
        self.assertEqual([(i.code, i.opt_key) for i in self.wm_items()][0], ("validation", "layer_height"))
        self.sw.settings_role, self.sw.settings_page, self.sw.settings_filter = "printer", "Extruder", "zzz"
        self.assertEqual(bpy.ops.slicewright.issue_goto(index=0), {"FINISHED"})
        self.assertEqual((self.sw.settings_role, self.sw.settings_page, self.sw.settings_filter),
                         ("process", "Quality", "layer_height"))
        self.sw.process_edits.property_unset("layer_height")

    def test_a_printer_key_switches_the_role(self):
        self.issues.set_source(self.scene, "check", [
            {"level": "error", "code": "validation", "message": "bed", "opt_key": "printable_area",
             "object_name": None}])
        self.sw.settings_role = "process"
        bpy.ops.slicewright.issue_goto(index=0)
        self.assertEqual((self.sw.settings_role, self.sw.settings_filter), ("printer", "printable_area"))
        self.assertEqual(self.sw.settings_page, "Basic information")

    def test_an_expert_key_hidden_by_the_settings_level_still_opens_its_page_but_clears_the_filter(self):
        schema = self.registry.state.status.module.config_schema()
        key = next(k for k, e in schema.items() if e.get("mode") == "expert" and e.get("preset") == "process")
        self.issues.set_source(self.scene, "check", [
            {"level": "warning", "code": "validation", "message": "x", "opt_key": key, "object_name": None}])
        self.sw.settings_filter = "old"
        bpy.ops.slicewright.issue_goto(index=0)
        self.assertEqual((self.sw.settings_role, self.sw.settings_filter), ("process", ""))

    def test_an_unknown_key_does_not_fail(self):
        self.issues.set_source(self.scene, "check", [
            {"level": "warning", "code": "unknown_key", "message": "u", "opt_key": "no_such_key",
             "object_name": None}])
        self.sw.settings_role = "process"
        self.assertEqual(bpy.ops.slicewright.issue_goto(index=0), {"FINISHED"})
        self.assertEqual(self.sw.settings_role, "process")

    def test_a_row_with_an_object_and_a_key_does_both(self):
        self.issues.set_source(self.scene, "check", [
            {"level": "error", "code": "validation", "message": "x", "opt_key": "layer_height", "object_name": "B"}])
        self.select_only(self.a)
        bpy.ops.slicewright.issue_goto(index=0)
        self.assertEqual(bpy.context.view_layer.objects.active, self.b)
        self.assertEqual(self.sw.settings_filter, "layer_height")

    def test_the_description_says_what_a_click_does(self):
        self.issues.set_source(self.scene, "check", [
            {"level": "error", "code": "validation", "message": "msg", "opt_key": "layer_height",
             "object_name": "B"}])
        text = bpy.types.SLICEWRIGHT_OT_issue_goto.description(bpy.context, type("P", (), {"index": 0}))
        self.assertIn("msg", text)
        self.assertIn("select B", text)
        self.assertIn("layer_height", text)

    # -- drawing ---------------------------------------------------------------------------------------------

    def test_the_panel_shows_no_issues_then_the_list_and_counts_in_the_header(self):
        from slicewright.blender.ui import issues as ui
        log = []
        ui.draw_issues(Layout(log), bpy.context.window_manager, self.scene)
        self.assertEqual(log, [("label", "No issues", "NONE")])
        self.issues.set_source(self.scene, "check", [
            {"level": "error", "code": "a", "message": "e", "opt_key": None, "object_name": None},
            {"level": "warning", "code": "b", "message": "w1", "opt_key": None, "object_name": None},
            {"level": "warning", "code": "c", "message": "w2", "opt_key": None, "object_name": None}])
        log.clear()
        ui.draw_issues(Layout(log), bpy.context.window_manager, self.scene)
        self.assertEqual(log[0][:2], ("template_list", ui.SLICEWRIGHT_UL_issues.bl_idname))
        from slicewright.core import issues as core
        self.assertEqual(ui.header_counts(core.counts(self.issues.for_scene(self.scene))),
                         [("1", "CANCEL"), ("2", "ERROR")])

    def test_the_active_rows_detail_is_listed_under_the_list_and_in_the_tooltip(self):
        from slicewright.blender.ui import issues as ui
        keys = ", ".join(f"bambu_key_{n}" for n in range(12))
        self.issues.set_source(self.scene, "check", [
            {"level": "info", "code": "preset_keys_unknown", "message": "12 settings from the preset aren't known "
             "to this engine", "opt_key": None, "object_name": None, "detail": keys},
            {"level": "warning", "code": "b", "message": "w", "opt_key": None, "object_name": None}])
        wm = bpy.context.window_manager
        self.assertEqual(self.wm_items()[1].detail, keys)                    # the UIList mirror carries it
        self.assertEqual(self.wm_items()[0].detail, "")
        log = []
        setattr(wm, self.issues.WM_INDEX, 0)           # errors, warnings, then notes: the warning is row 0
        ui.draw_issues(Layout(log), wm, self.scene)
        self.assertEqual([e for e in log if e[0] == "label"], [])            # a row without detail adds nothing
        setattr(wm, self.issues.WM_INDEX, 1)
        log.clear()
        ui.draw_issues(Layout(log), wm, self.scene)
        text = " ".join(e[1] for e in log if e[0] == "label")
        self.assertEqual([e[0] for e in log][0], "template_list")
        self.assertIn("bambu_key_0", text)
        self.assertIn("bambu_key_11", text)
        tip = bpy.types.SLICEWRIGHT_OT_issue_goto.description(bpy.context, type("P", (), {"index": 1}))
        self.assertIn("12 settings", tip)
        self.assertIn("bambu_key_11", tip)
        self.assertEqual(bpy.ops.slicewright.issue_goto(index=1), {"FINISHED"})   # no target: it only reports

    def test_a_row_is_a_flat_button_with_the_severity_icon_and_a_select_helper_for_open_edges(self):
        from slicewright.blender.ui import issues as ui
        self.issues.set_source(self.scene, "check", [
            {"level": "warning", "code": "mesh_open_edges", "message": "A has open edges", "opt_key": None,
             "object_name": "A"}])
        item = self.wm_items()[0]
        log = []
        ui.SLICEWRIGHT_UL_issues.draw_item(None, bpy.context, Layout(log), None, item, 0, None, "", 0)
        self.assertEqual(log[0], ("operator", "slicewright.issue_goto", "A has open edges", "ERROR", False))
        self.assertEqual(log[1][:2], ("operator", "slicewright.select_non_manifold"))

    def test_every_icon_the_list_uses_exists_in_this_blender(self):
        from slicewright.core import issues as core
        valid = {i.identifier for i in bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items}
        for level in (core.ERROR, core.WARNING, core.INFO):
            self.assertIn(core.icon(level), valid)
        self.assertIn("RESTRICT_SELECT_OFF", valid)

    def test_the_plate_panel_no_longer_lists_issues(self):
        import inspect
        from slicewright.blender.ui import plate
        self.assertNotIn("plate_issues", inspect.getsource(plate))

    # -- hygiene ---------------------------------------------------------------------------------------------

    def test_unregister_removes_the_classes_properties_and_hooks(self):
        self.addon.unregister()
        for base, name in ((bpy.types.UIList, "SLICEWRIGHT_UL_issues"), (bpy.types.Panel, "SLICEWRIGHT_PT_issues"),
                           (bpy.types.Operator, "SLICEWRIGHT_OT_issue_goto"),
                           (bpy.types.PropertyGroup, "SLICEWRIGHT_PG_Issue")):
            self.assertIsNone(base.bl_rna_get_subclass_py(name), name)
        self.assertFalse(hasattr(bpy.types.WindowManager, "slicewright_issues"))
        self.assertFalse(hasattr(bpy.types.WindowManager, "slicewright_issues_index"))
        self.assertEqual(self.registry.state.plate_issues, [])
        self.assertNotIn(self.issues._on_file_load, self.registry.state.load_post_hooks)
        self.assertIsNone(self.issues.scene_name())


if __name__ == "__main__":
    bl_common.run("__main__")
