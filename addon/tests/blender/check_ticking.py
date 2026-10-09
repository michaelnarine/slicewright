# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: the tick timer glue (blender/timers.py) driving core/ticking.py.

Blender's background mode never enters its main loop after the script, so ``bpy.app.timers``
callbacks do not fire here (verified on 5.1.2). The timer function is therefore driven
directly, exactly as Blender would call it; what is tested is the registration, re-arming
and cleanup around it.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402

import bpy  # noqa: E402


def drive(timers, max_calls=1000):
    """Call the timer function the way Blender would until it returns None."""
    for calls in range(1, max_calls + 1):
        if timers._tick() is None:
            return calls
    raise AssertionError("tick timer never went idle")


def work(n, log):
    for i in range(n):
        log.append(i)
        yield i / n
    return n


class TickTimerTests(unittest.TestCase):
    def setUp(self):
        os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
        import slicewright
        from slicewright.blender import timers
        self.addon, self.timers = slicewright, timers
        self.before = bl_common.handler_snapshot()
        self.addCleanup(self.finish)
        self.addon.register()

    def finish(self):
        self.addon.unregister()
        self.assertFalse(bpy.app.timers.is_registered(self.timers._tick))
        self.assertEqual(bl_common.handler_snapshot(), self.before)

    def test_submitting_work_arms_the_timer(self):
        self.assertFalse(bpy.app.timers.is_registered(self.timers._tick))
        log = []
        done = []
        self.timers.runner.submit("job", work(5, log), on_done=done.append)
        self.assertTrue(bpy.app.timers.is_registered(self.timers._tick))
        drive(self.timers)
        self.assertEqual((log, done), ([0, 1, 2, 3, 4], [5]))
        self.assertTrue(self.timers.runner.idle)

    def test_the_timer_function_returns_a_delay_while_work_remains_then_none(self):
        def slow():
            for _ in range(3):
                time.sleep(0.03)          # each step alone exceeds the 25 ms tick budget
                yield
        self.timers.runner.submit("slow", slow)
        self.assertEqual(self.timers._tick(), 0.0)
        self.assertEqual(self.timers._tick(), 0.0)
        self.assertEqual(self.timers._tick(), 0.0)    # the generator's return is still to come
        self.assertIsNone(self.timers._tick())

    def test_a_stopped_timer_is_rearmed_by_new_work(self):
        self.timers.runner.submit("one", work(1, []))
        drive(self.timers)
        bpy.app.timers.unregister(self.timers._tick)      # what Blender does after a None return
        self.assertFalse(bpy.app.timers.is_registered(self.timers._tick))
        self.timers.runner.submit("two", work(1, []))
        self.assertTrue(bpy.app.timers.is_registered(self.timers._tick))

    def test_unregister_cancels_pending_tasks_and_removes_the_timer(self):
        cancelled = []
        task = self.timers.runner.submit("pending", work(100, []), on_cancel=lambda: cancelled.append(1))
        self.assertTrue(bpy.app.timers.is_registered(self.timers._tick))
        self.addon.unregister()
        self.assertEqual(cancelled, [1])
        self.assertEqual(task.state, "cancelled")
        self.assertTrue(self.timers.runner.idle)
        self.assertFalse(bpy.app.timers.is_registered(self.timers._tick))

    def test_work_submitted_after_unregister_does_not_arm_a_timer(self):
        self.addon.unregister()
        self.timers.runner.submit("late", work(2, []))
        self.assertFalse(bpy.app.timers.is_registered(self.timers._tick))
        self.timers.runner.cancel_all()

    def test_register_unregister_cycles_leave_no_timer(self):
        for _ in range(3):
            self.addon.unregister()
            self.addon.register()
            self.timers.runner.submit("job", work(2, []))
            self.addon.unregister()
            self.assertFalse(bpy.app.timers.is_registered(self.timers._tick))
        self.addon.register()

    def test_a_failing_task_does_not_break_the_timer_function(self):
        def bad():
            yield
            raise RuntimeError("expected in this test")
        errors = []
        self.timers.runner.submit("bad", bad, on_error=errors.append)
        self.timers.runner.submit("good", work(2, []))
        drive(self.timers)
        self.assertEqual(len(errors), 1)
        self.assertTrue(self.timers.runner.idle)


if __name__ == "__main__":
    bl_common.run("__main__")
