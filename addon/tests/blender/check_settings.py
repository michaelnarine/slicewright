# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: edit buffers, settings pages, modes, filter, custom drawers and overrides.

Pages are drawn into a recording layout (``-b`` has no UI), so what is asserted is which rows the
drawing code asks Blender for.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


class Rec:
    """A stand-in for ``UILayout`` that records ``prop``, ``label`` and ``operator`` calls."""

    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls
        self.enabled = True
        self.active = True

    def _child(self, name):
        self.calls.append((name,))
        child = Rec(self.calls)
        child.enabled = self.enabled
        return child

    row = column = box = lambda self, **kw: self._child("box")

    def prop(self, data, key, **kw):
        self.calls.append(("prop", key, kw.get("text"), self.enabled))

    def enabled_of(self, key):
        return [c[3] for c in self.calls if c[0] == "prop" and c[1] == key][0]

    def label(self, **kw):
        self.calls.append(("label", kw.get("text")))

    def operator(self, idname, **kw):
        self.calls.append(("operator", idname, kw.get("text")))
        return type("Op", (), {})()

    def separator(self, **kw):
        pass

    def props(self):
        return [c[1] for c in self.calls if c[0] == "prop"]

    def labels(self):
        return [c[1] for c in self.calls if c[0] == "label"]


class Prefs:
    def __init__(self, mode="advanced", develop=False):
        self.settings_mode, self.show_develop = mode, develop


class SettingsTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright.blender import config_pg, library, picker, timers
        from slicewright.blender.ui import settings
        self.addon, self.cp, self.library = slicewright, config_pg, library
        self.picker, self.timers, self.st = picker, timers, settings
        self.addCleanup(self.finish)
        self.addon.register()
        self.pg = bpy.context.scene.slicewright
        self.reset()
        library.request()
        timers.runner.run_until_idle()
        self.pg.printer_id = "sys:Acme/Acme Maker 1 0.4 nozzle"

    def reset(self):
        self.pg.filaments.clear()
        for key in ("printer_id", "process_id", "pick_vendor", "pick_model", "pick_nozzle",
                    "settings_role", "settings_page", "settings_filter", "settings_slot"):
            self.pg.property_unset(key)
        self.cp.clear(self.pg.printer_edits)
        self.cp.clear(self.pg.process_edits)

    def finish(self):
        self.reset()
        self.addon.unregister()

    def draw(self, role, page, mode="advanced", text="", develop=False, slot=0):
        pg = self.pg
        pg.settings_role, pg.settings_slot, pg.settings_filter = role, slot, text
        pg.settings_page = page
        rec = Rec()
        self.st.draw_settings(rec, pg, Prefs(mode, develop))
        return rec

    # -- edit buffers -----------------------------------------------------------------------------

    def test_picking_a_printer_fills_both_edit_buffers_completely(self):
        pe, qe = self.pg.printer_edits, self.pg.process_edits
        self.assertEqual(pe.printable_area, "0x0,220x0,220x220,0x220")
        self.assertEqual(pe.nozzle_diameter, "0.4")
        self.assertAlmostEqual(qe.layer_height, 0.2, places=6)
        self.assertEqual(qe.wall_loops, 2)
        self.assertTrue(all(qe.is_property_set(k) for k in ("layer_height", "enable_support")))
        self.pg.pick_nozzle = "0.6"
        self.assertEqual(self.pg.process_id, "sys:Acme/0.30mm Draft @Acme")
        self.assertAlmostEqual(qe.layer_height, 0.3, places=6)

    def test_changing_only_the_process_keeps_printer_edits(self):
        self.pg.printer_edits.printable_height = 999.0
        self.pg.process_id = "sys:Acme/0.20mm Strong @Acme"
        self.assertEqual(self.pg.process_edits.wall_loops, 4)
        self.assertEqual(self.pg.printer_edits.printable_height, 999.0)

    # -- pages, modes, filter ---------------------------------------------------------------------

    def test_a_page_draws_its_groups_and_rows(self):
        rec = self.draw("process", "Strength")
        self.assertEqual([k for k in rec.props() if not k.startswith("settings_")][:3],
                         ["wall_loops", "sparse_infill_density", "sparse_infill_pattern"])
        self.assertIn("Walls", rec.labels())
        self.assertIn("settings_role", rec.props())
        self.assertEqual(self.draw("process", "Quality").props().count("layer_height"), 1)

    def test_page_items_come_from_the_engine_layout(self):
        names = [i[0] for i in self.st.page_items("process")]
        self.assertEqual(names[:4], ["Quality", "Strength", "Speed", "Support"])
        self.assertIn("Others", names)                          # the schema-category fallback page
        self.assertEqual([i[0] for i in self.st.page_items("filament")], ["Filament", "Cooling"])

    def test_modes_hide_advanced_options(self):
        simple = self.draw("process", "Others", mode="simple").props()
        advanced = self.draw("process", "Others", mode="advanced").props()
        self.assertIn("skirt_loops", simple)
        self.assertNotIn("filename_format", simple)
        self.assertIn("filename_format", advanced)

    def test_filter_limits_rows_and_reports_no_match(self):
        rec = self.draw("process", "Strength", text="sparse pattern")
        self.assertEqual([k for k in rec.props() if not k.startswith("settings_")], ["sparse_infill_pattern"])
        rec = self.draw("process", "Strength", text="zzz")
        self.assertIn("No settings match the filter on this page", rec.labels())

    def test_the_bed_and_extruder_custom_drawers(self):
        rec = self.draw("printer", "Basic information")
        self.assertIn("Bed 220 x 220 mm", rec.labels())
        self.assertEqual([k for k in rec.props() if not k.startswith("settings_")],
                         ["printable_area", "bed_exclude_area", "printable_height", "gcode_flavor"])
        rec = self.draw("printer", "Extruder")
        self.assertIn("Extruders: 1", rec.labels())
        self.pg.printer_edits.nozzle_diameter = "0.4,0.4"
        self.assertIn("Extruders: 2", self.draw("printer", "Extruder").labels())

    def test_nothing_is_drawn_without_a_preset(self):
        self.pg.printer_id = ""
        rec = self.draw("printer", "Basic information")
        self.assertIn("Choose a preset first", rec.labels())

    def test_rules_gate_rows_by_the_buffer_values(self):
        qe = self.pg.process_edits
        qe.enable_support = False
        rec = self.draw("process", "Support")
        self.assertFalse(rec.enabled_of("support_type"))             # in the schema-category "More" group
        self.assertNotIn("max_bridge_length", rec.props())            # tree-only: hidden
        qe.enable_support = True
        qe.support_type = "tree(auto)"
        rec = self.draw("process", "Support")
        self.assertTrue(rec.enabled_of("support_type"))
        self.assertIn("max_bridge_length", rec.props())
        self.assertTrue(rec.enabled_of("max_bridge_length"))
        qe.support_type = "normal(auto)"
        self.assertNotIn("max_bridge_length", self.draw("process", "Support").props())

    def test_rules_read_the_preset_value_for_unset_overrides(self):
        slot = self.pg.filaments[0]
        slot.preset_id = "sys:OrcaFilamentLibrary/Generic PETG"
        rec = self.draw("filament", "Cooling")
        self.assertIn("Pressure advance: 0.02", rec.labels())         # unset: shown as inherited text
        slot.overrides.enable_pressure_advance = "1"      # per-extruder flags are text
        slot.overrides.pressure_advance = "0.05"
        rec = self.draw("filament", "Cooling")
        self.assertTrue(rec.enabled_of("pressure_advance"))
        slot.overrides.enable_pressure_advance = "0"
        self.assertFalse(self.draw("filament", "Cooling").enabled_of("pressure_advance"))

    # -- override buffers (filament role) ---------------------------------------------------------

    def test_unset_overrides_show_the_inherited_value_and_an_override_button(self):
        self.assertEqual(self.pg.filaments[0].preset_id, "sys:OrcaFilamentLibrary/Generic PETG")
        rec = self.draw("filament", "Filament")
        self.assertIn("Nozzle temperature: 240", rec.labels())
        self.assertIn(("operator", "slicewright.settings_override", "Override"), rec.calls)
        self.assertNotIn("nozzle_temperature", rec.props())

    def test_override_then_reset_through_the_operator(self):
        slot = self.pg.filaments[0]
        self.pg.settings_role = "filament"
        self.assertEqual(bpy.ops.slicewright.settings_override(key="nozzle_temperature", action="SET"),
                         {"FINISHED"})
        self.assertEqual(self.cp.overridden(slot.overrides), {"nozzle_temperature": "240"})
        rec = self.draw("filament", "Filament")
        self.assertIn("nozzle_temperature", rec.props())          # now an editable row
        self.assertIn(("operator", "slicewright.settings_override", ""), rec.calls)
        slot.overrides.nozzle_temperature = "255"
        self.assertEqual(bpy.ops.slicewright.settings_override(key="nozzle_temperature", action="RESET"),
                         {"FINISHED"})
        self.assertEqual(self.cp.overridden(slot.overrides), {})

    def test_override_operator_refuses_buffers_that_are_not_overrides(self):
        self.pg.settings_role = "process"
        self.assertEqual(bpy.ops.slicewright.settings_override(key="layer_height", action="SET"),
                         {"CANCELLED"})

    def test_filament_page_selects_the_slot(self):
        self.assertGreater(len(self.pg.filaments), 1)
        rec = self.draw("filament", "Filament", slot=1)
        self.assertIn("settings_slot", rec.props())
        self.assertIn("Nozzle temperature: 210", rec.labels())    # Acme PLA Pro inherits fdm_filament_pla

    def test_preferences_fall_back_to_defaults_when_the_addon_is_not_enabled(self):
        from slicewright import prefs
        got = prefs.get()
        self.assertEqual((got.settings_mode, got.show_develop), ("advanced", False))


if __name__ == "__main__":
    bl_common.run("__main__")
