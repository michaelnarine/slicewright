# SPDX-License-Identifier: GPL-3.0-or-later
"""``engine/jobs.py`` (the slice job runner, 03 section 6.1) against the real engine: states, errors, ``Busy``,
cancelling, the staleness key and the issues a real run produces."""
from __future__ import annotations

import time

import numpy as np
import pytest
from real_helpers import cube, drive, flush_project, request
from contract_helpers import FILAMENT
from slicewright.engine import jobs
from slicewright.engine.jobs import JobRunner

pytestmark = pytest.mark.real_engine


def test_start_validating_slicing_done_with_a_real_result(real_sc, runner):
    first = runner.start(request(tag="scene-1"))
    assert (first.state, first.finished, first.tag) == (jobs.VALIDATING, False, "scene-1")
    assert first.key and runner.live
    seen = [first] + drive(runner)
    states = [s.state for s in seen]
    assert states[0] == jobs.VALIDATING and states[-1] == jobs.DONE and jobs.SLICING in states
    assert states.index(jobs.VALIDATING) < states.index(jobs.SLICING) < states.index(jobs.DONE)
    progress = [s.progress for s in seen]
    assert progress == sorted(progress) and progress[-1] == 1.0
    assert [s.finished for s in seen].count(True) == 1
    final = seen[-1]
    assert final.result is not None and final.error is None and final.text == "Done"
    assert not runner.live and runner.poll().finished is False         # polling after the end is harmless
    result = final.result
    assert len(result.layers["z"]) == 100 and result.stats["layer_count"] == 100       # 20 mm at 0.2 mm
    assert result.stats["time_s"]["normal"] > 0 and result.stats["filament_per_extruder"][0]["mm"] > 0


def test_slicing_text_names_the_engine_stage(runner):
    runner.start(request())
    texts = {s.text for s in drive(runner) if s.state == jobs.SLICING}
    assert any(t.startswith("Slicing ") and "%" in t for t in texts), texts


def test_a_second_start_cancels_the_first_and_runs_after_it(runner):
    big = request(objects=[cube(height=60.0)])
    runner.start(big)
    time.sleep(0.05)
    during = runner.start(request(objects=[cube(size=10.0)], tag="second"))     # no Busy: it waits for the engine lock
    assert during.cancelling or during.state in (jobs.VALIDATING, jobs.SLICING)
    final = drive(runner)[-1]
    assert final.state == jobs.DONE and final.tag == "second"
    assert len(final.result.layers["z"]) == 50                                   # 10 mm: the second plate won


def test_cancel_ends_cancelled_and_frees_the_engine(runner):
    runner.start(request(objects=[cube(height=60.0)]))
    time.sleep(0.05)
    assert runner.cancel() is True
    final = drive(runner)[-1]
    assert final.state == jobs.CANCELLED and final.result is None and final.message == "Cancelled"
    assert final.error.kind == "Cancelled" and final.error.severity == "INFO"
    assert runner.cancel() is False                                              # nothing left to cancel
    runner.start(request())                                                      # and the lock is free again
    assert drive(runner)[-1].state == jobs.DONE


def test_busy_is_mapped_while_another_job_holds_the_engine(real_sc, runner):
    other = real_sc.SliceJob()                                                   # not started: holds no lock yet
    runner.start(request(objects=[cube(height=60.0)]))
    try:
        busy = JobRunner(real_sc)
        status = busy.start(request())
        assert status.state == jobs.FAILED and status.error.kind == "Busy"
        assert status.error.action == "retry_later" and status.finished
    finally:
        runner.cancel()
        drive(runner)
    del other


def test_an_object_off_the_bed_fails_validation_with_issues(runner):
    status = runner.start(request(objects=[cube(cx=500.0)]))
    seen = [status] + (drive(runner) if not status.terminal else [])
    final = seen[-1]
    assert final.state == jobs.FAILED and final.finished and final.result is None
    assert final.error.kind == "ValidationError"
    assert final.message.startswith("Validation failed: ")
    assert any(i["level"] == "error" for i in final.issues)


def test_a_bad_value_is_a_settings_error_with_the_key(runner):
    final = runner.start(request(process={"layer_height": "banana"}))
    final = final if final.terminal else drive(runner)[-1]
    assert final.state == jobs.FAILED and final.error.kind == "ConfigError"
    assert final.message.startswith("Settings error:")
    assert [(i["code"], i["opt_key"]) for i in final.issues] == [("config_error", "layer_height")]


