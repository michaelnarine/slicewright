# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the generated ConfigPG and its three roles (03 section 2.3)."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402

TYPE = {"bool": "BOOLEAN", "int": "INT", "float": "FLOAT", "percent": "FLOAT", "enum": "ENUM",
        "string": "STRING"}


class ConfigPGTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import fake_engine
        import slicewright
        from slicewright.blender import config_pg
        self.sc, self.addon, self.cp = fake_engine, slicewright, config_pg
        self.before = bl_common.handler_snapshot()
        self.addCleanup(self.finish)
        self.addon.register()
        self.scene = bpy.context.scene
        self.ob = bpy.data.objects.new("Thing", None)
        self.addCleanup(lambda: bpy.data.objects.remove(self.ob))

    def finish(self):
        self.addon.unregister()
        self.assertFalse(hasattr(bpy.types.Scene, "slicewright"))
        self.assertFalse(hasattr(bpy.types.Object, "slicewright"))
        self.assertIsNone(getattr(bpy.types, self.cp.CLASS_NAME, None))
        self.assertEqual(self.cp.specs(), {})
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    # -- generation -------------------------------------------------------------------------------

    def test_every_schema_key_becomes_a_property_of_the_right_type(self):
        schema = self.sc.config_schema()
        props = self.cp.config_class().bl_rna.properties
        for key, entry in schema.items():
            self.assertIn(key, props, key)
            spec = self.cp.specs()[key]
            self.assertEqual(props[key].type, TYPE[spec.kind], key)
            self.assertEqual(props[key].name, entry["label"])
        self.assertEqual(props["sparse_infill_density"].subtype, "PERCENTAGE")
        self.assertEqual([i.identifier for i in props["sparse_infill_pattern"].enum_items][:4],
                         ["grid", "gyroid", "line", "cubic"])
        self.assertAlmostEqual(props["layer_height"].hard_min, -3.4e38, delta=1e37)
        self.assertAlmostEqual(props["layer_height"].soft_min, 0.01)     # soft limits from min/max

    def test_the_pointers_exist_on_scene_object_and_slot(self):
        pg = self.scene.slicewright
        self.assertEqual(type(pg.printer_edits).__name__, self.cp.CLASS_NAME)
        self.assertEqual(type(pg.process_edits).__name__, self.cp.CLASS_NAME)
        self.assertEqual(type(self.ob.slicewright.overrides).__name__, self.cp.CLASS_NAME)
        slot = pg.filaments.add()
        self.addCleanup(pg.filaments.clear)
        self.assertEqual(type(slot.overrides).__name__, self.cp.CLASS_NAME)
        self.assertEqual((self.ob.slicewright.filament, self.ob.slicewright.role), (0, "PART"))

    def test_keys_blender_cannot_hold_are_skipped_not_fatal(self):
        schema = self.sc.config_schema()
        schema.update({"name": dict(schema["layer_height"]), "bl_rna": dict(schema["layer_height"]),
                       "not an identifier": dict(schema["layer_height"])})
        cls, specs, skipped = self.cp.build_class(schema)
        self.assertEqual(sorted(skipped), ["bl_rna", "name", "not an identifier"])
        self.assertNotIn("name", specs)
        self.assertIn("layer_height", specs)

    def test_800_properties_register_quickly(self):
        entry = self.sc.config_schema()["layer_height"]
        schema = {f"synthetic_key_{i}": dict(entry) for i in range(800)}
        t0 = time.perf_counter()
        cls, specs, _ = self.cp.build_class(schema)
        cls.__name__ = "SLICEWRIGHT_PG_Synthetic"
        bpy.utils.register_class(cls)
        elapsed = time.perf_counter() - t0
        bpy.utils.unregister_class(cls)
        self.assertEqual(len(specs), 800)
        self.assertLess(elapsed, 0.5)                    # the spec measured 2.8 ms; CI gets slack

    # -- role 1: edit buffer ----------------------------------------------------------------------

    def test_the_edit_buffer_round_trips_a_normalised_config(self):
        sc = self.sc
        flat = sc.compose_config(
            {"name": "p", "printable_area": ["0x0", "220x0", "220x220", "0x220"],
             "nozzle_diameter": ["0.6"], "gcode_flavor": "klipper"},
            {"name": "q", "layer_height": "0.3", "sparse_infill_density": "12%", "wall_loops": "4",
             "enable_support": "1", "sparse_infill_pattern": "gyroid"},
            [{"name": "f", "filament_type": ["PETG"], "nozzle_temperature": ["240"],
              "filament_colour": ["#112233"]}])
        config = sc.normalize_config(flat)["config"]
        pg = self.scene.slicewright.process_edits
        self.assertEqual(self.cp.load_flat(pg, config), [])
        self.assertAlmostEqual(pg.layer_height, 0.3, places=6)
        self.assertEqual((pg.sparse_infill_density, pg.wall_loops, pg.enable_support),
                         (12.0, 4, True))
        self.assertEqual(pg.sparse_infill_pattern, "gyroid")
        back = self.cp.to_flat(pg)
        self.assertEqual(back, config)                    # every key, in the engine's own formats
        self.assertEqual(sc.normalize_config(back)["errors"], {})

    def test_load_accepts_preset_dict_vectors_and_resets_missing_keys(self):
        pg = self.scene.slicewright.printer_edits
        pg.layer_height = 0.9
        self.cp.load_flat(pg, {"nozzle_diameter": ["0.4", "0.6"], "filament_type": ["A", "B"],
                               "printable_area": ["0x0", "10x0"]})
        self.assertEqual((pg.nozzle_diameter, pg.filament_type, pg.printable_area),
                         ("0.4,0.6", '"A";"B"', "0x0,10x0"))
        self.assertAlmostEqual(pg.layer_height, 0.2, places=6)    # not in the dict: default, and set
        self.assertTrue(pg.is_property_set("layer_height"))
        pg.wall_loops = 7
        self.cp.load_flat(pg, {"layer_height": "0.1"}, reset=False)
        self.assertAlmostEqual(pg.layer_height, 0.1, places=6)
        self.assertEqual(pg.wall_loops, 7)

    def test_bad_values_are_reported_and_leave_the_old_value(self):
        pg = self.scene.slicewright.process_edits
        pg.wall_loops = 5
        problems = self.cp.load_flat(pg, {"wall_loops": "many", "sparse_infill_pattern": "swirl",
                                          "layer_height": "0.15"}, reset=False)
        self.assertEqual(len(problems), 2)
        self.assertEqual(pg.wall_loops, 5)
        self.assertAlmostEqual(pg.layer_height, 0.15, places=6)
        self.assertEqual(pg.sparse_infill_pattern, "grid")

    # -- roles 2 and 3: overrides -----------------------------------------------------------------

    def test_object_overrides_are_exactly_the_keys_that_were_set(self):
        ov = self.ob.slicewright.overrides
        self.assertEqual(self.cp.overridden(ov), {})
        ov.layer_height = 0.1
        ov.enable_support = True
        self.assertEqual(self.cp.overridden(ov), {"layer_height": "0.1", "enable_support": "1"})
        self.cp.clear(ov, "layer_height")
        self.assertEqual(self.cp.overridden(ov), {"enable_support": "1"})
        self.assertAlmostEqual(ov.layer_height, 0.2, places=6)       # back to the default
        self.cp.clear(ov)
        self.assertEqual(self.cp.overridden(ov), {})

    def test_setting_an_override_to_the_default_still_counts_as_an_override(self):
        ov = self.ob.slicewright.overrides
        ov.layer_height = 0.2
        self.assertEqual(self.cp.overridden(ov), {"layer_height": "0.2"})

    def test_slot_overrides_work_the_same_way(self):
        slot = self.scene.slicewright.filaments.add()
        self.addCleanup(self.scene.slicewright.filaments.clear)
        slot.overrides.nozzle_temperature = "255"
        self.assertEqual(self.cp.overridden(slot.overrides), {"nozzle_temperature": "255"})

    def test_overrides_only_use_object_or_region_scope_keys_when_filtered(self):
        schema = self.sc.config_schema()
        ov = self.ob.slicewright.overrides
        ov.layer_height = 0.1                              # scope object
        ov.skirt_loops = 3                                 # scope global: not accepted by the engine
        sendable = {k: v for k, v in self.cp.overridden(ov).items()
                    if schema[k]["scope"] in ("object", "region")}
        self.assertEqual(sendable, {"layer_height": "0.1"})

    # -- orphans, persistence ---------------------------------------------------------------------

    def test_orphan_keys_survive_and_the_engine_reports_them(self):
        pg = self.scene.slicewright.process_edits
        self.cp.load_flat(pg, {"fill_density": "20%", "gone_in_new_engine": "x"})
        flat = self.cp.to_flat(pg)
        self.assertEqual(flat["gone_in_new_engine"], "x")
        normalised = self.sc.normalize_config(flat)
        self.assertEqual([i["opt_key"] for i in normalised["issues"]], ["gone_in_new_engine"])
        self.assertEqual(normalised["config"]["sparse_infill_density"], "20%")   # legacy name still maps
        self.cp.clear(pg)
        self.assertNotIn("gone_in_new_engine", self.cp.to_flat(pg))

    def test_values_and_set_state_survive_a_save_and_reload(self):
        pg = self.scene.slicewright
        self.cp.load_flat(pg.process_edits, {"layer_height": "0.25"})
        self.ob.slicewright.overrides.wall_loops = 6
        self.scene.collection.objects.link(self.ob)
        path = os.path.join(tempfile.mkdtemp(prefix="slicewright-"), "t.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        scene = bpy.context.scene
        self.assertAlmostEqual(scene.slicewright.process_edits.layer_height, 0.25, places=6)
        ob = bpy.data.objects["Thing"]
        self.assertEqual(self.cp.overridden(ob.slicewright.overrides), {"wall_loops": "6"})
        self.ob = ob
        self.assertTrue(os.path.getsize(path) < 2_000_000)


if __name__ == "__main__":
    bl_common.run("__main__")
