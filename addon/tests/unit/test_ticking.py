# SPDX-License-Identifier: GPL-3.0-or-later
"""core/ticking.py: budgeted round-robin execution of generator tasks."""
from __future__ import annotations

import logging

import pytest
from slicewright.core.ticking import CANCELLED, DONE, FAILED, PENDING, TickRunner, chunked


class FakeClock:
    """Time advances only when a step 'works', so budgets are deterministic."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def work(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return FakeClock()


def counting(clock, n, cost, log=None, tag=""):
    for i in range(n):
        clock.work(cost)
        if log is not None:
            log.append((tag, i))
        yield i / n
    return f"{tag}-done"


def test_a_task_runs_to_completion_and_reports_its_result(clock):
    runner = TickRunner(budget_s=1.0, clock=clock)
    done = []
    task = runner.submit("a", counting(clock, 3, 0.001, tag="a"), on_done=done.append)
    assert task.state == PENDING and not runner.idle
    assert runner.tick() is None           # everything fit in one budget, so nothing is left
    assert (task.state, task.result, done) == (DONE, "a-done", ["a-done"])
    assert task.progress == 1.0 and task.steps == 3 and runner.idle


def test_the_budget_limits_steps_per_tick(clock):
    runner = TickRunner(budget_s=0.025, clock=clock, interval_s=0.0)
    runner.submit("a", counting(clock, 20, 0.010))
    assert runner.tick() == 0.0             # more work remains: call again immediately
    assert runner.steps == 3                # 10 ms steps: 3 reach the 25 ms budget (30 ms)
    runner.tick()
    assert runner.steps == 6


def test_at_least_one_step_runs_even_if_it_exceeds_the_budget(clock):
    runner = TickRunner(budget_s=0.001, clock=clock)
    runner.submit("slow", counting(clock, 3, 1.0))
    runner.tick()
    assert runner.steps == 1
    assert runner.run_until_idle() == 3 and runner.idle   # two more yields, then the return


def test_tasks_are_round_robin_so_none_starves(clock):
    order = []
    runner = TickRunner(budget_s=1.0, clock=clock)
    runner.submit("a", counting(clock, 3, 0.001, order, "a"))
    runner.submit("b", counting(clock, 3, 0.001, order, "b"))
    runner.tick()
    assert [t for t, _ in order] == ["a", "b", "a", "b", "a", "b"]


def test_a_long_task_does_not_block_a_short_one(clock):
    runner = TickRunner(budget_s=0.02, clock=clock)
    long = runner.submit("long", counting(clock, 100, 0.01))
    short = runner.submit("short", counting(clock, 2, 0.01))
    for _ in range(3):
        runner.tick()
    assert short.state == DONE and long.state == PENDING


def test_progress_and_messages_from_yields(clock):
    def work():
        for value in (0.25, (0.5, "halfway"), None, 7.0, "ignored"):   # 7.0 is clamped
            clock.work(0.01)
            yield value
    runner = TickRunner(budget_s=0.0001, clock=clock)
    task = runner.submit("p", work)
    seen = []
    while not task.finished:
        runner.tick()
        seen.append((task.progress, task.message))
    assert seen[0][0] == 0.25
    assert (0.5, "halfway") in seen
    assert max(p for p, _ in seen) == 1.0


def test_exceptions_fail_the_task_not_the_tick(clock, caplog):
    def bad():
        yield 0.1
        raise RuntimeError("boom")
    errors = []
    runner = TickRunner(clock=clock)
    ok = runner.submit("ok", counting(clock, 2, 0.001))
    bad_task = runner.submit("bad", bad, on_error=errors.append)
    with caplog.at_level(logging.ERROR, logger="slicewright.ticking"):
        runner.run_until_idle()
    assert bad_task.state == FAILED and isinstance(bad_task.error, RuntimeError)
    assert [str(e) for e in errors] == ["boom"]
    assert ok.state == DONE
    assert "boom" in caplog.text


def test_a_failing_callback_does_not_break_the_tick(clock, caplog):
    def explode(_):
        raise ValueError("callback")
    runner = TickRunner(clock=clock)
    first = runner.submit("a", counting(clock, 1, 0.001), on_done=explode)
    second = runner.submit("b", counting(clock, 1, 0.001))
    with caplog.at_level(logging.ERROR, logger="slicewright.ticking"):
        runner.run_until_idle()
    assert first.state == DONE and second.state == DONE
    assert "callback" in caplog.text


def test_cancel_closes_the_generator_and_calls_on_cancel(clock):
    cleaned, cancelled = [], []

    def work():
        try:
            for i in range(10):
                clock.work(0.01)
                yield i / 10
        finally:
            cleaned.append(True)
    runner = TickRunner(budget_s=0.0001, clock=clock)
    task = runner.submit("w", work, on_cancel=lambda: cancelled.append(True))
    runner.tick()
    assert runner.cancel("w") is True
    assert (task.state, cleaned, cancelled) == (CANCELLED, [True], [True])
    assert runner.idle and runner.get("w") is None
    assert runner.cancel("w") is False       # nothing left to cancel
    assert runner.tick() is None


def test_a_task_may_cancel_itself_and_others_from_its_steps(clock):
    runner = TickRunner(budget_s=1.0, clock=clock)
    victim = runner.submit("victim", counting(clock, 10, 0.001))

    def killer():
        yield
        runner.cancel("victim")
        runner.cancel("killer")
        yield
        yield
    k = runner.submit("killer", killer)
    runner.run_until_idle()
    assert victim.state == CANCELLED and k.state == CANCELLED


def test_cancel_all(clock):
    runner = TickRunner(clock=clock)
    tasks = [runner.submit(str(i), counting(clock, 5, 0.001)) for i in range(3)]
    runner.cancel_all()
    assert runner.idle and all(t.state == CANCELLED for t in tasks)


def test_duplicate_names_are_rejected_or_replaced(clock):
    runner = TickRunner(clock=clock)
    first = runner.submit("x", counting(clock, 5, 0.001))
    with pytest.raises(ValueError):
        runner.submit("x", counting(clock, 5, 0.001))
    second = runner.submit("x", counting(clock, 5, 0.001), replace=True)
    assert first.state == CANCELLED and runner.get("x") is second
    runner.run_until_idle()
    assert second.state == DONE
    runner.submit("x", counting(clock, 1, 0.001))   # the name is free again once finished


def test_wake_is_called_only_when_work_arrives_at_an_idle_runner(clock):
    wakes = []
    runner = TickRunner(clock=clock, wake=lambda: wakes.append(1))
    runner.submit("a", counting(clock, 5, 0.001))
    runner.submit("b", counting(clock, 5, 0.001))
    assert len(wakes) == 1
    runner.run_until_idle()
    runner.submit("c", counting(clock, 1, 0.001))
    assert len(wakes) == 2


def test_a_callback_can_submit_follow_up_work(clock):
    runner = TickRunner(budget_s=1.0, clock=clock)
    log = []
    runner.submit("first", counting(clock, 1, 0.001),
                  on_done=lambda r: runner.submit("second", counting(clock, 1, 0.001),
                                                  on_done=log.append))
    runner.run_until_idle()
    assert log == ["-done"]


def test_a_task_can_be_a_plain_iterable(clock):
    runner = TickRunner(clock=clock)
    task = runner.submit("list", [0.5, 1.0])
    runner.run_until_idle()
    assert task.state == DONE and task.steps == 2


def test_run_until_idle_gives_up_if_work_never_ends(clock):
    def forever():
        while True:
            clock.work(0.01)
            yield
    runner = TickRunner(clock=clock)
    runner.submit("f", forever)
    with pytest.raises(RuntimeError):
        runner.run_until_idle(max_ticks=5)
    runner.cancel_all()


def test_budget_must_be_positive():
    with pytest.raises(ValueError):
        TickRunner(budget_s=0)


def test_chunked_helper_reports_progress_and_returns_all_results(clock):
    runner = TickRunner(budget_s=0.0001, clock=clock)
    seen = []
    task = runner.submit("map", chunked(list(range(10)), 4, lambda x: x * x), on_done=seen.append)
    fractions = []
    while not task.finished:
        runner.tick()
        fractions.append(task.progress)
    assert seen == [[x * x for x in range(10)]]
    assert fractions == [0.4, 0.8, 1.0, 1.0] or fractions[-1] == 1.0
    assert task.steps == 3                      # ceil(10 / 4) chunks


def test_chunked_edge_cases():
    assert list(chunked([], 5, str)) == []
    with pytest.raises(ValueError):
        next(chunked([1], 0, str))


def test_it_works_with_the_real_clock():
    runner = TickRunner(budget_s=0.005)
    total = []
    runner.submit("t", chunked(list(range(2000)), 10, lambda x: total.append(x)))
    ticks = runner.run_until_idle()
    assert len(total) == 2000 and ticks >= 1
