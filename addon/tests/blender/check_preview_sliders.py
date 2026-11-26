# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the Preview sliders write the preview settings (and read them back), appear only where they
belong, and leave no draw handler, timer or modal bookkeeping behind (03 section 7.4a). Headless: no modal operator can
run here, so the mouse handling is checked by ``tools/ui_shots.py`` in a real window; this covers everything around it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402

from check_mode import ModeBase  # noqa: E402


class Space:
    type = "VIEW_3D"


class Area:
    type = "VIEW_3D"
    spaces = [Space()]


class Window:
    def __init__(self, scene):
        self.scene = scene


class SliderTests(ModeBase):
    def setUp(self):
        super().setUp()
        from slicewright.blender.preview import props as pprops, slider_modal, sliders
        from slicewright.core import preview_nav as nav
        from slicewright.core import preview_slider as ps
        self.pprops, self.modal, self.sliders, self.nav, self.ps = pprops, slider_modal, sliders, nav, ps
        self.slice()
        self.p = pprops.of(self.scene)
        self.rt = self.runtime.get(self.scene)

    def view(self):
        n = self.nav.layer_count(self.rt.layers)
        scrub = self.nav.clamp(self.p.scrub(), self.rt.layers)
        g = self.ps.make_geometry(1200, 800, 1.0)
        return self.sliders.View(g, scrub, n, self.nav.layer_len(self.rt.layers, scrub.hi) - 1, self.p, self.rt)

    def test_the_option_defaults_to_on_and_the_thumbs_do_not_push(self):
        self.assertTrue(self.p.show_sliders)
        self.assertFalse(self.p.slider_follow)

    def test_the_sliders_belong_only_over_a_3d_view_in_preview_with_a_result_and_the_option_on(self):
        self.assertEqual(self.sw.mode, "PREVIEW")
        self.assertTrue(self.sliders.shown(self.scene, Space()))
        self.assertTrue(self.modal._wanted(Window(self.scene), Area()))
        other = Space()
        other.type = "PROPERTIES"
        self.assertFalse(self.sliders.shown(self.scene, other))
        self.assertFalse(self.sliders.shown(self.scene, None))
        self.p.show_sliders = False
        self.assertFalse(self.modal._wanted(Window(self.scene), Area()))
        self.p.show_sliders = True
        self.sw.mode = "PREPARE"
        self.assertFalse(self.modal._wanted(Window(self.scene), Area()))      # the modal ends itself at its next event
        self.sw.mode = "PREVIEW"
        self.assertTrue(self.modal._wanted(Window(self.scene), Area()))
        self.runtime.put(self.scene, None)
        self.assertFalse(self.modal._wanted(Window(self.scene), Area()))      # no preview, no sliders

    def test_a_slider_write_lands_in_the_panels_properties_and_comes_back(self):
        view = self.view()
        n = view.n
        self.assertGreaterEqual(n, 3)
        self.assertTrue(self.sliders.write(view, self.nav.Scrub(1, n - 2, None)))
        self.assertEqual((self.p.layer_lo, self.p.layer_hi, self.p.scrub_moves), (1, n - 2, False))
        # The panel's own fields move the sliders: the next read sees them.
        self.p.layer_hi = 2
        self.assertEqual(self.view().scrub, self.nav.Scrub(1, 2, None))
        # An in-layer position turns scrubbing on and writes move_pos.
        view = self.view()
        self.assertTrue(self.sliders.write(view, self.nav.Scrub(1, 2, 3)))
        self.assertEqual((self.p.scrub_moves, self.p.move_pos), (True, 3))
        self.assertFalse(self.sliders.write(self.view(), self.nav.Scrub(1, 2, 3)))     # unchanged: nothing written

    def test_a_slider_write_is_clamped_to_the_layer_table_and_keeps_lo_le_hi(self):
        view = self.view()
        self.sliders.write(view, self.nav.Scrub(500, 900, 99999))
        self.assertEqual((self.p.layer_lo, self.p.layer_hi), (view.n - 1, view.n - 1))
        self.assertLessEqual(self.p.layer_lo, self.p.layer_hi)
        self.sliders.write(self.view(), self.nav.Scrub(-5, -9, None))
        self.assertEqual((self.p.layer_lo, self.p.layer_hi), (0, 0))

    def test_dragging_through_the_pure_rules_drives_the_properties(self):
        ps, nav = self.ps, self.nav
        view = self.view()
        g, n = view.geometry, view.n
        self.sliders.write(view, nav.full(self.rt.layers))
        view = self.view()
        top_y = ps.layer_to_y(g, view.scrub.hi, n)
        hit = ps.hit_test(g, g.track_x, top_y, view.scrub, n, view.move_last)
        self.assertEqual(hit, ps.HI)
        drag, scrub = ps.begin_drag(g, hit, g.track_x, top_y, view.scrub, n, view.move_last)
        self.sliders.write(view, scrub)
        target = n // 2
        self.sliders.write(self.view(), ps.drag_to(g, drag, g.track_x, ps.layer_to_y(g, target, n), self.view().scrub, n,
                                                   view.move_last))
        self.assertEqual(self.p.layer_hi, target)
        # The move slider scrubs the top layer.
        v = self.view()
        mhit = ps.hit_test(g, (g.move_x0 + g.move_x1) / 2, g.move_y, v.scrub, n, v.move_last)
        self.assertEqual(mhit, ps.MOVE_TRACK)
        d, s = ps.begin_drag(g, mhit, (g.move_x0 + g.move_x1) / 2, g.move_y, v.scrub, n, v.move_last)
        self.sliders.write(v, s)
        self.assertTrue(self.p.scrub_moves)
        self.assertEqual(self.p.move_pos, s.pos)

    def test_registration_state_is_added_and_fully_removed(self):
        self.assertIsNotNone(self.sliders._handle)
        self.assertTrue(bpy.app.timers.is_registered(self.modal.ensure))
        self.assertIn(self.modal.reset, self.registry.state.load_pre_hooks)
        self.assertTrue(hasattr(bpy.types, "SLICEWRIGHT_OT_layer_slider"))
        generation = self.modal._generation
        self.modal._running.add((1, 2))                       # a modal that was running when the add-on goes away
        self.sliders.ui.drag = self.ps.Drag(self.ps.HI, self.nav.Scrub())
        self.addon.unregister()
        self.assertIsNone(self.sliders._handle)
        self.assertFalse(bpy.app.timers.is_registered(self.modal.ensure))
        self.assertNotIn(self.modal.reset, self.registry.state.load_pre_hooks)
        self.assertEqual(self.modal.running(), set())
        self.assertIsNone(self.sliders.ui.drag)
        self.assertGreater(self.modal._generation, generation)    # a modal that outlives this ends at its next event
        self.assertFalse(hasattr(bpy.types, "SLICEWRIGHT_OT_layer_slider"))
        self.addon.register()                                  # finish() unregisters again

    def test_load_pre_forgets_running_modals(self):
        self.modal._running.add((1, 2))
        self.registry.on_load_pre()
        self.assertEqual(self.modal.running(), set())

    def test_ensure_does_nothing_without_windows_and_returns_its_interval(self):
        self.assertEqual(self.modal.ensure(), self.modal.ENSURE_INTERVAL)
        self.assertEqual(self.modal.running(), set())


bl_common.run(__name__)
