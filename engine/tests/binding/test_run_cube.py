# SPDX-License-Identifier: AGPL-3.0-only
"""M2 acceptance in plain Python: load and slice the spike's cube, G-code equal to official OrcaSlicer v2.4.2,
read-only functions usable during a slice. The same case runs inside Blender (engine/tests/blender/run_cube.py)."""
import gc
import os
import threading
import time

import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402


def test_run_slices_the_cube_and_matches_official_orca(tmp_path):
    job = cube_case.build_job(sc)
    seen = []
    result = job.run(progress=lambda pct, msg: seen.append((pct, msg)))
    assert job.poll()[0] == "done"
    assert result.objects == ["cube.stl"]
    assert result.stats["layer_count"] > 0
    out = tmp_path / "out.gcode"
    result.write_gcode(str(out))
    assert out.read_bytes() == open(result.gcode_path, "rb").read()
    diff = cube_case.diff_against_reference(out.read_text(errors="replace"))
    assert diff == [], "\n".join(diff[:40])
    pcts = [p for p, _ in seen]
    assert pcts == sorted(pcts) and pcts[-1] > 0.0


@pytest.mark.parametrize("threads", [1, 4, 8, 64])  # 64 is clamped to the machine (a larger arena never finishes)
def test_gcode_is_independent_of_the_thread_count(tmp_path, threads):
    job = cube_case.build_job(sc, threads=threads)
    result = job.run()
    # The arena the job really ran in: what was asked for, clamped to what the machine can supply (a CI runner
    # with 3 vCPUs runs "8" as 3). The parity claim is only as strong as this number, so it is asserted.
    available = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    assert result.stats["threads"] == min(threads, available)
    assert cube_case.diff_against_reference(open(result.gcode_path, errors="replace").read()) == []


def test_read_only_functions_work_during_a_slice():
    job = cube_case.build_job(sc)
    job.start()
    calls = 0
    ctx = sc.ConditionContext({"printer_model": "X"})
    while job.poll()[0] not in ("done", "failed", "cancelled"):
        assert len(sc.config_schema()) > 500
        assert sc.version()["api"] == (1, 0)
        assert sc.enums()["move_type"]["Extrude"] == 10
        assert sc.normalize_config({"layer_height": "0.2"})["errors"] == {}
        assert ctx.eval('printer_model == "X"') is True
        assert sc.eval_condition("num_extruders == 1", {"num_extruders": 1}) is True
        calls += 1
        time.sleep(0.002)
    assert job.result().stats["layer_count"] > 0
    assert calls >= 1


def test_busy_state_and_errors():
    job = cube_case.build_job(sc)
    job.start()
    other = cube_case.build_job(sc)
    with pytest.raises(sc.Busy):
        other.start()
    with pytest.raises(sc.StateError):
        job.set_config({})
    with pytest.raises(sc.StateError):
        job.start()
    job.result()
    with pytest.raises(sc.StateError):
        job.add_object("x", *cube_case.load_stl(cube_case.FIXTURES / "cube.stl"))
    assert job.result() is job.result()  # the same object every call


def test_cancel_reaches_cancelled():
    job = cube_case.build_job(sc)
    job.start()
    job.cancel()
    state = None
    while True:
        state = job.poll()[0]
        if state in ("done", "cancelled", "failed"):
            break
        time.sleep(0.002)
    if state == "cancelled":
        with pytest.raises(sc.Cancelled):
            job.result()
    else:
        assert state == "done"  # finished before the cancel took effect (allowed by 04 section 8)


def test_result_times_out():
    job = cube_case.build_job(sc)
    job.start()
    try:
        job.result(timeout=0.0)
    except TimeoutError:
        pass
    job.result()


def test_results_of_successive_jobs_have_distinct_files():
    """CPython reuses freed addresses; a G-code file name derived from one let job 2 overwrite result 1's file, and
    freeing result 1 then deleted job 2's (04 section 5.1)."""
    job1 = cube_case.build_job(sc)
    r1 = job1.run()
    path1 = r1.gcode_path
    data1 = open(path1, "rb").read()
    del job1
    gc.collect()
    job2 = cube_case.build_job(sc, threads=2)
    r2 = job2.run()
    assert r2.gcode_path != path1
    assert os.path.isfile(path1) and os.path.isfile(r2.gcode_path)
    assert open(path1, "rb").read() == data1
    path2 = r2.gcode_path
    del r1
    gc.collect()
    assert not os.path.exists(path1)
    assert os.path.isfile(path2), "freeing result 1 deleted result 2's file"


def test_write_gcode_reports_a_copy_error_as_engine_error(tmp_path):
    result = cube_case.build_job(sc).run()
    with pytest.raises(sc.EngineError) as err:
        result.write_gcode(str(tmp_path / "no" / "such" / "dir" / "out.gcode"))
    assert "cannot write" in err.value.message
    assert err.value.detail == "filesystem"
    result.write_gcode(str(tmp_path / "ok.gcode"))  # the result is still usable


def test_a_raising_progress_callback_does_not_keep_the_engine_busy():
    def boom(pct, msg):
        raise KeyError("boom")

    with pytest.raises(KeyError):
        cube_case.build_job(sc).run(progress=boom)
    follow_up = cube_case.build_job(sc)
    follow_up.start()  # no Busy: run() cancelled the job and waited for it before re-raising
    follow_up.result()
