# SPDX-License-Identifier: GPL-3.0-or-later
"""Job state machine, Busy, cancellation, error mapping (04 sections 4.3-4.4, 7, 8, 9)."""
from __future__ import annotations

import gc

import numpy as np
import pytest
from contract_helpers import STATES, assert_issue, box, drive, new_job, sliced


@pytest.fixture
def sc(jobs_backend):
    return jobs_backend


def _failing_job(sc):
    """A job whose validation fails: face_extruder 5 with a single configured filament."""
    job = new_job(sc, objects=[])
    v, t = box()
    fe = np.zeros(len(t), np.uint8)
    fe[0] = 5
    job.add_object("painted", v, t, face_extruder=fe)
    return job


# --- happy path ------------------------------------------------------------------------

def test_start_then_poll_until_done(sc):
    job = new_job(sc)
    job.start()
    seen = drive(job)
    states = [s for s, _, _ in seen]
    assert set(states) <= set(STATES)
    assert states[0] in ("validating", "running", "done")
    assert states[-1] == "done"
    # states only move forward through validating -> running -> done
    order = {"validating": 0, "running": 1, "done": 2}
    ranks = [order[s] for s in states]
    assert ranks == sorted(ranks)
    percents = [p for _, p, _ in seen]
    assert percents == sorted(percents), "percent must be non-decreasing"
    assert all(0.0 <= p <= 100.0 for p in percents)
    assert all(isinstance(m, str) for _, _, m in seen)
    assert seen[-1] == ("done", 100.0, "")


def test_result_is_the_same_object_every_call(sc):
    job, result = sliced(sc)
    assert job.result() is result
    assert job.result(timeout=1.0) is result


def test_done_job_rejects_start_and_mutators_but_ignores_cancel(sc):
    job, _ = sliced(sc)
    v, t = box()
    with pytest.raises(sc.StateError):
        job.start()
    with pytest.raises(sc.StateError):
        job.add_object("late", v, t)
    job.cancel()
    assert job.poll()[0] == "done"


def test_start_on_a_non_idle_job_raises_state_error(sc):
    job = new_job(sc)
    job.start()
    with pytest.raises(sc.StateError):
        job.start()
    job.cancel()
    drive(job)


def test_result_waits_for_a_running_job(sc):
    job = new_job(sc)
    job.start()
    result = job.result()   # blocks (GIL released) until terminal
    assert job.poll()[0] == "done"
    assert result.objects == ["cube"]


def test_result_timeout_raises_timeout_error_while_running(sc):
    job = new_job(sc)
    job.start()
    with pytest.raises(TimeoutError):
        job.result(timeout=0.0)
    drive(job)


def test_result_on_an_idle_job_is_a_state_error(sc):
    with pytest.raises(sc.StateError) as err:
        new_job(sc).result()
    assert err.value.state == "idle"


def test_poll_in_idle_is_idle_zero_and_empty(sc):
    assert new_job(sc).poll() == ("idle", 0.0, "")
    assert sc.SliceJob().poll() == ("idle", 0.0, "")


def test_cancel_on_an_idle_job_is_a_no_op(sc):
    job = new_job(sc)
    job.cancel()
    assert job.poll()[0] == "idle"
    job.start()
    drive(job)


# --- Busy ------------------------------------------------------------------------------

def test_second_job_gets_busy_and_stays_idle(sc):
    first, second = new_job(sc), new_job(sc)
    first.start()
    with pytest.raises(sc.Busy):
        second.start()
    assert second.poll()[0] == "idle"
    drive(first)
    second.start()          # the lock is free again
    assert drive(second)[-1][0] == "done"


def test_arrange_is_busy_while_another_job_runs(sc):
    running, other = new_job(sc), new_job(sc)
    running.start()
    with pytest.raises(sc.Busy):
        other.arrange()
    drive(running)


def test_dropping_a_running_job_releases_the_engine_lock(sc):
    job = new_job(sc)
    job.start()
    del job
    gc.collect()
    follow_up = new_job(sc)
    follow_up.start()       # must not raise Busy
    assert drive(follow_up)[-1][0] == "done"


