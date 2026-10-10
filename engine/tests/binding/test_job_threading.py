# SPDX-License-Identifier: AGPL-3.0-only
"""M5 layer 1: the engine thread and the process-wide lock (04 sections 4.3, 4.4, 8, 9).

Covers Busy, the GIL being released while waiting, poll() cost, cancel latency per stage (measured and printed;
bounded generously so a slow CI runner does not flake), and a dropped live job releasing the lock."""
import threading
import time

import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402
import heavy  # noqa: E402

TERMINAL = ("done", "failed", "cancelled")


def _wait_terminal(job, timeout=120.0):
    end = time.monotonic() + timeout
    while job.poll()[0] not in TERMINAL:
        assert time.monotonic() < end, "job did not finish"
        time.sleep(0.001)
    return job.poll()[0]


def test_result_with_an_infinite_timeout_waits_without_a_limit():
    job = cube_case.build_job(sc)
    job.start()
    assert job.result(timeout=float("inf")).stats["layer_count"] > 0
    with pytest.raises(ValueError):
        job.result(timeout=float("nan"))


def test_a_cancel_token_that_cannot_be_read_still_cancels_the_job():
    """nb::cast<bool> of the token's `cancelled` throws a cast error, which is not a python_error: the cancel-and-wait
    path must run for it too, or the job keeps the engine lock (Busy for every later job)."""
    class Token:
        cancelled = "not a bool"

    with pytest.raises(Exception):
        cube_case.build_job(sc).run(cancel=Token())
    follow_up = cube_case.build_job(sc)
    follow_up.start()  # no Busy
    follow_up.result()


def test_ctrl_c_during_run_cancels_the_job_and_raises_keyboard_interrupt():
    """run() is a native loop: a signal handler only runs if the loop asks for it (PyErr_CheckSignals)."""
    import signal

    def handler(signum, frame):
        raise KeyboardInterrupt

    old = signal.signal(signal.SIGALRM, handler)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.15)
        with pytest.raises(KeyboardInterrupt):
            heavy.heavy_job(sc).run()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    follow_up = cube_case.build_job(sc)
    follow_up.start()  # the interrupted job was cancelled and joined: no Busy
    follow_up.result()


def test_start_returns_at_once_and_the_state_is_validating_or_later():
    job = heavy.heavy_job(sc)
    t0 = time.perf_counter()
    job.start()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05, f"start() took {elapsed:.3f}s; it must not do slicing work on the caller's thread"
    assert job.poll()[0] in ("validating", "running", "done")
    job.cancel()
    _wait_terminal(job)


def test_poll_and_cancel_are_cheap():
    job = heavy.heavy_job(sc)
    job.start()
    t0 = time.perf_counter()
    for _ in range(2000):
        job.poll()
    per_call = (time.perf_counter() - t0) / 2000
    assert per_call < 1e-3, f"poll() took {per_call * 1e6:.0f} us"
    t0 = time.perf_counter()
    job.cancel()
    assert time.perf_counter() - t0 < 1e-3
    _wait_terminal(job)


def test_busy_blocks_a_second_start_and_frees_on_the_terminal_state():
    first, second = heavy.heavy_job(sc), cube_case.build_job(sc)
    first.start()
    with pytest.raises(sc.Busy):
        second.start()
    assert second.poll()[0] == "idle"
    first.cancel()
    _wait_terminal(first)
    # The lock is free the moment a terminal state is visible (no race with the engine thread).
    second.start()
    assert _wait_terminal(second) == "done"


