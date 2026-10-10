# SPDX-License-Identifier: AGPL-3.0-only
"""M2 acceptance in plain Python: load and slice the spike's cube, G-code equal to official OrcaSlicer v2.4.2,
read-only functions usable during a slice. The same case runs inside Blender (engine/tests/blender/run_cube.py)."""
import gc
import os
import threading
import time

import numpy as np
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


def test_the_config_allowlist_has_no_stale_entries():
    """Every CONFIG_KNOWN_DIFFERENCES entry must still differ from the official G-code; one that does not is
    removed, so the list can only shrink and never hides a regression (engine/tools/normalize_gcode.py)."""
    result = cube_case.build_job(sc).run()  # the file goes with the result: keep it until it is read
    text = open(result.gcode_path, errors="replace").read()
    assert cube_case.stale_allowlist_entries(text) == []


def test_enum_vector_options_reach_the_gcode_config_block():
    """z_hop_types and nozzle_volume_type are enum vectors: values copied into the defaults of full_print_config()
    (null keys_map) serialised to nothing, so set_config lost them and the CONFIG block printed them empty."""
    job = cube_case.build_job(sc, config_extra={"z_hop_types": "Spiral Lift", "nozzle_volume_type": "High Flow"})
    result = job.run()
    text = open(result.gcode_path, errors="replace").read()
    block = cube_case.normalize_gcode.config_block(text)
    assert block["z_hop_types"] == "Spiral Lift"
    assert block["nozzle_volume_type"] == "High Flow"
    # ... and the official cube's own values are present too, not empty
    ref = cube_case.normalize_gcode.config_block(cube_case.reference_text())
    plain = cube_case.build_job(sc).run()
    got = cube_case.normalize_gcode.config_block(open(plain.gcode_path, errors="replace").read())
    for key in ("extruder_type", "nozzle_type", "overhang_fan_threshold", "retract_lift_enforce"):
        assert got[key] == ref[key] != "", key


@pytest.fixture
def decimal_comma_locale():
    """The process locale set to one that formats 1.5 as "1,5" (de_DE.UTF-8), restored afterwards; skipped if missing."""
    import locale

    old = locale.setlocale(locale.LC_ALL)
    for name in ("de_DE.UTF-8", "de_DE.utf8", "de_DE"):
        try:
            locale.setlocale(locale.LC_ALL, name)
            break
        except locale.Error:
            continue
    else:
        pytest.skip("no locale with a decimal comma is installed")
    if locale.localeconv()["decimal_point"] != ",":
        locale.setlocale(locale.LC_ALL, old)
        pytest.skip("the de_DE locale does not use a decimal comma here")
    yield
    locale.setlocale(locale.LC_ALL, old)


PROBE_SCRIPT = """
import json, locale, sys
for name in ("de_DE.UTF-8", "de_DE.utf8", "de_DE"):
    try:
        locale.setlocale(locale.LC_ALL, name)
        break
    except locale.Error:
        pass
else:
    print("NOLOCALE")
    sys.exit(0)
if locale.localeconv()["decimal_point"] != ",":
    print("NOLOCALE")
    sys.exit(0)
import slicewright_engine as sc
print(json.dumps(sc._native._locale_probe(int(sys.argv[1]))))
"""


@pytest.mark.parametrize("threads", [1, 4, 10])
def test_every_thread_of_the_job_arena_uses_the_c_locale(threads):
    """04 section 9 rule 7: the arena's threads get the C locale when they enter it (a task_scheduler_observer; there is
    no barrier that waits for all of them). Run in a FRESH process, before any slice, under a decimal-comma process
    locale: no earlier job or locale setting can have prepared the pool workers, so a thread that kept the process
    locale reports ',' (patch 0015 removes Orca's own once-per-process barrier that used to set it)."""
    import json
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-c", PROBE_SCRIPT, str(threads)], capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    assert out.returncode == 0, out.stderr
    if out.stdout.strip() == "NOLOCALE":
        pytest.skip("no locale with a decimal comma is installed")
    probe = json.loads(out.stdout.strip().splitlines()[-1])
    assert probe["decimal_points"] == ["."], probe
    assert probe["threads_seen"] >= 1


@pytest.mark.parametrize("threads", [1, 8])
def test_the_gcode_is_the_same_under_a_decimal_comma_process_locale(decimal_comma_locale, threads):
    result = cube_case.build_job(sc, threads=threads).run()
    assert cube_case.diff_against_reference(open(result.gcode_path, errors="replace").read()) == []


def test_a_custom_gcode_template_that_fails_is_a_config_error_from_result():
    """GCode::check_placeholder_parser_failed throws PlaceholderParserError from export_gcode, on the engine thread; it is
    a config problem like on the calling thread (04 section 7), not an EngineError."""
    p = cube_case.profiles()
    flat = sc.normalize_config(sc.compose_config({**p["machine"], "machine_start_gcode": "M104 S{"}, p["process"], [p["filament"]]))["config"]
    job = sc.SliceJob()
    job.set_config(flat)
    cx, cy = cube_case.bed_centre(p["machine"])
    v, t = cube_case.load_stl(cube_case.FIXTURES / "cube.stl")
    job.add_object("cube", v + np.array([cx - 10.0, cy - 10.0, 0.0], dtype=np.float32), t)
    job.start()
    with pytest.raises(sc.ConfigError) as err:
        job.result()
    assert "machine_start_gcode" in err.value.message
