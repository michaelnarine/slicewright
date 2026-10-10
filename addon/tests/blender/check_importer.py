# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the preset import operator over synthetic OrcaSlicer / BambuStudio folders."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


def tree_digest(root):
    """Names, sizes, mtimes and content hashes of everything below ``root`` (to prove nothing changed)."""
    out = {}
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(base, name)
            st = os.stat(path)
            digest = hashlib.sha1(open(path, "rb").read()).hexdigest() if os.path.isfile(path) else ""
            out[os.path.relpath(path, root)] = (st.st_size, st.st_mtime_ns, digest)
    return out


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


class ImporterTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright.blender import library, timers, user_presets
        from slicewright.blender.operators import importer
        self.addon, self.library, self.importer, self.up = slicewright, library, importer, user_presets
        self.tmp = tempfile.mkdtemp(prefix="slicewright-import-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store_dir = os.path.join(self.tmp, "ours")
        user_presets.set_presets_dir(self.store_dir)
        self.addCleanup(user_presets.set_presets_dir, None)
        self.orca = os.path.join(self.tmp, "OrcaSlicer")
        self.bambu = os.path.join(self.tmp, "BambuStudio")
        self.addCleanup(importer.set_dirs_override, None)
        importer.set_dirs_override([("OrcaSlicer", self.orca), ("BambuStudio", self.bambu), ("Gone", self.tmp + "/none")])
        self.before = bl_common.handler_snapshot()
        self.addCleanup(self.finish)
        self.addon.register()
        self.pg = bpy.context.scene.slicewright
        self.pg.filaments.clear()
        library.request()
        timers.runner.run_until_idle()
        self.lib = library.get()

    def finish(self):
        self.pg.filaments.clear()
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    def make_dirs(self):
        write(f"{self.orca}/user/1234/process/My strong.json",
              {"type": "process", "name": "My strong", "inherits": "0.20mm Standard @Acme",
               "from": "User", "wall_loops": "5", "layer_height": "0.2", "setting_id": "abc"})
        write(f"{self.orca}/user/default/machine/Odd printer.json",
              {"type": "machine", "name": "Odd printer", "inherits": "Vendor Does Not Ship This",
               "printable_height": "321", "printhost_apikey": "SECRET", "print_host": "10.0.0.5"})
        write(f"{self.bambu}/user/9/filament/Tuned PETG.json",
              {"type": "filament", "name": "Tuned PETG", "inherits": "Generic PETG", "nozzle_temperature": ["245"]})

    def test_nothing_is_scanned_until_the_user_asks(self):
        self.make_dirs()
        self.assertEqual(self.importer._pending, [])
        self.assertEqual(self.lib.store.list("process"), [])

    def test_the_operator_imports_every_found_preset_and_leaves_the_source_untouched(self):
        self.make_dirs()
        digest = tree_digest(self.tmp)
        self.assertEqual(bpy.ops.slicewright.import_presets(), {"FINISHED"})
        # the other apps' folders are byte-for-byte what they were (our own store lives beside them)
        after = {k: v for k, v in tree_digest(self.tmp).items() if not k.startswith("ours")}
        self.assertEqual({k: v for k, v in digest.items() if not k.startswith("ours")}, after)
        self.assertEqual(self.lib.store.list("process"), ["My strong"])
        self.assertEqual(self.lib.store.list("filament"), ["Tuned PETG"])
        self.assertEqual(self.lib.store.list("machine"), ["Odd printer"])
        with open(os.path.join(self.store_dir, "process", "My strong.json"), encoding="utf-8") as fh:
            strong = json.load(fh)
        self.assertEqual(strong["inherits"], "0.20mm Standard @Acme")              # parent known: kept
        self.assertEqual(strong["wall_loops"], "5")
        self.assertNotIn("layer_height", strong)                                   # equal to the parent: not copied
        self.assertNotIn("setting_id", strong)
        with open(os.path.join(self.store_dir, "machine", "Odd printer.json"), encoding="utf-8") as fh:
            odd = json.load(fh)
        self.assertNotIn("inherits", odd)                                          # parent unknown: standalone
        self.assertEqual(odd["printable_height"], "321")
        text = json.dumps(odd)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("10.0.0.5", text)

    def test_imported_presets_are_usable_in_the_picker(self):
        self.make_dirs()
        bpy.ops.slicewright.import_presets()
        self.pg.printer_id = "sys:Acme/Acme Maker 1 0.4 nozzle"
        self.pg.process_id = "user:My strong"
        self.assertEqual(self.pg.process_edits.wall_loops, 5)
        self.pg.printer_id = "user:Odd printer"
        self.assertEqual(self.pg.printer_edits.printable_height, 321.0)

    def test_importing_twice_does_not_overwrite(self):
        self.make_dirs()
        bpy.ops.slicewright.import_presets()
        bpy.ops.slicewright.import_presets()
        self.assertEqual(sorted(self.lib.store.list("process")), ["My strong", "My strong (imported)"])

    def test_the_selection_decides_what_is_imported(self):
        self.make_dirs()
        found = self.importer.find_candidates()
        self.assertEqual(len(found.candidates), 3)
        petg = [c for c in found.candidates if c.kind == "filament"]
        results = self.importer.run_import(petg)
        self.assertEqual([r.error for r in results], [None])
        self.assertEqual((self.lib.store.list("filament"), self.lib.store.list("process")), (["Tuned PETG"], []))

    def test_without_the_other_apps_there_is_nothing_to_do(self):
        shutil.rmtree(self.orca, ignore_errors=True)
        self.assertEqual(self.importer.find_candidates().candidates, [])
        self.assertEqual(bpy.ops.slicewright.import_presets(), {"CANCELLED"})

    def test_the_manifest_asks_for_the_files_permission(self):
        import tomllib
        path = os.path.join(bl_common.ADDON_DIR, "slicewright", "blender_manifest.toml")
        with open(path, "rb") as fh:
            manifest = tomllib.load(fh)
        self.assertIn("files", manifest["permissions"])
        self.assertLessEqual(len(manifest["permissions"]["files"]), 64)


if __name__ == "__main__":
    bl_common.run("__main__")
