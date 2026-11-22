# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the library loader and the vendor / model / nozzle / process / filament picker.

Timers never fire in ``-b``, so the tick runner is driven directly, exactly as the timer would.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


def names(ids):
    return [i.partition("/")[2] for i in ids]


class PickerTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright.blender import library, timers
        self.addon, self.library, self.timers = slicewright, library, timers
        self.before = bl_common.handler_snapshot()
        self.addCleanup(self.finish)
        self.addon.register()
        self.pg = bpy.context.scene.slicewright
        self.reset_scene_props()

    def finish(self):
        self.reset_scene_props()
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)
        self.assertFalse(bpy.app.timers.is_registered(self.timers._tick))
        self.assertIsNone(self.library.get())
        self.assertEqual(self.library.status()[0], "idle")

    def reset_scene_props(self):
        """Scene data outlives register/unregister cycles, so each test starts from a clean slate."""
        self.pg.filaments.clear()
        for key in ("printer_id", "process_id", "pick_vendor", "pick_model", "pick_nozzle"):
            self.pg.property_unset(key)

    def load(self):
        self.library.request()
        self.timers.runner.run_until_idle()
        self.assertEqual(self.library.status()[0], "ready", self.library.status())

    # -- loading ------------------------------------------------------------------------------

    def test_nothing_is_offered_before_the_library_is_loaded(self):
        from slicewright.blender import picker
        self.assertIsNone(self.library.get())
        self.assertEqual((picker.search_printers(""), picker.search_vendors("")), ([], []))
        self.assertEqual(self.library.status()[0], "idle")

    def test_request_builds_the_index_on_the_tick_runner_once(self):
        self.library.request()
        self.assertEqual(self.library.status()[0], "loading")
        self.assertTrue(bpy.app.timers.is_registered(self.timers._tick))
        self.library.request()                                  # a second request is a no-op
        self.assertEqual(len(self.timers.runner.pending()), 1)
        ticks = 0
        while self.timers._tick() is not None:
            ticks += 1
        self.assertEqual(self.library.status()[0], "ready")
        lib = self.library.get()
        self.assertEqual(len(lib.printers()), 4)
        self.assertGreater(len(lib.index), 20)

    def test_unregister_while_loading_cancels_and_leaves_no_library(self):
        self.library.request()
        self.addon.unregister()
        self.assertIsNone(self.library.get())
        self.assertTrue(self.timers.runner.idle)
        self.addon.register()
        self.pg = bpy.context.scene.slicewright

    # -- picking ------------------------------------------------------------------------------

    def test_vendor_then_model_then_nozzle_selects_a_printer_with_defaults(self):
        from slicewright.blender import picker
        self.load()
        pg = self.pg
        self.assertEqual(sorted(self.library.get().index.vendors), ["Acme", "Bolt3D", "OrcaFilamentLibrary"])
        pg.pick_vendor = "Acme"
        self.assertEqual((pg.pick_model, pg.pick_nozzle), ("Acme Maker 1", "0.4"))
        self.assertEqual(pg.printer_id, "sys:Acme/Acme Maker 1 0.4 nozzle")
        self.assertEqual(pg.process_id, "sys:Acme/0.20mm Standard @Acme")
        # the library "Generic PLA" is hidden by the printer-specific alias, so it is not a default
        # and a printer brings only its first default material as a slot; the rest are one click away
        self.assertEqual(names(s.preset_id for s in pg.filaments), ["Generic PETG"])
        self.assertEqual(names(e.id for e in picker.missing_default_materials(pg)), ["Acme PLA Pro"])
        pg.pick_nozzle = "0.6"
        self.assertEqual(pg.printer_id, "sys:Acme/Acme Maker 1 0.6 nozzle")
        self.assertEqual(pg.process_id, "sys:Acme/0.30mm Draft @Acme")
        # Acme PLA Pro needs layer_height < 0.3 (compatible_prints_condition) and the 0.6 default is 0.3
        self.assertEqual(names(s.preset_id for s in pg.filaments), ["Generic PLA"])
        self.assertEqual(names(e.id for e in picker.missing_default_materials(pg)), ["Generic PETG"])
        pg.pick_model = "Acme Maker 2"
        self.assertEqual((pg.pick_nozzle, pg.printer_id), ("0.4", "sys:Acme/Acme Maker 2 0.4 nozzle"))

    def test_setting_the_printer_id_updates_the_pickers(self):
        self.load()
        pg = self.pg
        pg.printer_id = "sys:Bolt3D/Bolt One 0.4 nozzle"
        self.assertEqual((pg.pick_vendor, pg.pick_model, pg.pick_nozzle), ("Bolt3D", "Bolt One", "0.4"))
        self.assertEqual(pg.process_id, "sys:Bolt3D/0.20mm Bolt")
        self.assertEqual(len(pg.filaments), 1)

    def test_an_unknown_printer_id_changes_nothing_else(self):
        self.load()
        pg = self.pg
        pg.printer_id = "user:not-there"
        self.assertEqual((pg.pick_vendor, pg.process_id, len(pg.filaments)), ("", "", 0))

    def test_a_slot_takes_the_filament_colour(self):
        self.load()
        pg = self.pg
        pg.printer_id = "sys:Acme/Acme Maker 1 0.4 nozzle"
        self.assertEqual(bpy.ops.slicewright.filament_add_defaults(), {"FINISHED"})
        slot = pg.filaments[1]
        self.assertEqual(slot.preset_id, "sys:Acme/Acme PLA Pro")
        self.assertEqual([round(c * 255) for c in slot.color], [0xC0, 0x39, 0x2B])
        slot.preset_id = "sys:Bolt3D/Bolt PLA"
        self.assertEqual([round(c * 255) for c in slot.color], [0xDD, 0xDD, 0xDD])

    def test_search_callbacks_filter_by_compatibility(self):
        from slicewright.blender import picker
        self.load()
        pg = self.pg
        self.assertEqual(names(picker.search_printers("0.6")), ["Acme Maker 1 0.6 nozzle"])
        self.assertEqual(picker.search_vendors("bo"), ["Bolt3D"])
        pg.printer_id = "sys:Acme/Acme Maker 1 0.6 nozzle"
        self.assertEqual(sorted(names(picker.search_processes(pg, ""))),
                         ["0.20mm Strong @Acme", "0.30mm Draft @Acme"])
        self.assertEqual(picker.search_models(pg, "2"), ["Acme Maker 2"])
        self.assertNotIn("sys:Acme/Generic PLA @Acme", picker.search_filaments(pg, ""))
        self.assertEqual(pg.process_id, "sys:Acme/0.30mm Draft @Acme")
        self.assertNotIn("sys:Acme/Acme PLA Pro", picker.search_filaments(pg, ""))   # layer_height < 0.3
        pg.process_id = "sys:Acme/0.20mm Strong @Acme"
        self.assertIn("sys:Acme/Acme PLA Pro", picker.search_filaments(pg, "pro"))

    def test_nozzle_items_follow_the_model(self):
        from slicewright.blender import picker
        self.load()
        pg = self.pg
        pg.pick_vendor = "Acme"
        self.assertEqual([i[0] for i in picker.nozzle_items(pg)], ["0.4", "0.6"])
        pg.pick_model = "Acme Maker 2"
        self.assertEqual([i[0] for i in picker.nozzle_items(pg)], ["0.4"])

    def test_the_pickers_show_readable_names_and_keep_the_ids(self):
        from slicewright.blender import picker
        from slicewright.blender.ui import picker as ui
        self.load()
        pg = self.pg
        pg.printer_id = "sys:Bolt3D/Bolt One 0.4 nozzle"
        process_id, filament_id = pg.process_id, pg.filaments[0].preset_id
        self.assertTrue(process_id.startswith("sys:Bolt3D/") and filament_id.startswith("sys:"), (process_id, filament_id))
        calls = []

        class Layout:
            def label(self, text="", icon="NONE"):
                calls.append(("label", text))

            def operator(self, idname, text="", icon="NONE"):
                calls.append(("operator", idname, text))
                return type("Op", (), {})()

        ui.draw_preset_button(Layout(), "process", -1, process_id, "PRESET")
        ui.draw_preset_button(Layout(), "filament", 0, filament_id, "NONE")
        ui.draw_preset_button(Layout(), "filament", 1, "", "NONE")
        shown = [c[2] for c in calls]
        self.assertEqual(shown, [process_id.partition("/")[2], filament_id.partition("/")[2], "Choose..."])
        self.assertTrue(all("sys:" not in t for t in shown))
        self.assertEqual((pg.process_id, pg.filaments[0].preset_id), (process_id, filament_id))   # the data is unchanged
        tip = bpy.types.SLICEWRIGHT_OT_pick_preset.description(bpy.context, type("P", (), {"kind": "process", "index": -1}))
        self.assertIn(process_id, tip)                                           # the full id is in the tooltip
        self.assertIn("Vendor: Bolt3D", tip)

    def test_the_search_popup_lists_names_sets_the_id_and_runs_the_update_callbacks(self):
        from slicewright.blender import picker
        self.load()
        pg = self.pg
        pg.printer_id = "sys:Acme/Acme Maker 1 0.6 nozzle"
        choices = picker.preset_choices(pg, "process")
        self.assertEqual([c[0] for c in choices],
                         ["sys:Acme/0.20mm Strong @Acme", "sys:Acme/0.30mm Draft @Acme"])      # sorted by display name
        self.assertEqual([c[1] for c in choices], ["0.20mm Strong @Acme", "0.30mm Draft @Acme"])
        self.assertTrue(all(c[0] in c[2] for c in choices))
        self.assertEqual(bpy.ops.slicewright.pick_preset(kind="process", choice="sys:Acme/0.20mm Strong @Acme"),
                         {"FINISHED"})
        self.assertEqual(pg.process_id, "sys:Acme/0.20mm Strong @Acme")
        self.assertAlmostEqual(pg.process_edits.layer_height, 0.2, places=5)                   # on_process_id loaded the buffer
        other = picker.preset_choices(pg, "filament")[0][0]
        self.assertEqual(bpy.ops.slicewright.pick_preset(kind="filament", index=0, choice=other), {"FINISHED"})
        self.assertEqual(pg.filaments[0].preset_id, other)
        self.assertEqual(bpy.ops.slicewright.pick_preset(kind="filament", index=9, choice=other), {"CANCELLED"})

    def test_the_picker_operator_needs_the_library(self):
        with self.assertRaises(RuntimeError):
            bpy.ops.slicewright.pick_preset(kind="process", choice="NONE")

    def test_filament_operators(self):
        self.load()
        pg = self.pg
        pg.printer_id = "sys:Bolt3D/Bolt One 0.4 nozzle"
        self.assertEqual(len(pg.filaments), 1)
        self.assertEqual(bpy.ops.slicewright.filament_add(), {"FINISHED"})
        self.assertEqual(bpy.ops.slicewright.filament_add(), {"FINISHED"})
        self.assertEqual(len(pg.filaments), 3)
        self.assertEqual(bpy.ops.slicewright.filament_remove(index=0), {"FINISHED"})
        self.assertEqual(len(pg.filaments), 2)
        self.assertEqual(bpy.ops.slicewright.filament_remove(index=9), {"CANCELLED"})
        pg.filaments.remove(1)
        with self.assertRaises(RuntimeError):                   # poll: the last slot stays
            bpy.ops.slicewright.filament_remove(index=0)

    def test_add_default_materials_appends_only_the_missing_ones_and_leaves_slots_alone(self):
        from slicewright.blender import picker
        self.load()
        pg = self.pg
        pg.printer_id = "sys:Acme/Acme Maker 1 0.4 nozzle"
        self.assertEqual(names(s.preset_id for s in pg.filaments), ["Generic PETG"])
        pg.filaments[0].color = (0.1, 0.2, 0.3)
        pg.filaments[0].ams_slot = 3
        self.assertEqual(bpy.ops.slicewright.filament_add_defaults(), {"FINISHED"})
        self.assertEqual(names(s.preset_id for s in pg.filaments), ["Generic PETG", "Acme PLA Pro"])
        self.assertEqual([round(c, 3) for c in pg.filaments[0].color], [0.1, 0.2, 0.3])   # the first slot is untouched
        self.assertEqual(pg.filaments[0].ams_slot, 3)
        self.assertEqual(picker.missing_default_materials(pg), [])
        self.assertEqual(picker.add_default_materials(pg), 0)                            # idempotent
        with self.assertRaises(RuntimeError):                                              # poll: nothing left to add
            bpy.ops.slicewright.filament_add_defaults()
        self.assertEqual(len(pg.filaments), 2)

    def test_add_default_materials_keeps_a_users_own_choice_and_does_not_duplicate_it(self):
        from slicewright.blender import picker
        self.load()
        pg = self.pg
        pg.printer_id = "sys:Acme/Acme Maker 1 0.4 nozzle"
        pg.filaments[0].preset_id = "sys:Acme/Acme PLA Pro"      # the user swapped the first slot for the second default
        self.assertEqual(names(e.id for e in picker.missing_default_materials(pg)), ["Generic PETG"])
        picker.add_default_materials(pg)
        self.assertEqual(names(s.preset_id for s in pg.filaments), ["Acme PLA Pro", "Generic PETG"])

    def test_add_default_materials_marks_the_plate_stale_like_any_slot_change(self):
        from slicewright.blender import picker, staleness
        self.load()
        pg = self.pg
        pg.printer_id = "sys:Acme/Acme Maker 1 0.4 nozzle"
        marked = []
        real = staleness.mark
        staleness.mark = lambda *a, **k: (marked.append(1), real(*a, **k))[1]
        self.addCleanup(setattr, staleness, "mark", real)
        picker.add_default_materials(pg)
        self.assertGreaterEqual(len(marked), 1)                              # the new slot marks the plate (slot add/remove logic intact)

    def test_a_printer_without_default_materials_offers_none_and_a_user_printer_has_no_model(self):
        from slicewright.blender import picker
        self.load()
        pg = self.pg
        self.assertEqual(picker.missing_default_materials(pg), [])           # no printer chosen
        with self.assertRaises(RuntimeError):
            bpy.ops.slicewright.filament_add_defaults()
        pg.printer_id = "user:not-there"
        self.assertEqual(picker.missing_default_materials(pg), [])

    def test_changing_the_printer_still_replaces_the_slots_with_the_new_printers_first_default(self):
        self.load()
        pg = self.pg
        pg.printer_id = "sys:Acme/Acme Maker 1 0.4 nozzle"
        bpy.ops.slicewright.filament_add_defaults()
        bpy.ops.slicewright.filament_add()
        self.assertEqual(len(pg.filaments), 3)
        pg.printer_id = "sys:Bolt3D/Bolt One 0.4 nozzle"                     # unchanged behaviour: a new printer resets
        self.assertEqual(len(pg.filaments), 1)

    def test_the_load_operator_retries_after_a_failure(self):
        sc = self.addon.engine.adapter.load().module
        orig = sc.profiles_archive
        sc.profiles_archive = lambda: "/nonexistent/profiles.zip"
        self.addCleanup(setattr, sc, "profiles_archive", orig)
        self.library.request()
        self.timers.runner.run_until_idle()
        state, _, message = self.library.status()
        self.assertEqual(state, "failed")
        self.assertIn("profile archive", message)
        self.library.request()                                  # without force: stays failed
        self.assertEqual(self.library.status()[0], "failed")
        sc.profiles_archive = orig
        self.assertEqual(bpy.ops.slicewright.load_library(), {"FINISHED"})
        self.timers.runner.run_until_idle()
        self.assertEqual(self.library.status()[0], "ready")

    def test_each_tick_stays_within_the_budget(self):
        self.library.request()
        worst = 0.0
        while True:
            t0 = time.perf_counter()
            more = self.timers._tick()
            worst = max(worst, time.perf_counter() - t0)
            if more is None:
                break
        self.assertLess(worst, 0.025)


if __name__ == "__main__":
    bl_common.run("__main__")