class GilProbe:
    """Tells whether a call let go of the GIL. A helper thread waits for `go` and then sets `released`; setting an Event
    needs the GIL, so it can only happen while the calling thread is inside native code that released it.

    The interpreter's switch interval is raised to 1000 s around the call. Without that, a helper that cannot get the GIL
    asks for it after 5 ms and the calling thread hands it over as soon as the call returns to Python, before the caller
    reads `released`: a call that held the GIL would look as if it had released it. With it, the request never comes and
    `released` is set during the call or not at all. No timing thresholds."""

    def __init__(self):
        self.go = threading.Event()
        self.released = threading.Event()
        self.stop = False
        self.thread = threading.Thread(target=self._run)

    def _run(self):
        while not self.stop:
            self.go.wait()
            self.go.clear()
            if self.stop:
                return
            self.released.set()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop = True
        self.go.set()
        self.thread.join()

    def released_during(self, call):
        """Runs `call` (its result or exception is discarded) and returns whether the GIL was free at some point."""
        import sys

        time.sleep(0.05)  # the helper is blocked in go.wait()
        self.released.clear()
        old = sys.getswitchinterval()
        sys.setswitchinterval(1000.0)
        try:
            self.go.set()
            try:
                call()
            except (TimeoutError, sc.Cancelled):
                pass
            return self.released.is_set()
        finally:
            sys.setswitchinterval(old)


def test_the_probe_can_tell_a_call_that_holds_the_gil():
    """The negative control: a native sleep that does NOT release the GIL must be reported as "not released". The
    positive tests below use exactly this probe."""
    with GilProbe() as probe:
        assert probe.released_during(lambda: sc._native._hold_gil_sleep(0.3)) is False
        assert probe.released_during(lambda: time.sleep(0.05)) is True  # a call that does release it


def test_the_gil_is_released_while_result_waits():
    job = heavy.heavy_job(sc)
    with GilProbe() as probe:
        job.start()
        assert probe.released_during(lambda: job.result(timeout=2.0)), "result() held the GIL while it waited"
    job.cancel()
    try:
        job.result()
    except sc.Cancelled:
        pass


def test_the_gil_is_released_during_validate_and_run_waits():
    with GilProbe() as probe:
        # validate(): synchronous with the GIL released; the job is built before the probe is armed
        job = heavy.heavy_job(sc, subdivisions=6, supports=False)
        assert probe.released_during(job.validate), "validate() held the GIL"
        # run(): waits in 50 ms slices with the GIL released
        job = heavy.heavy_job(sc, subdivisions=3)
        assert probe.released_during(job.run), "run() held the GIL while it waited"


def test_progress_callbacks_run_on_the_calling_thread_only():
    seen = set()
    heavy.heavy_job(sc, subdivisions=2).run(progress=lambda pct, msg: seen.add(threading.get_ident()))
    assert seen == {threading.get_ident()}


def test_dropping_a_live_job_cancels_it_and_frees_the_lock():
    job = heavy.heavy_job(sc)
    job.start()
    time.sleep(0.1)
    t0 = time.perf_counter()
    del job
    assert time.perf_counter() - t0 < 30
    nxt = cube_case.build_job(sc)
    nxt.start()  # no Busy
    assert _wait_terminal(nxt) == "done"


# Stage keywords from Orca's status texts, in pipeline order. The measurement cancels the first poll() that
# shows the stage and times the way to a terminal state.
STAGES = ["Slicing mesh", "Generating walls", "Generating infill", "Generating support", "Generating skirt", "G-code"]


def measure_cancel_latency(stage, threads=None):
    """Seconds from cancel() to a terminal state for the first poll whose message contains `stage`; None when
    the job finished before that stage showed up."""
    job = heavy.heavy_job(sc, threads=threads)
    job.start()
    while True:
        state, _, msg = job.poll()
        if state in TERMINAL:
            return None, state
        if stage.lower() in msg.lower():
            break
        time.sleep(0.0005)
    t0 = time.perf_counter()
    job.cancel()
    final = _wait_terminal(job)
    return time.perf_counter() - t0, final


@pytest.mark.parametrize("stage", STAGES)
def test_cancel_latency_per_stage(stage, record_property):
    latency, final = measure_cancel_latency(stage)
    if latency is None:
        pytest.skip(f"the job finished before a '{stage}' status was seen")
    record_property("cancel_latency_s", round(latency, 3))
    print(f"\ncancel latency at '{stage}': {latency * 1000:.0f} ms -> {final}")
    # 04 section 9 rule 8 targets < 0.5 s for most stages and a few seconds worst case; the bound here is loose
    # on purpose (shared CI runners). The numbers themselves are reported in the M5 PR.
    assert latency < 10.0
    assert final in ("cancelled", "done")