def test_an_empty_plate_is_a_validation_failure_without_calling_the_engine(runner):
    final = runner.start(request(objects=[]))
    assert final.state == jobs.FAILED and final.issues[0]["code"] == "empty_plate"


def test_two_filaments_need_the_project_flush_matrix(runner):
    fils = [dict(FILAMENT), {**FILAMENT, "name": "Second"}]
    failed = runner.start(request(filaments=fils))
    failed = failed if failed.terminal else drive(runner)[-1]
    assert failed.state == jobs.FAILED and "Flush volumes matrix" in failed.message
    runner.start(request(filaments=fils, project=flush_project(2)))
    assert drive(runner)[-1].state == jobs.DONE


def test_an_unknown_preset_key_is_an_unknown_key_warning_not_a_failure(runner):
    runner.start(request(process={"some_future_key": "1"}))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE
    assert any(i["code"] == "unknown_key" and i["opt_key"] == "some_future_key" and i["level"] == "warning"
               for i in final.issues), final.issues


# -- the staleness key (03 section 4.4) ----------------------------------------------------------------------

def _key(sc, **kw):
    r = request(**kw)
    config = jobs.normalized_config(sc, r.printer, r.process, r.filaments, r.project)
    assert config is not None
    for o in r.objects:
        o.compute_key()
    return jobs.slice_key(sc, config, [o.key for o in r.objects])


def test_the_key_is_stable_and_follows_config_and_geometry(real_sc):
    base = _key(real_sc)
    assert len(base) == 32 and base == _key(real_sc)
    assert _key(real_sc, process={"layer_height": "0.3"}) != base
    assert _key(real_sc, process={"wall_loops": "4"}) != base
    assert _key(real_sc, objects=[cube(cx=100.0)]) != base
    assert _key(real_sc, objects=[cube(size=30.0)]) != base


def test_the_runner_key_equals_the_staleness_key_and_ignores_threads(real_sc, runner):
    status = runner.start(request(threads=2))
    assert status.key == _key(real_sc)
    drive(runner)


def test_normalized_config_is_none_for_an_invalid_value(real_sc):
    r = request(process={"layer_height": "banana"})
    assert jobs.normalized_config(real_sc, r.printer, r.process, r.filaments) is None


def test_the_real_schema_covers_every_key_the_normalized_config_holds(real_sc):
    r = request()
    config = jobs.normalized_config(real_sc, r.printer, r.process, r.filaments)
    schema = real_sc.config_schema()
    assert len(config) > 400 and not [k for k in config if k not in schema]


def test_result_arrays_are_read_only_and_typed_as_documented(sliced):
    result = sliced.result
    for name, dtype in (("position", np.float32), ("type", np.uint8), ("role", np.uint8), ("layer_id", np.uint32),
                        ("gcode_line", np.uint32), ("time", np.float32)):
        arr = result.moves[name]
        assert arr.dtype == dtype and not arr.flags.writeable, name
    assert result.moves["time"].shape[1] == 2
    assert result.layers["z"].dtype == np.float32 and result.layers["first"].dtype == np.uint32
    assert result.gcode_line_ends.dtype == np.uint64
    n = len(result.moves["type"])
    assert result.layers["first"][0] == 0 and result.layers["last"][-1] == n - 1


def test_the_spike_cube_through_the_runner_equals_official_orca_gcode(runner):
    """The M2 golden (engine/tests/fixtures/spike_cube, G-code of OrcaSlicer v2.4.2) reached through the add-on's job
    runner instead of the engine's own test: the runner composes, normalises, keys and starts the job."""
    import sys
    from pathlib import Path
    from slicewright.core.plate_data import ExtractedObject
    binding = Path(__file__).resolve().parents[3] / "engine" / "tests" / "binding"
    if str(binding) not in sys.path:
        sys.path.insert(0, str(binding))
    import cube_case
    p = cube_case.profiles()
    v, t = cube_case.load_stl(cube_case.FIXTURES / "cube.stl")
    cx, cy = cube_case.bed_centre(p["machine"])
    v = np.ascontiguousarray(v + np.array([cx - 10.0, cy - 10.0, 0.0], dtype=np.float32))
    req = jobs.SliceRequest(p["machine"], p["process"], [p["filament"]], [ExtractedObject("cube.stl", "cube.stl", v, t)])
    runner.start(req)
    final = drive(runner)[-1]
    assert final.state == jobs.DONE, final
    diff = cube_case.diff_against_reference(open(final.result.gcode_path, errors="replace").read())
    assert not diff, "\n".join(diff[:20])