# --- cancellation ----------------------------------------------------------------------

def test_cancel_leads_to_cancelled_or_a_late_finish(sc):
    job = new_job(sc)
    job.start()
    job.cancel()
    state, _, _ = job.poll()
    assert state in ("cancelling", "cancelled", "done")
    seen = drive(job)
    final = seen[-1][0]
    assert final in ("cancelled", "done")
    if final == "cancelled":
        for _ in range(2):   # the stored exception is raised every time
            with pytest.raises(sc.Cancelled):
                job.result()
        assert job.poll()[0] == "cancelled"
    else:
        assert job.result() is job.result()


def test_cancelled_job_releases_the_lock(sc):
    job = new_job(sc)
    job.start()
    job.cancel()
    drive(job)
    nxt = new_job(sc)
    nxt.start()
    assert drive(nxt)[-1][0] == "done"


def test_cancel_is_a_no_op_after_failure(sc):
    job = _failing_job(sc)
    job.start()
    drive(job)
    job.cancel()
    assert job.poll()[0] == "failed"


# --- validation failures ---------------------------------------------------------------

def test_failed_validation_reports_through_result(sc):
    job = _failing_job(sc)
    job.start()
    seen = drive(job)
    assert seen[-1][0] == "failed"
    for _ in range(2):   # raised every call
        with pytest.raises(sc.ValidationError) as err:
            job.result()
    exc = err.value
    assert exc.issues and all(assert_issue(i) is None for i in exc.issues)
    first = [i for i in exc.issues if i["level"] == "error"][0]
    assert first["code"] == "paint_out_of_range"
    assert exc.object_name == "painted" == first["object_name"]
    assert exc.opt_key == first["opt_key"]


@pytest.mark.parametrize("kind,code", [("far", "object_outside_bed"), ("tall", "object_too_tall")])
def test_objects_outside_the_bed_or_too_tall_block_start(sc, kind, code):
    objs = [("far", *box(cx=400.0))] if kind == "far" else [("tall", *box(size=20.0, height=300.0))]
    job = new_job(sc, objects=objs)
    job.start()
    assert drive(job)[-1][0] == "failed"
    with pytest.raises(sc.ValidationError) as err:
        job.result()
    assert code in [i["code"] for i in err.value.issues if i["level"] == "error"]


def test_failed_job_releases_the_lock_and_rejects_mutators(sc):
    job = _failing_job(sc)
    job.start()
    drive(job)
    v, t = box()
    with pytest.raises(sc.StateError):
        job.add_object("late", v, t)
    with pytest.raises(sc.StateError):
        job.start()
    with pytest.raises(sc.StateError):
        job.validate()
    ok = new_job(sc)
    ok.start()
    assert drive(ok)[-1][0] == "done"


# --- run() -----------------------------------------------------------------------------

def test_run_reports_progress_on_the_calling_thread(sc):
    calls = []
    result = new_job(sc).run(progress=lambda pct, msg: calls.append((pct, msg)))
    assert result.objects == ["cube"]
    assert calls, "progress should be called at least once"
    pcts = [p for p, _ in calls]
    assert pcts == sorted(pcts)
    assert all(isinstance(m, str) for _, m in calls)


def test_run_without_callbacks(sc):
    assert new_job(sc).run().objects == ["cube"]


def test_run_with_a_cancelled_token_cancels_or_finishes(sc):
    token = sc.CancelToken()
    token.cancel()
    try:
        result = new_job(sc).run(cancel=token)
    except sc.Cancelled:
        return
    assert result.objects == ["cube"]   # finished before the cancel took effect


def test_run_raises_validation_error(sc):
    with pytest.raises(sc.ValidationError):
        _failing_job(sc).run()


# --- exception attributes (section 7) --------------------------------------------------

def test_state_error_carries_the_state(sc):
    job = new_job(sc)
    with pytest.raises(sc.StateError) as err:
        job.result()
    assert isinstance(err.value, sc.Error) and err.value.state in STATES

