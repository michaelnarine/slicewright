# SPDX-License-Identifier: AGPL-3.0-only
"""M2 acceptance in plain Python: load and slice the spike's cube, G-code equal to official OrcaSlicer v2.4.2,
read-only functions usable during a slice. The same case runs inside Blender (engine/tests/blender/run_cube.py)."""
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
