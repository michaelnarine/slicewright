# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: baking the slice into Curves and meshes (03 section 7.11), on the fake engine or the REAL one.

``SLICEWRIGHT_CHECK_BACKEND=real`` selects the built ``slicewright_engine`` and the Bambu Lab A1 (skipped when it is not
built); the default is the fake. Timers never fire in ``-b`` (03 7.9a): the tests pump ``slicing.poll_job`` and drive the tick
runner themselves (``drain`` skips the GPU preview upload, ``run_bake`` ticks the bake task).
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402
import numpy as np  # noqa: E402

BACKEND = os.environ.get("SLICEWRIGHT_CHECK_BACKEND", "fake")
REAL = BACKEND == "real"
BUILD = os.path.join(bl_common.TESTS_DIR, os.pardir, os.pardir, "engine", "build", "engine", "python")
PRINTER = "Bambu Lab A1 0.4 nozzle"
if REAL:
    if not os.environ.get("SLICEWRIGHT_ENGINE_PATH") and os.path.isdir(os.path.join(BUILD, "slicewright_engine")):
        os.environ["SLICEWRIGHT_ENGINE_PATH"] = os.path.abspath(BUILD)
    os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "slicewright_engine"
else:
    os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"


class Layout:
    """A recording stand-in for ``UILayout``."""

    def __init__(self, log):
        self.log, self.alert, self.enabled, self.scale_y = log, False, True, 1.0

    def progress(self, factor=0.0, type="BAR", text=""):
        self.log.append(("progress", round(factor, 2), text))

    def column(self, align=False):
        return self

    row = box = column

    def label(self, text="", icon="NONE"):
        self.log.append(("label", text, icon))

    def prop(self, data, name, **kw):
        self.log.append(("prop", name))

    def operator(self, idname, text="", icon="NONE", emboss=True):
        self.log.append(("operator", idname, text, icon))
        return type("Op", (), {})()


