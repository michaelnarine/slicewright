# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: saving, diffing, reverting, managing and embedding user presets (03 section 3.8)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class UserPresetTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright.blender import config_pg, library, picker, timers, user_presets
        self.addon, self.cp, self.library, self.picker = slicewright, config_pg, library, picker
        self.timers, self.up = timers, user_presets
        self.dir = tempfile.mkdtemp(prefix="slicewright-presets-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        user_presets.set_presets_dir(self.dir)
        self.addCleanup(user_presets.set_presets_dir, None)
        self.before = bl_common.handler_snapshot()
        self.addCleanup(self.finish)
        self.addon.register()
        self.pg = bpy.context.scene.slicewright
        self.reset()
        library.request()
        timers.runner.run_until_idle()
        self.pg.printer_id = "sys:Acme/Acme Maker 1 0.4 nozzle"
        self.lib = library.get()

    def reset(self):
        self.pg.filaments.clear()
        for key in ("printer_id", "process_id", "pick_vendor", "pick_model", "pick_nozzle",
                    "settings_role", "settings_page", "settings_slot", "embedded_presets"):
            self.pg.property_unset(key)
        self.cp.clear(self.pg.printer_edits)
        self.cp.clear(self.pg.process_edits)

    def finish(self):
        self.reset()
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    def file(self, kind, name):
        with open(os.path.join(self.dir, kind, name + ".json"), encoding="utf-8") as fh:
            return json.load(fh)

    # -- save and diff ----------------------------------------------------------------------------

    def test_unedited_buffers_have_no_changes(self):
        for role in ("printer", "process", "filament"):
            self.assertEqual(self.up.changes(self.pg, role), [], role)

    def test_save_writes_only_the_diff_and_selects_the_new_preset(self):
        qe = self.pg.process_edits
        qe.layer_height = 0.16
        qe.sparse_infill_density = 25.0
        rows = self.up.changes(self.pg, "process")
        self.assertEqual([r[0] for r in rows], ["layer_height", "sparse_infill_density"])
        self.assertEqual(rows[1][2:], ("15%", "25%"))
        self.assertEqual(bpy.ops.slicewright.preset_save(role="process", name="My fine"), {"FINISHED"})
        data = self.file("process", "My fine")
        self.assertEqual({k: v for k, v in data.items() if k not in ("version",)},
                         {"type": "process", "from": "User", "inherits": "0.20mm Standard @Acme",
                          "name": "My fine", "layer_height": "0.16", "sparse_infill_density": "25%"})
        self.assertEqual(self.pg.process_id, "user:My fine")
        self.assertEqual(self.up.changes(self.pg, "process"), [])
        self.assertAlmostEqual(self.pg.process_edits.layer_height, 0.16, places=6)
        self.assertEqual(self.pg.process_edits.wall_loops, 2)                # from the system parent

    def test_vectors_and_other_roles_are_written_in_file_form(self):
        pe = self.pg.printer_edits
        pe.nozzle_diameter = "0.4,0.6"
        pe.printable_height = 123.0
        pe.layer_height = 0.9                                                  # a process key: ignored here
        self.assertEqual(bpy.ops.slicewright.preset_save(role="printer", name="Two nozzles"), {"FINISHED"})
        data = self.file("machine", "Two nozzles")
        self.assertEqual(data["nozzle_diameter"], ["0.4", "0.6"])
        self.assertEqual(data["printable_height"], "123")
        self.assertNotIn("layer_height", data)
        self.assertEqual(data["inherits"], "Acme Maker 1 0.4 nozzle")
        self.assertEqual(self.pg.printer_id, "user:Two nozzles")

    def test_saving_over_a_user_preset_keeps_the_system_parent(self):
        self.pg.process_edits.wall_loops = 4
        bpy.ops.slicewright.preset_save(role="process", name="Mine")
        self.pg.process_edits.wall_loops = 5
        self.assertEqual(bpy.ops.slicewright.preset_save(role="process", name="Mine"), {"FINISHED"})
        data = self.file("process", "Mine")
        self.assertEqual((data["inherits"], data["wall_loops"]), ("0.20mm Standard @Acme", "5"))

    def test_filament_overrides_become_a_user_filament(self):
        slot = self.pg.filaments[0]
        parent = slot.preset_id
        slot.overrides.nozzle_temperature = "255"
        self.assertEqual([r[0] for r in self.up.changes(self.pg, "filament")], ["nozzle_temperature"])
        self.pg.settings_role = "filament"
        self.assertEqual(bpy.ops.slicewright.preset_save(role="filament", name="Hot PETG"), {"FINISHED"})
        self.assertEqual(slot.preset_id, "user:Hot PETG")
        self.assertEqual(self.cp.overridden(slot.overrides), {})
        data = self.file("filament", "Hot PETG")
        self.assertEqual((data["inherits"], data["nozzle_temperature"]), (parent.partition("/")[2], ["255"]))

    def test_a_bad_name_is_reported_not_raised(self):
        with self.assertRaises(RuntimeError):                                  # operator reports an error
            bpy.ops.slicewright.preset_save(role="process", name="a/b")
        self.assertEqual(self.lib.store.list("process"), [])

    # -- revert ------------------------------------------------------------------------------------

    def test_revert_one_key_or_everything(self):
        qe = self.pg.process_edits
        qe.layer_height, qe.wall_loops = 0.3, 6
        bpy.ops.slicewright.preset_revert(role="process", key="wall_loops")
        self.assertEqual(qe.wall_loops, 2)
        self.assertAlmostEqual(qe.layer_height, 0.3, places=6)
        bpy.ops.slicewright.preset_revert(role="process")
        self.assertAlmostEqual(qe.layer_height, 0.2, places=6)
        self.assertEqual(self.up.changes(self.pg, "process"), [])

    def test_revert_filament_override_drops_the_override(self):
        slot = self.pg.filaments[0]
        slot.overrides.nozzle_temperature = "1"
        slot.overrides.filament_cost = "9"
        bpy.ops.slicewright.preset_revert(role="filament", key="filament_cost")
        self.assertEqual(self.cp.overridden(slot.overrides), {"nozzle_temperature": "1"})
        bpy.ops.slicewright.preset_revert(role="filament")
        self.assertEqual(self.cp.overridden(slot.overrides), {})

    # -- the library sees user presets -------------------------------------------------------------

    def test_user_presets_are_searchable_and_selectable(self):
        from slicewright.blender import picker
        self.pg.printer_edits.printable_height = 99.0
        bpy.ops.slicewright.preset_save(role="printer", name="Shelf printer")
        self.assertIn("user:Shelf printer", picker.search_printers("shelf"))
        self.pg.printer_id = "sys:Acme/Acme Maker 2 0.4 nozzle"
        self.assertEqual(self.pg.printer_edits.printable_height, 300.0)
        self.pg.printer_id = "user:Shelf printer"
        self.assertEqual(self.pg.printer_edits.printable_height, 99.0)
        self.assertEqual(self.pg.process_id, "sys:Acme/0.20mm Standard @Acme")   # a user printer keeps the rest

    def test_a_user_printer_is_compatible_through_its_inherits(self):
        bpy.ops.slicewright.preset_save(role="printer", name="Mine")
        subject = self.lib.subject("machine", "user:Mine")
        self.assertTrue(subject.is_user)
        self.assertEqual(subject.inherits, "Acme Maker 1 0.4 nozzle")
        names = {e.name for e in self.lib.compat.compatible_processes(subject)}
        self.assertIn("0.20mm Standard @Acme", names)                          # lists the parent, not "Mine"

    def test_duplicate_rename_and_delete(self):
        self.pg.process_edits.wall_loops = 3
        bpy.ops.slicewright.preset_save(role="process", name="One")
        self.assertEqual(bpy.ops.slicewright.preset_manage(role="process", action="DUPLICATE", name="Two"),
                         {"FINISHED"})
        self.assertEqual(self.pg.process_id, "user:Two")
        self.assertEqual(sorted(self.lib.store.list("process")), ["One", "Two"])
        self.assertEqual(bpy.ops.slicewright.preset_manage(role="process", action="RENAME", name="Three"),
                         {"FINISHED"})
        self.assertEqual(sorted(self.lib.store.list("process")), ["One", "Three"])
        self.assertEqual(self.pg.process_id, "user:Three")
        self.assertEqual(bpy.ops.slicewright.preset_manage(role="process", action="DELETE"), {"FINISHED"})
        self.assertEqual(self.lib.store.list("process"), ["One"])
        self.assertEqual(self.pg.process_id, "sys:Acme/0.20mm Standard @Acme")   # back to the parent
        with self.assertRaises(RuntimeError):                                  # a system preset is not deletable
            bpy.ops.slicewright.preset_manage(role="process", action="DELETE")

    # -- embedding ---------------------------------------------------------------------------------

    def test_embedded_copy_has_the_modified_settings_and_no_secrets(self):
        pe = self.pg.printer_edits
        pe.printable_height = 77.0
        pe["printhost_apikey"] = "SECRET-KEY"                                   # an orphan ID property
        pe["print_host"] = "192.168.1.9"
        self.pg.filaments[0].overrides.nozzle_temperature = "250"
        self.up.embed_all_scenes()
        text = self.pg.embedded_presets
        self.assertNotIn("SECRET-KEY", text)
        self.assertNotIn("192.168.1.9", text)
        data = json.loads(text)
        self.assertEqual(data["printer"]["config"]["printable_height"], "77")
        self.assertEqual(data["printer"]["id"], "sys:Acme/Acme Maker 1 0.4 nozzle")
        self.assertEqual(data["filaments"][0]["config"]["nozzle_temperature"], "250")
        self.assertEqual(len(data["filaments"]), len(self.pg.filaments))

    def test_save_pre_handler_embeds_when_the_file_is_saved(self):
        self.pg.printer_edits.printable_height = 88.0
        path = os.path.join(self.dir, "t.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        self.assertIn('"printable_height":"88"', self.pg.embedded_presets)
        bpy.ops.wm.open_mainfile(filepath=path)
        pg = bpy.context.scene.slicewright
        self.assertEqual(pg.printer_edits.printable_height, 88.0)
        self.assertEqual(json.loads(pg.embedded_presets)["printer"]["config"]["printable_height"], "88")
        self.pg = pg

    def test_a_missing_user_preset_falls_back_to_the_embedded_copy(self):
        self.pg.process_edits.wall_loops = 7
        bpy.ops.slicewright.preset_save(role="process", name="Gone soon")
        self.up.embed_all_scenes()
        os.remove(os.path.join(self.dir, "process", "Gone soon.json"))
        self.assertIsNone(self.lib.resolve("process", "user:Gone soon"))
        self.cp.clear(self.pg.process_edits)
        self.picker.load_edit_buffers(self.pg)
        self.assertEqual(self.pg.process_edits.wall_loops, 7)                  # from the embedded config
        self.assertEqual(bpy.ops.slicewright.preset_restore_embedded(role="process"), {"FINISHED"})
        self.assertEqual(self.lib.store.list("process"), ["Gone soon"])
        self.assertEqual(self.lib.resolve("process", "user:Gone soon").config["wall_loops"], "7")

    def test_a_missing_system_preset_resolves_through_renamed_from(self):
        entry = self.lib.index.get("process", "sys:Acme/0.20mm Strong @Acme")
        entry.renamed_from = ["0.20mm Sturdy @Acme"]
        self.assertEqual(self.lib.resolve("process", "sys:Acme/0.20mm Sturdy @Acme").name,
                         "0.20mm Strong @Acme")
        self.pg.process_id = "sys:Acme/0.20mm Sturdy @Acme"
        self.assertEqual(self.pg.process_id, "sys:Acme/0.20mm Strong @Acme")


if __name__ == "__main__":
    bl_common.run("__main__")
