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

from slicewright.core import settings_view as sv  # noqa: E402


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

    row = column = box = split = grid_flow = lambda self, **kw: self._child("box")

    def prop(self, data, key, **kw):
        self.calls.append(("prop", key, kw.get("text"), self.enabled))
        if kw.get("expand"):
            self.calls.append(("expanded", key))

    def enabled_of(self, key):
        return [c[3] for c in self.calls if c[0] == "prop" and c[1] == key][0]

    def label(self, **kw):
        self.calls.append(("label", kw.get("text")))

    def operator(self, idname, **kw):
        op = type("Op", (), {})()
        self.calls.append(("operator", idname, kw.get("text"), op))
        return op

    def separator(self, **kw):
        pass

    def operators(self, idname):
        return [c for c in self.calls if c[0] == "operator" and c[1] == idname]

    def headers(self):
        """The section and group headers drawn, as (id, title, open)."""
        return [(c[3].section, c[2]) for c in self.operators("slicewright.settings_section") if c[2]]

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
                    "settings_role", "settings_page", "settings_filter", "settings_slot", "favourites"):
            self.pg.property_unset(key)
        self.cp.clear(self.pg.printer_edits)
        self.cp.clear(self.pg.process_edits)

    def finish(self):
        self.reset()
        self.addon.unregister()

    def draw(self, role, page, mode="advanced", text="", develop=False, slot=0, width=None, open_all=True):
        """Draw the panel with ``page`` opened (every group of it too, unless ``open_all`` is off)."""
        pg = self.pg
        pg.settings_role, pg.settings_slot, pg.settings_filter = role, slot, text
        pg.settings_page = page
        wm = bpy.context.window_manager
        ids = [sv.section_id(role, page)]
        if open_all:
            ids += [sv.group_id(role, page, i) for i, _g in enumerate(self.st.pages_for(role)[[p.name for p in self.st.pages_for(role)].index(page)].groups)]
        setattr(wm, self.st.WM_OPEN, sv.join_set(ids))
        rec = Rec()
        self.st.draw_settings(rec, pg, Prefs(mode, develop), width_chars=width)
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
        self.assertIn("Walls", [title for _id, title in rec.headers()])
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
        self.assertIn("No settings match 'zzz'", rec.labels())

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
        self.assertIn("0.02", rec.labels())         # unset: shown as inherited text
        slot.overrides.enable_pressure_advance = "1"      # per-extruder flags are text
        slot.overrides.pressure_advance = "0.05"
        rec = self.draw("filament", "Cooling")
        self.assertTrue(rec.enabled_of("pressure_advance"))
        slot.overrides.enable_pressure_advance = "0"
        self.assertFalse(self.draw("filament", "Cooling").enabled_of("pressure_advance"))

    def test_rows_put_the_label_left_when_it_fits_and_above_when_it_does_not(self):
        from slicewright.blender import config_pg
        from slicewright.blender.ui import settings
        cfg = self.pg.process_edits
        look = settings.Look(cell=40)
        rec = Rec()
        settings.draw_key(rec, cfg, "layer_height", override=False, look=look)
        self.assertEqual(rec.props(), ["layer_height"])
        self.assertIn(("prop", "layer_height", "Layer height", True), rec.calls)      # native label column
        self.assertIn(("operator", "slicewright.settings_favourite"), [c[:2] for c in rec.calls])
        narrow = Rec()
        long_keys = [k for k, sp in config_pg.specs().items() if len(sp.label) > 24 and k in cfg.bl_rna.properties
                     and cfg.bl_rna.properties[k].type != "ENUM"]
        self.assertTrue(long_keys, "the fake schema has no setting with a long label")
        settings.draw_key(narrow, cfg, long_keys[0], override=False, look=settings.Look(cell=20))
        self.assertIn(("prop", long_keys[0], "", True), narrow.calls)                 # label above, field without one
        self.assertIn(config_pg.specs()[long_keys[0]].label.split()[0], " ".join(narrow.labels()))

    def test_short_enums_are_segmented_and_the_rest_are_dropdowns(self):
        from slicewright.blender import config_pg
        from slicewright.blender.ui import choice, settings
        from slicewright.core import choice_layout as cl
        cfg = self.pg.process_edits
        found = {"segmented": [], "dropdown": []}
        for key, spec in config_pg.specs().items():
            if spec.kind != "enum" or key not in cfg.bl_rna.properties:
                continue
            labels = [i[1] for i in spec.enum_items]
            want = cl.choose(labels, cl.DEFAULT_WIDTH_CHARS)
            self.assertEqual(choice.style_of(cfg, key, cl.DEFAULT_WIDTH_CHARS), want, key)
            rec = Rec()
            settings.draw_key(rec, cfg, key, override=False)
            self.assertEqual(("expanded", key) in rec.calls, want == "segmented", key)
            self.assertEqual(rec.props(), [key])
            self.assertIn(key, rec.props())
            if want == "segmented":
                self.assertIn(("prop", key, None, True), rec.calls)
            found[want].append(key)
        self.assertIn("wall_generator", found["segmented"])           # 2 options, short
        self.assertIn("sparse_infill_pattern", found["dropdown"])     # many options
        self.assertIn("support_type", found["dropdown"])              # four options
        self.assertEqual(choice.style_of(cfg, "layer_height"), "")    # not an enum

    def test_an_unset_enum_override_shows_the_option_label(self):
        from slicewright.blender.ui import settings
        self.assertEqual(settings.display_value("wall_generator", "arachne"), "Arachne")
        self.assertEqual(settings.display_value("wall_generator", "nonsense"), "nonsense")
        self.assertEqual(settings.display_value("no_such_key", "1"), "1")

    # -- override buffers (filament role) ---------------------------------------------------------

    def test_unset_overrides_show_the_inherited_value_and_an_override_button(self):
        self.assertEqual(self.pg.filaments[0].preset_id, "sys:OrcaFilamentLibrary/Generic PETG")
        rec = self.draw("filament", "Filament")
        self.assertIn("Nozzle temperature", rec.labels())
        self.assertIn("240", rec.labels())
        self.assertIn("Override", [c[2] for c in rec.operators("slicewright.settings_override")])
        self.assertNotIn("nozzle_temperature", rec.props())

    def test_override_then_reset_through_the_operator(self):
        slot = self.pg.filaments[0]
        self.pg.settings_role = "filament"
        self.assertEqual(bpy.ops.slicewright.settings_override(key="nozzle_temperature", action="SET"),
                         {"FINISHED"})
        self.assertEqual(self.cp.overridden(slot.overrides), {"nozzle_temperature": "240"})
        rec = self.draw("filament", "Filament")
        self.assertIn("nozzle_temperature", rec.props())          # now an editable row
        self.assertIn("", [c[2] for c in rec.operators("slicewright.settings_override")])     # the reset button
        slot.overrides.nozzle_temperature = "255"
        self.assertEqual(bpy.ops.slicewright.settings_override(key="nozzle_temperature", action="RESET"),
                         {"FINISHED"})
        self.assertEqual(self.cp.overridden(slot.overrides), {})

    def test_override_operator_refuses_buffers_that_are_not_overrides(self):
        self.pg.settings_role = "process"
        self.assertEqual(bpy.ops.slicewright.settings_override(key="layer_height", action="SET"),
                         {"CANCELLED"})

    def test_filament_page_selects_the_slot(self):
        self.assertEqual(len(self.pg.filaments), 1)                # a printer brings its first default material only
        self.assertEqual(bpy.ops.slicewright.filament_add_defaults(), {"FINISHED"})
        self.assertGreater(len(self.pg.filaments), 1)
        rec = self.draw("filament", "Filament", slot=1)
        self.assertIn("settings_slot", rec.props())
        self.assertIn("210", rec.labels())    # Acme PLA Pro inherits fdm_filament_pla

    # -- sections, favourites, search, width -------------------------------------------------------

    def open_text(self):
        return getattr(bpy.context.window_manager, self.st.WM_OPEN)

    def draw_closed(self, role="process", **kw):
        setattr(bpy.context.window_manager, self.st.WM_OPEN, "")
        pg = self.pg
        pg.settings_role, pg.settings_filter = role, kw.get("text", "")
        rec = Rec()
        self.st.draw_settings(rec, pg, Prefs(kw.get("mode", "advanced")), width_chars=kw.get("width"))
        return rec

    def test_closed_sections_draw_only_their_headers(self):
        rec = self.draw_closed()
        titles = [t for _i, t in rec.headers()]
        self.assertEqual(titles[:5], ["Favourites", "Quality", "Strength", "Speed", "Support"])
        self.assertEqual([k for k in rec.props() if not k.startswith("settings_")], [])
        self.assertNotIn("wall_loops", rec.props())

    def test_an_open_section_shows_group_headers_and_an_open_group_shows_rows(self):
        wm = bpy.context.window_manager
        setattr(wm, self.st.WM_OPEN, sv.section_id("process", "Strength"))
        rec = Rec()
        self.pg.settings_role, self.pg.settings_filter = "process", ""
        self.st.draw_settings(rec, self.pg, Prefs())
        self.assertIn("Walls", [t for _i, t in rec.headers()])
        self.assertNotIn("wall_loops", rec.props())
        setattr(wm, self.st.WM_OPEN, sv.join_set([sv.section_id("process", "Strength"), sv.group_id("process", "Strength", 0)]))
        rec = Rec()
        self.st.draw_settings(rec, self.pg, Prefs())
        self.assertEqual([k for k in rec.props() if not k.startswith("settings_")], ["wall_loops"])

    def test_the_section_operator_toggles_and_collapses_everything(self):
        wm = bpy.context.window_manager
        setattr(wm, self.st.WM_OPEN, "")
        self.assertEqual(bpy.ops.slicewright.settings_section(section="process/Speed"), {"FINISHED"})
        self.assertEqual(self.open_text(), "process/Speed")
        bpy.ops.slicewright.settings_section(section="process/Speed")
        self.assertEqual(self.open_text(), "")
        bpy.ops.slicewright.settings_section(section="a")
        bpy.ops.slicewright.settings_section(section="")
        self.assertEqual(self.open_text(), "")

    def test_a_modified_setting_counts_in_its_headers_and_resets_to_the_preset_value(self):
        self.pg.process_edits.wall_loops = 5
        rec = self.draw("process", "Strength")
        self.assertIn(("process/Strength", "Strength"), rec.headers())
        self.assertIn("1 modified", rec.labels())
        resets = [c[3] for c in rec.operators("slicewright.settings_override") if c[2] == ""]
        self.assertEqual([(o.key, o.action) for o in resets], [("wall_loops", "RESET")])
        self.assertEqual(bpy.ops.slicewright.settings_override(key="wall_loops", action="RESET"), {"FINISHED"})
        self.assertEqual(self.pg.process_edits.wall_loops, 2)                       # the preset's own value
        self.assertNotIn("1 modified", self.draw("process", "Strength").labels())

    def test_pinning_a_setting_shows_it_in_favourites_with_the_same_controls(self):
        self.assertEqual(bpy.ops.slicewright.settings_favourite(key="wall_loops"), {"FINISHED"})
        self.assertEqual(self.pg.favourites, "wall_loops")
        self.assertTrue(sv.contains(self.open_text(), sv.FAVOURITES_ID))
        self.pg.process_edits.wall_loops = 4
        rec = Rec()
        self.pg.settings_role, self.pg.settings_filter = "process", ""
        setattr(bpy.context.window_manager, self.st.WM_OPEN, sv.FAVOURITES_ID)
        self.st.draw_settings(rec, self.pg, Prefs())
        self.assertEqual([k for k in rec.props() if not k.startswith("settings_")], ["wall_loops"])
        self.assertIn(("operator", "slicewright.settings_favourite"), [c[:2] for c in rec.calls])
        self.assertIn("", [c[2] for c in rec.operators("slicewright.settings_override")])      # its reset button
        self.assertEqual(bpy.ops.slicewright.settings_favourite(key="wall_loops"), {"FINISHED"})
        self.assertEqual(self.pg.favourites, "")
        self.assertEqual(bpy.ops.slicewright.settings_favourite(key="no_such_key"), {"CANCELLED"})

    def test_favourites_of_other_roles_use_their_own_buffer(self):
        bpy.ops.slicewright.settings_favourite(key="wall_loops")
        bpy.ops.slicewright.settings_favourite(key="printable_height")
        rec = Rec()
        self.pg.settings_role, self.pg.settings_filter = "filament", ""
        setattr(bpy.context.window_manager, self.st.WM_OPEN, sv.FAVOURITES_ID)
        self.st.draw_settings(rec, self.pg, Prefs())
        self.assertEqual([k for k in rec.props() if not k.startswith("settings_")], ["printable_height", "wall_loops"][::1]
                         if False else [k for k in rec.props() if k in ("wall_loops", "printable_height")])
        self.assertEqual(sorted(k for k in rec.props() if k in ("wall_loops", "printable_height")),
                         ["printable_height", "wall_loops"])

    def test_the_search_opens_matching_sections_and_hides_the_rest(self):
        rec = self.draw_closed(text="sparse pattern")
        self.assertEqual([k for k in rec.props() if not k.startswith("settings_")], ["sparse_infill_pattern"])
        titles = [t for _i, t in rec.headers()]
        self.assertIn("Strength", titles)
        self.assertNotIn("Speed", titles)
        self.assertIn("1 found", rec.labels())

    def test_the_search_also_filters_favourites(self):
        bpy.ops.slicewright.settings_favourite(key="wall_loops")
        self.assertNotIn("wall_loops", self.draw_closed(text="sparse").props())
        self.assertIn("wall_loops", self.draw_closed(text="wall loops").props())

    def test_a_wide_region_draws_two_columns_and_narrow_one(self):
        wide = self.draw("process", "Strength", width=100)
        self.assertGreater(sum(1 for c in wide.calls if c == ("box",)), 0)
        grid_calls = []
        orig = Rec.grid_flow
        Rec.grid_flow = lambda self, **kw: (grid_calls.append(kw.get("columns")), orig(self, **kw))[1]
        try:
            self.draw("process", "Strength", width=100)
            self.draw("process", "Strength", width=34)
        finally:
            Rec.grid_flow = orig
        self.assertIn(2, grid_calls)
        self.assertEqual(grid_calls.count(2), len([c for c in grid_calls if c == 2]))
        self.assertNotIn(3, grid_calls)

    def test_wide_regions_allow_segmented_choices_that_narrow_ones_turn_into_dropdowns(self):
        from slicewright.blender.ui import choice
        cfg = self.pg.process_edits
        self.assertEqual(choice.style_of(cfg, "wall_generator", 40), "segmented")
        self.assertEqual(choice.style_of(cfg, "wall_generator", 8), "dropdown")

    def test_focusing_a_setting_opens_its_section_and_group(self):
        from slicewright.blender.operators import issues as issue_ops
        setattr(bpy.context.window_manager, self.st.WM_OPEN, "")
        issue_ops.focus_setting(bpy.context, "sparse_infill_density")
        self.assertTrue(sv.contains(self.open_text(), "process/Strength"))
        self.assertTrue(sv.contains(self.open_text(), "process/Strength/1"))

    def test_the_layout_structure_is_cached_per_level(self):
        a = self.st.visible_for("process", "advanced", False)
        self.assertIs(a, self.st.visible_for("process", "advanced", False))
        self.assertIsNot(a, self.st.visible_for("process", "simple", False))
        self.st.clear()
        self.assertIsNot(a, self.st.visible_for("process", "advanced", False))

    def test_preferences_fall_back_to_defaults_when_the_addon_is_not_enabled(self):
        from slicewright import prefs
        got = prefs.get()
        self.assertEqual((got.settings_mode, got.show_develop), ("advanced", False))


if __name__ == "__main__":
    bl_common.run("__main__")