class BakeTests(unittest.TestCase):
    def setUp(self):
        import slicewright
        from slicewright import prefs
        from slicewright.blender import bake_data, baking, library, paths, picker, registry, slicing, staleness, timers
        from slicewright.blender.preview import runtime
        from slicewright.core import bake_plan
        self.addon, self.baking, self.bake_data, self.bp = slicewright, baking, bake_data, bake_plan
        self.registry, self.slicing, self.st, self.timers, self.runtime = registry, slicing, staleness, timers, runtime
        if REAL:
            from slicewright.engine import adapter
            adapter.add_engine_paths()
            try:
                import slicewright_engine  # noqa: F401
            except ImportError:
                self.skipTest("slicewright_engine is not built")
        self.tmp = tempfile.mkdtemp(prefix="sw-bake-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        paths.set_override(os.path.join(self.tmp, "user"))
        self.addCleanup(paths.set_override, None)

        class Prefs(prefs._Defaults):
            cache_cap_mb = 2048
            auto_reslice = False
            last_export_dir = ""
        real_get = prefs.get
        prefs.get = lambda: Prefs
        self.addCleanup(setattr, prefs, "get", real_get)
        bl_common.fresh_scene()
        self.before = bl_common.handler_snapshot()
        self.addon.register()
        self.addCleanup(self.finish)
        self.assertTrue(registry.state.status.ok, registry.state.status.summary)
        self.scene = bpy.context.scene
        self.sw = self.scene.slicewright
        if REAL:
            library.request()
            self.drain()
            entry = [e for e in library.get().printers() if e.name == PRINTER]
            picker.select_printer(self.sw, entry[0])
        else:
            self.sw.printer_edits.printable_area = "0x0,256x0,256x256,0x256"
            self.sw.printer_edits.printable_height = 250.0
        self.cube = bl_common.add_cube("Cube", 20.0, (100.0, 100.0, 10.0))
        self.cube.select_set(True)
        bpy.ops.slicewright.plate_add()
        self.st_ = bpy.context.window_manager.slicewright_bake

    def finish(self):
        if self.slicing.is_live():
            self.slicing.runner().cancel(discard=True)
            self.pump(expect=None)
        self.baking._abort()
        self.drain()
        self.addon.unregister()
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    # -- helpers ------------------------------------------------------------------------------------------

    def pump(self, timeout=60.0, expect="done"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.slicing.poll_job() is None:
                break
            time.sleep(0.002)
        else:
            self.fail("the slice never finished")
        if expect is not None:
            self.assertEqual(self.sw.job_state, expect, self.sw.job_message)

    def drain(self):
        runner = self.timers.runner
        for _ in range(100_000):
            for task in runner.pending():
                if task.name.startswith("preview:"):
                    runner.cancel(task)
            if runner.idle:
                return
            runner.tick()
        self.fail("the tick runner never went idle")

    def slice(self):
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        self.pump()
        self.drain()

    def run_bake(self, max_ticks=100_000):
        runner = self.timers.runner
        ticks = 0
        while self.baking.is_live():
            for task in runner.pending():
                if task.name.startswith("preview:"):
                    runner.cancel(task)
            runner.tick()
            ticks += 1
            self.assertLess(ticks, max_ticks, "the bake never finished")
        return ticks

    def bake(self, **flags):
        self.assertEqual(bpy.ops.slicewright.bake(**flags), {"FINISHED"})
        return self.run_bake()

    def baked(self, kind=None):
        return [o for o in bpy.data.objects if self.bake_data.is_ours(o) and (kind is None or o[self.bp.TAG] == kind)]

    def obj(self, source="Cube", kind="CURVES"):
        found = [o for o in self.baked(kind) if o[self.bp.TAG_SOURCE] == source]
        self.assertEqual(len(found), 1, f"{source} {kind}: {[o.name for o in self.baked()]}")
        return found[0]

    def groups(self):
        """How many objects a default bake makes: one per source (the cube, and the skirt or brim as 'Plate extras')."""
        return len(self.baking.make_plan(self.scene, self.st_).groups)

    def result(self):
        return self.runtime.get(self.scene).result

    # -- availability ------------------------------------------------------------------------------------

    def test_bake_is_greyed_with_a_reason_until_there_is_a_current_slice(self):
        self.assertFalse(bpy.ops.slicewright.bake.poll())
        self.assertEqual(self.baking.availability(self.scene), (False, "Slice the plate to bake it"))
        with self.assertRaises(RuntimeError):
            bpy.ops.slicewright.bake()
        log = []
        from slicewright.blender.ui import bake_panel
        bake_panel.draw_bake(Layout(log), bpy.context)
        self.assertIn(("label", "Slice the plate to bake it", "INFO"), log)
        self.assertEqual(self.baked(), [])
        self.assertEqual(bpy.ops.slicewright.slice(), {"FINISHED"})
        if self.slicing.is_live():
            self.assertEqual(self.baking.availability(self.scene), (False, "Wait for the slice to finish"))
        self.pump()
        self.drain()
        self.assertEqual(self.baking.availability(self.scene), (True, ""))
        self.sw.stale = True
        self.assertEqual(self.baking.availability(self.scene), (False, "Out of date: slice again, then bake"))
        self.assertFalse(bpy.ops.slicewright.bake.poll())
        self.sw.stale = False

    # -- curves ------------------------------------------------------------------------------------------

    def test_bake_to_curves_makes_plain_curves_with_attributes_and_materials(self):
        self.slice()
        res = self.result()
        self.bake()
        self.assertEqual(sorted(o.name for o in self.baked("CURVES"))[0], "Cube Bake (curves)")
        ob = self.obj()
        self.assertEqual(ob.type, "CURVES")
        coll = bpy.data.collections.get("Slicewright Bake")
        self.assertIsNotNone(coll)
        self.assertIn(ob, list(coll.objects))
        self.assertIn(coll, list(self.scene.collection.children))
        curves = ob.data
        from slicewright.core import bake
        types = bake.MoveTypes.from_enums(self.registry.state.status.module.enums())
        sel = bake.select(res.moves, bake.Filter(0, len(res.moves["type"]) - 1), types)
        groups = dict(bake.split_groups(res.moves, sel))
        mine = groups[[i for i, n in enumerate(res.objects) if n == "Cube"][0]]
        self.assertEqual((len(curves.curves), len(curves.points)), (mine.n_paths, mine.n_points))
        for name in ("radius", "feature_type", "layer", "width", "height", "speed", "flow", "filament"):
            self.assertIn(name, curves.attributes.keys(), name)
        self.assertEqual(curves.attributes["material_index"].domain, "CURVE")
        types_attr = np.empty(len(curves.curves), np.int8)
        curves.attributes["curve_type"].data.foreach_get("value", types_attr)
        self.assertTrue((types_attr == 1).all())                                 # POLY
        pos = np.empty(len(curves.points) * 3, np.float32)
        curves.attributes["position"].data.foreach_get("vector", pos)
        pos = pos.reshape(-1, 3)
        self.assertGreater(pos[:, 2].min(), -0.01)
        self.assertTrue(80.0 < pos[:, 0].min() and pos[:, 0].max() < 120.0)    # a 20 mm cube at x=100, 1 BU = 1 mm
        layer = np.empty(len(curves.points), np.int32)
        curves.attributes["layer"].data.foreach_get("value", layer)
        self.assertEqual(int(layer.max()) + 1, len(res.layers["z"]))
        radius = np.empty(len(curves.points), np.float32)
        curves.attributes["radius"].data.foreach_get("value", radius)
        self.assertTrue(((radius > 0.05) & (radius < 1.0)).all())
        self.assertGreater(len(curves.materials), 0)
        self.assertEqual(ob[self.bp.TAG], "CURVES")
        self.assertTrue(set(dict(ob[self.bp.TAG_FEATURES]).values()) <= {"Travel", *(v[0] for v in __import__("slicewright.core.preview_palette", fromlist=["x"]).ROLE_STYLE.values())})
        self.assertTrue(dict(ob[self.bp.TAG_FEATURES]))
        msg = self.baking.message(self.scene)
        self.assertEqual(msg[0], "INFO")
        self.assertIn(f"Baked {self.groups()} object", msg[1])

    def test_curve_geometry_follows_the_scene_unit_scale(self):
        self.slice()
        self.bake()
        mm = self.obj()
        a = np.empty(len(mm.data.points) * 3, np.float32)
        mm.data.attributes["position"].data.foreach_get("vector", a)
        self.scene.unit_settings.scale_length = 1.0            # 1 BU = 1 m: the same bake is a thousand times smaller
        self.sw.stale = False
        self.bake(replace=True)
        m = self.obj()
        b = np.empty(len(m.data.points) * 3, np.float32)
        m.data.attributes["position"].data.foreach_get("vector", b)
        np.testing.assert_allclose(b * 1000.0, a, rtol=1e-4, atol=1e-3)

    def test_filters_choose_layers_features_and_travel(self):
        self.slice()
        res = self.result()
        st = self.st_
        st.use_preview = False
        st.layer_lo, st.layer_hi = 1, 2
        self.bake()
        ob = self.obj()
        layer = np.empty(len(ob.data.points), np.int32)
        ob.data.attributes["layer"].data.foreach_get("value", layer)
        self.assertEqual(sorted(set(layer.tolist())), [1, 2])
        ft = np.empty(len(ob.data.points), np.int32)
        ob.data.attributes["feature_type"].data.foreach_get("value", ft)
        self.assertNotIn(32, ft.tolist())                                         # travel is off by default
        st.travel = True
        self.bake(replace=True)
        found = set()
        for ob in self.baked("CURVES"):                     # travel has no object in the engine's table: the extras
            ft = np.empty(len(ob.data.points), np.int32)
            ob.data.attributes["feature_type"].data.foreach_get("value", ft)
            found |= set(ft.tolist())
        self.assertIn(32, found)
        # one feature type only
        first_role = int(np.bincount(res.moves["role"][res.moves["type"] == self.registry.state.status.module.enums()["move_type"]["Extrude"]]).argmax())
        st.travel = False
        st.layer_lo, st.layer_hi = 0, 100000
        st.roles = [i == first_role for i in range(32)]
        self.bake(replace=True)
        ob = self.obj()
        ft = np.empty(len(ob.data.points), np.int32)
        ob.data.attributes["feature_type"].data.foreach_get("value", ft)
        self.assertEqual(set(ft.tolist()), {first_role})

    def test_the_preview_range_is_what_gets_baked(self):
        self.slice()
        from slicewright.blender.preview import props as pp
        p = pp.of(self.scene)
        p.layer_lo = p.layer_hi = 0
        self.bake()
        ob = self.obj()
        layer = np.empty(len(ob.data.points), np.int32)
        ob.data.attributes["layer"].data.foreach_get("value", layer)
        self.assertEqual(set(layer.tolist()), {0})
        text, level = self.baking.panel_estimate(self.scene, self.st_)
        self.assertEqual(level, "ok")

    def test_nothing_selected_is_reported_not_baked(self):
        self.slice()
        self.st_.use_preview = False
        self.st_.roles = [False] * 32
        self.assertEqual(bpy.ops.slicewright.bake(), {"CANCELLED"})
        self.assertEqual(self.baking.message(self.scene)[0], "ERROR")
        self.assertEqual(self.baked(), [])

    # -- mesh --------------------------------------------------------------------------------------------

    def test_bake_to_mesh_splits_into_parts_with_the_estimated_vertex_count(self):
        self.slice()
        st = self.st_
        st.kind = "MESH"
        st.part_segments = self.bp.MIN_PART_SEGMENTS
        plan = self.baking.make_plan(self.scene, st)
        est = plan.estimate
        self.assertEqual(est.vertices, 12 * est.segments)
        self.bake()
        objs = sorted(self.baked("MESH"), key=lambda o: o.name)
        expected_parts = sum(-(-s.n_segments // self.bp.MIN_PART_SEGMENTS) for _, s in plan.groups)
        cube = [o for o in objs if o[self.bp.TAG_SOURCE] == "Cube"]
        self.assertEqual(cube[0].name, "Cube Bake (mesh 01)" if len(cube) > 1 else "Cube Bake (mesh)")
        self.assertEqual(len(objs), expected_parts)
        self.assertEqual(sum(len(o.data.vertices) for o in objs), est.vertices)
        self.assertEqual(sum(len(o.data.polygons) for o in objs), est.faces)
        for o in objs:
            me = o.data
            for name in ("feature_type", "layer", "width", "height", "speed", "flow", "filament", "material_index"):
                self.assertEqual(me.attributes[name].domain, "FACE", name)
            self.assertFalse(me.validate(verbose=False), "the mesh arrays are consistent")
            self.assertGreater(len(me.edges), 0)
        me = cube[0].data
        xs = np.empty(len(me.vertices) * 3, np.float32)
        me.attributes["position"].data.foreach_get("vector", xs)
        self.assertTrue(80.0 < xs[0::3].min() and xs[0::3].max() < 120.0)
        self.assertGreater(len(me.materials), 0)

    def test_every_step_of_a_mesh_bake_is_small(self):
        self.slice()
        self.st_.kind = "MESH"
        self.st_.part_segments = self.bp.MIN_PART_SEGMENTS
        self.assertEqual(bpy.ops.slicewright.bake(), {"FINISHED"})
        worst, runner = 0.0, self.timers.runner
        while self.baking.is_live():
            t = time.perf_counter()
            runner.tick()
            worst = max(worst, time.perf_counter() - t)
        self.assertLess(worst, 0.2, "one tick is bounded (the tiny fake plate; real sizes are measured separately)")

    # -- replacing, asking, keeping ----------------------------------------------------------------------

    def test_rebake_needs_a_yes_and_only_replaces_objects_the_addon_tagged(self):
        self.slice()
        self.bake()
        first = self.obj()
        n_baked = len(self.baked("CURVES"))
        mine = bpy.data.objects.new("Cube Bake (curves)", None)                      # a user's object with the same name
        self.scene.collection.objects.link(mine)
        other = bpy.data.objects.new("Other", None)
        self.scene.collection.objects.link(other)
        self.assertEqual(bpy.ops.slicewright.bake(), {"CANCELLED"})                  # no replace=True: nothing is deleted
        self.assertIn(first.name, bpy.data.objects)
        self.assertEqual(len(self.baked("CURVES")), n_baked)
        old_data = first.data.name
        self.assertEqual(bpy.ops.slicewright.bake(replace=True), {"FINISHED"})
        self.run_bake()
        self.assertEqual(len(self.baked("CURVES")), n_baked)
        self.assertIn(mine.name, bpy.data.objects)                                    # the user's lookalike is untouched
        self.assertIn("Other", bpy.data.objects)
        self.assertNotIn(old_data, bpy.data.hair_curves.keys())                       # no orphaned data left
        self.assertEqual(len(bpy.data.collections.get("Slicewright Bake").objects), n_baked)

    def test_the_confirmation_popup_walks_and_keep_releases_the_objects(self):
        self.slice()
        self.bake()
        n = self.groups()
        first_names = {o.name for o in self.baked("CURVES")}
        plan = self.baking.make_plan(self.scene, self.st_)
        text, button = self.baking.confirm_text(plan)
        self.assertIn("will be replaced", text)
        self.assertEqual(button, "Replace and bake")
        # Background Blender shows no popup: invoke_confirm cancels, so nothing is replaced or started
        self.assertEqual(bpy.ops.slicewright.bake("INVOKE_DEFAULT"), {"CANCELLED"})
        self.assertFalse(self.baking.is_live())
        self.assertEqual({o.name for o in self.baked("CURVES")}, first_names)
        self.assertEqual(bpy.ops.slicewright.bake_keep(), {"FINISHED"})
        self.assertEqual(self.baked(), [])
        self.assertEqual(len(bpy.data.collections.get("Slicewright Bake").objects), n)
        self.assertEqual(bpy.ops.slicewright.bake(), {"FINISHED"})                    # nothing tagged: no replace needed
        self.run_bake()
        self.assertEqual(len(self.baked("CURVES")), n)
        self.assertEqual(len(bpy.data.collections.get("Slicewright Bake").objects), 2 * n)
        self.assertEqual(len(first_names), n)

    def test_a_large_bake_asks_and_a_huge_one_is_refused(self):
        self.slice()
        old = self.bp.CONFIRM_BYTES, self.bp.REFUSE_BYTES
        self.addCleanup(lambda: (setattr(self.bp, "CONFIRM_BYTES", old[0]), setattr(self.bp, "REFUSE_BYTES", old[1])))
        self.bp.CONFIRM_BYTES = 1000
        self.assertEqual(bpy.ops.slicewright.bake(), {"CANCELLED"})
        self.assertEqual(self.baked(), [])
        self.assertEqual(bpy.ops.slicewright.bake(allow_large=True), {"FINISHED"})
        self.run_bake()
        self.assertEqual(len(self.baked("CURVES")), self.groups())
        plan = self.baking.make_plan(self.scene, self.st_)
        self.assertIsNotNone(self.baking.confirm_text(plan))
        self.bp.REFUSE_BYTES = 2000
        self.assertEqual(bpy.ops.slicewright.bake(allow_large=True, replace=True), {"CANCELLED"})
        self.assertEqual(self.baking.message(self.scene)[0], "ERROR")
        self.assertIn("too large", self.baking.message(self.scene)[1])
        self.assertEqual(len(self.baked("CURVES")), self.groups())                    # the old bake survives

    def test_cancel_leaves_the_scene_and_the_data_exactly_as_it_was(self):
        self.slice()
        self.st_.kind = "MESH"
        self.st_.part_segments = self.bp.MIN_PART_SEGMENTS
        before = (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.collections))
        self.assertEqual(bpy.ops.slicewright.bake(), {"FINISHED"})
        self.assertTrue(self.baking.is_live())
        self.assertFalse(bpy.ops.slicewright.bake.poll())
        self.assertEqual(self.baking.availability(self.scene)[1], "Wait for the bake to finish")
        runner = self.timers.runner
        runner.budget_s = 1e-9                                    # one step a tick, like a slow machine
        self.addCleanup(setattr, runner, "budget_s", 0.020)
        runner.tick()
        self.assertTrue(self.baking.is_live(), "a bake is more than one step")
        log = []
        from slicewright.blender.ui import bake_panel
        bake_panel.draw_bake(Layout(log), bpy.context)
        self.assertTrue(any(e[0] == "progress" for e in log))
        self.assertEqual(bpy.ops.slicewright.bake_cancel(), {"FINISHED"})
        self.assertFalse(self.baking.is_live())
        self.assertEqual(self.baking.message(self.scene), ("INFO", "Bake cancelled"))
        self.assertEqual(before, (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.collections)))

    def test_a_cancelled_rebake_keeps_the_previous_bake(self):
        self.slice()
        self.bake()
        self.assertEqual(bpy.ops.slicewright.bake(replace=True), {"FINISHED"})
        self.timers.runner.tick()
        self.baking.cancel()
        self.assertEqual(len(self.baked("CURVES")), self.groups())

    # -- plain data and the plate ------------------------------------------------------------------------

    def test_the_bake_is_excluded_from_the_plate_and_does_not_make_the_slice_stale(self):
        self.slice()
        self.bake()
        from slicewright.blender import plate_collection
        coll = bpy.data.collections["Slicewright Bake"]
        plate = plate_collection.plate_collection(self.scene)
        plate.children.link(coll)                                  # even inside the plate it is not sliced
        names = {o.name for o in plate_collection.plate_objects(self.scene)}
        self.assertEqual(names, {"Cube"})
        plate.children.unlink(coll)
        bpy.context.view_layer.update()
        self.assertFalse(self.sw.stale)

    def test_a_saved_bake_opens_without_the_addon(self):
        self.slice()
        self.bake()
        path = os.path.join(self.tmp, "baked.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        self.addon.unregister()
        bpy.ops.wm.open_mainfile(filepath=path)
        ob = bpy.data.objects["Cube Bake (curves)"]
        self.assertEqual(ob.type, "CURVES")
        self.assertGreater(len(ob.data.points), 0)
        self.assertEqual(ob.modifiers.__len__(), 0)
        self.assertFalse(hasattr(bpy.types.Scene, "slicewright"))
        self.addon.register()                                       # the finish hook unregisters again

    def test_a_file_load_cancels_a_running_bake(self):
        self.slice()
        self.assertEqual(bpy.ops.slicewright.bake(), {"FINISHED"})
        self.registry.on_load_pre()
        self.assertFalse(self.baking.is_live())
        self.assertEqual(self.timers.runner.pending(), [])

    # -- a render ----------------------------------------------------------------------------------------

    def test_cycles_renders_the_baked_curves(self):
        self.slice()
        self.bake()
        scene = self.scene
        try:
            scene.render.engine = "CYCLES"
        except TypeError:
            self.skipTest("Cycles is not available")
        scene.cycles.samples = 4
        scene.cycles.device = "CPU"
        scene.render.resolution_x = scene.render.resolution_y = 64
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
        scene.collection.objects.link(cam)
        cam.location = (100.0, 100.0, 300.0)
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = 60.0
        cam.data.clip_end = 2000.0
        scene.camera = cam
        out = os.path.join(self.tmp, "render.png")
        scene.render.filepath = out
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(out)
        px = np.array(img.pixels[:]).reshape(-1, 4)
        self.assertGreater(np.count_nonzero(px[:, 3] > 0.5), 100)
        self.assertGreater(np.ptp(px[:, 0]), 0.0)
        bpy.data.images.remove(img)


if __name__ == "__main__":
    bl_common.run("__main__")
