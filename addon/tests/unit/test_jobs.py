# SPDX-License-Identifier: GPL-3.0-or-later
"""The slice job runner (``engine/jobs.py``) against the fake engine, without Blender."""
from __future__ import annotations

import ast
from pathlib import Path

import fake_engine as sc
import numpy as np
import pytest
from slicewright.core.plate_data import ExtractedObject
from slicewright.engine import jobs
from slicewright.engine.jobs import JobRunner, SliceRequest

PKG = Path(jobs.__file__).resolve().parents[1]
_TRIS = np.array([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                  (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)], np.int32)
PRINTER = {"name": "P", "printable_area": ["0x0", "256x0", "256x256", "0x256"], "printable_height": "250",
           "nozzle_diameter": ["0.4"]}
PROCESS = {"name": "Q", "layer_height": "0.2", "initial_layer_print_height": "0.2"}
FILAMENT = {"name": "F", "filament_type": ["PLA"], "filament_colour": ["#FFFFFF"], "nozzle_temperature": ["220"]}


def cube(name="cube", cx=128.0, cy=128.0, size=20.0) -> ExtractedObject:
    r = size / 2
    v = np.array([(cx - r, cy - r, 0), (cx + r, cy - r, 0), (cx + r, cy + r, 0), (cx - r, cy + r, 0),
                  (cx - r, cy - r, size), (cx + r, cy - r, size), (cx + r, cy + r, size),
                  (cx - r, cy + r, size)], np.float32)
    return ExtractedObject(name, name, v, _TRIS.copy())


def request(objects=None, process=None, printer=None, **kw) -> SliceRequest:
    return SliceRequest({**PRINTER, **(printer or {})}, {**PROCESS, **(process or {})}, [dict(FILAMENT)],
                        objects if objects is not None else [cube()], **kw)


def drive(runner: JobRunner, limit: int = 200) -> list:
    """Poll to a terminal state. Returns every snapshot."""
    seen = []
    for _ in range(limit):
        status = runner.poll()
        seen.append(status)
        if status.terminal:
            return seen
    raise AssertionError(f"no terminal state after {limit} polls: {seen[-1]}")


@pytest.fixture
def runner():
    r = JobRunner(sc)
    yield r
    r.cancel(discard=True)
    for _ in range(50):                      # let a live job stop so it does not hold the engine lock
        if not r.live:
            break
        r.poll()


# -- the happy path -----------------------------------------------------------------------------------------

def test_start_validating_slicing_done_with_a_result(runner):
    first = runner.start(request(tag="scene-1"))
    assert (first.state, first.finished, first.tag) == (jobs.VALIDATING, False, "scene-1")
    assert first.key and runner.live
    seen = [first] + drive(runner)
    states = [s.state for s in seen]
    assert states[0] == jobs.VALIDATING and states[-1] == jobs.DONE
    assert jobs.SLICING in states
    assert states.index(jobs.VALIDATING) < states.index(jobs.SLICING) < states.index(jobs.DONE)
    final = seen[-1]
    assert final.finished and final.result is not None and final.error is None
    assert final.progress == 1.0 and final.text == "Done"
    assert [s.finished for s in seen].count(True) == 1               # the outcome is delivered once
    progress = [s.progress for s in seen]
    assert progress == sorted(progress)                              # non-decreasing
    assert len(final.result.layers["z"]) == 100                      # 20 mm at 0.2 mm
    assert not runner.live and runner.poll().finished is False       # polling after the end is harmless


def test_slicing_text_has_percent_and_the_engine_stage(runner):
    runner.start(request())
    texts = [s.text for s in drive(runner)]
    assert any(t.startswith("Slicing ") and "%" in t and " · " in t for t in texts), texts


def test_warnings_and_pre_checks_merge_into_the_issues(runner):
    pre = [{"level": "warning", "code": "mesh_open_edges", "message": "cube has open edges",
            "opt_key": None, "object_name": "cube"},
           {"level": "info", "code": "large_mesh", "message": "big", "opt_key": None, "object_name": "cube"}]
    runner.start(request(issues=pre))
    final = drive(runner)[-1]
    codes = [i["code"] for i in final.issues]
    assert {"mesh_open_edges", "large_mesh"} <= set(codes)
    assert [i["level"] for i in final.issues] == sorted((i["level"] for i in final.issues),
                                                        key=["error", "warning", "info"].index)


def test_merge_issues_prefers_the_first_and_puts_errors_first():
    a = {"level": "error", "code": "object_outside_bed", "message": "engine text", "opt_key": "printable_area",
         "object_name": "x"}
    b = {"level": "error", "code": "object_outside_bed", "message": "our text", "opt_key": None, "object_name": "x"}
    c = {"level": "info", "code": "moved_to_bed", "message": "m", "opt_key": None, "object_name": "x"}
    merged = jobs.merge_issues([c], [a], [b, c])
    assert merged == [a, c]


def test_slice_key_matches_the_key_a_job_reports_and_follows_the_inputs(runner):
    req = request()
    runner.start(req)
    key = drive(runner)[-1].key
    config = jobs.normalized_config(sc, req.printer, req.process, req.filaments)
    assert config is not None
    assert jobs.slice_key(sc, config, [o.key for o in req.objects]) == key
    other = jobs.normalized_config(sc, req.printer, {**req.process, "layer_height": "0.3"}, req.filaments)
    assert jobs.slice_key(sc, other, [o.key for o in req.objects]) != key
    assert jobs.slice_key(sc, config, [o.key for o in req.objects] + ["extra"]) != key
    assert jobs.slice_key(sc, config, list(reversed([o.key for o in req.objects]))) == key     # order-free


def test_normalized_config_is_none_for_an_invalid_value_or_an_uncomposable_preset():
    req = request()
    assert jobs.normalized_config(sc, req.printer, {**req.process, "layer_height": "-1"}, req.filaments) is None
    assert jobs.normalized_config(sc, {"name": "P", "nozzle_diameter": 3}, req.process, req.filaments) is None


# -- failures -----------------------------------------------------------------------------------------------

def test_validation_failure_is_typed_and_lists_the_issues(runner):
    runner.start(request([cube("out", cx=500.0), cube("ok")]))
    final = drive(runner)[-1]
    assert (final.state, final.finished) == (jobs.FAILED, True)
    assert final.error.kind == "ValidationError" and final.error.object_name == "out"
    assert final.error.action == "jump_to_issue" and final.result is None
    assert final.message == "Validation failed: out is outside the printable area"
    assert final.text == final.message
    assert [i["code"] for i in final.issues] == ["object_outside_bed"]
    assert not runner.live


def test_two_validation_errors_are_counted_in_the_message(runner):
    runner.start(request([cube("a", cx=500.0), cube("b", cx=600.0)]))
    assert drive(runner)[-1].message.endswith("is outside the printable area (+1 more)")


def test_an_invalid_setting_blocks_the_slice_before_the_engine_job(runner):
    status = runner.start(request(process={"layer_height": "-1"}))
    assert (status.state, status.finished) == (jobs.FAILED, True)      # no polling needed
    assert status.error.kind == "ConfigError" and status.error.opt_key == "layer_height"
    assert status.message.startswith("Settings error:") and "layer_height" in status.message
    assert [i["opt_key"] for i in status.issues] == ["layer_height"]
    assert not runner.live


def test_a_config_error_from_compose_is_mapped(runner):
    status = runner.start(request(printer={"printable_height": "tall"}))
    assert status.state == jobs.FAILED and status.error.kind == "ConfigError"
    assert status.error.opt_key == "printable_height" and status.finished


def test_a_config_error_found_on_the_engine_thread_reaches_the_status(runner):
    runner.start(request(printer={"machine_start_gcode": "{ unbalanced"}))
    final = drive(runner)[-1]
    assert (final.state, final.error.kind, final.error.action) == (jobs.FAILED, "ConfigError", "highlight_key")
    assert final.message.startswith("Settings error:")


def test_an_unexpected_engine_failure_becomes_an_engine_error(runner, monkeypatch):
    import fake_engine.slicer as slicer

    def boom(*args, **kwargs):
        raise RuntimeError("native crash")
    monkeypatch.setattr(slicer, "synthesize", boom)
    runner.start(request())
    final = drive(runner)[-1]
    assert final.state == jobs.FAILED and final.error.kind == "EngineError"
    assert final.message.startswith("Engine error:") and "Copy diagnostics" in final.message


def test_an_empty_plate_fails_without_an_engine_job(runner):
    status = runner.start(request(objects=[]))
    assert status.state == jobs.FAILED and status.error.kind == "ValidationError" and status.finished
    assert "no objects" in status.message and status.issues[0]["code"] == "empty_plate"


def test_unknown_keys_are_reported_but_do_not_block(runner):
    runner.start(request(process={"made_up_key": "1"}))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE
    assert [i["code"] for i in final.issues if i["level"] == "warning"].count("unknown_key") == 1


def test_preset_keys_are_demoted_to_one_summary_and_user_keys_stay_warnings(runner):
    process = {f"bambu_only_{n}": "1" for n in range(24)}
    runner.start(request(process={**process, "typed_by_the_user": "2"}, preset_keys=frozenset(process)))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE
    unknown = [i for i in final.issues if i["code"] == "unknown_key"]
    assert [(i["level"], i["opt_key"]) for i in unknown] == [("warning", "typed_by_the_user")]
    summary = [i for i in final.issues if i["code"] == "preset_keys_unknown"]
    assert len(summary) == 1 and summary[0]["level"] == "info"
    assert summary[0]["message"] == "24 settings from the preset aren't known to this engine"
    assert summary[0]["detail"].split(", ") == sorted(process)


def test_the_same_request_without_preset_keys_keeps_every_warning(runner):
    runner.start(request(process={f"bambu_only_{n}": "1" for n in range(3)}))
    final = drive(runner)[-1]
    assert [i["code"] for i in final.issues].count("unknown_key") == 3 and not [
        i for i in final.issues if i["code"] == "preset_keys_unknown"]


# -- Busy ---------------------------------------------------------------------------------------------------

def _hold_the_engine_lock():
    holder = sc.SliceJob()
    holder.set_config(sc.normalize_config(sc.compose_config(PRINTER, PROCESS, [FILAMENT]))["config"])
    holder.start()
    return holder


def test_busy_is_a_typed_failure_that_can_be_retried(runner):
    holder = _hold_the_engine_lock()
    status = runner.start(request())
    assert (status.state, status.finished, status.error.kind) == (jobs.FAILED, True, "Busy")
    assert status.error.action == "retry_later" and "busy" in status.message.lower()
    assert not runner.live
    holder.cancel()
    for _ in range(10):                           # the holder stops on its polls and frees the lock
        holder.poll()
    again = runner.start(request())               # the lock is free again
    assert again.state == jobs.VALIDATING and runner.live


# -- cancelling ---------------------------------------------------------------------------------------------

def test_cancel_stops_the_job_and_frees_the_engine(runner):
    runner.start(request())
    runner.poll()
    runner.poll()
    assert runner.cancel() is True
    assert runner.status.cancelling and runner.status.text == "Cancelling…"
    final = drive(runner)[-1]
    assert (final.state, final.finished, final.error.kind) == (jobs.CANCELLED, True, "Cancelled")
    assert final.error.severity == "INFO" and final.text == "Cancelled" and final.result is None
    assert not runner.live
    assert runner.start(request()).state == jobs.VALIDATING        # no Busy afterwards


def test_cancel_with_nothing_running_is_a_no_op(runner):
    assert runner.cancel() is False
    assert runner.status.state == jobs.IDLE


def test_cancel_with_discard_drops_the_outcome(runner):
    runner.start(request())
    runner.poll()
    runner.cancel(discard=True)
    final = drive_to_idle(runner)
    assert final.state == jobs.IDLE and final.result is None and not final.finished


def drive_to_idle(runner: JobRunner, limit: int = 200) -> jobs.JobStatus:
    status = runner.status
    for _ in range(limit):
        status = runner.poll()
        if not runner.live:
            return status
    raise AssertionError("job did not stop")


def test_a_new_slice_cancels_the_running_one_and_starts_after_it_stopped(runner):
    first = runner.start(request([cube("a")]))
    runner.poll()
    runner.poll()
    second = runner.start(request([cube("b", size=10.0)]))
    assert second.cancelling and second.key == first.key          # still the old job until it stops
    seen = drive(runner)
    assert [s.finished for s in seen].count(True) == 1            # the first job's outcome is never reported
    final = seen[-1]
    assert final.state == jobs.DONE and final.key != first.key and not final.cancelling
    assert len(final.result.layers["z"]) == 50                    # 10 mm cube: the second request ran


def test_the_latest_of_several_waiting_requests_wins(runner):
    runner.start(request([cube("a")]))
    runner.poll()
    runner.start(request([cube("b", size=10.0)]))
    runner.start(request([cube("c", size=30.0)]))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE and len(final.result.layers["z"]) == 150


def test_a_new_slice_that_fails_to_launch_reports_that_failure(runner):
    runner.start(request())
    runner.poll()
    runner.start(request(process={"layer_height": "-1"}))
    final = drive(runner)[-1]
    assert final.state == jobs.FAILED and final.error.kind == "ConfigError" and final.finished


def test_a_job_that_finishes_despite_the_cancel_is_replaced_by_the_new_request(runner):
    runner.start(request([cube("a", size=10.0)]))
    for _ in range(12):                                            # let the job run to its last stage
        runner.poll()
    runner.start(request([cube("b", size=30.0)]))
    final = drive(runner)[-1]
    assert final.state == jobs.DONE and len(final.result.layers["z"]) == 150


# -- keys ---------------------------------------------------------------------------------------------------

def _key(**kw) -> str:
    r = JobRunner(sc)
    key = r.start(request(**kw)).key
    r.cancel()
    for _ in range(20):
        r.poll()
    return key


def test_the_key_is_stable_for_identical_inputs_and_not_for_changed_ones():
    base = _key()
    assert _key() == base and len(base) == 32
    assert _key(objects=[cube(cx=129.0)]) != base                              # moved
    assert _key(objects=[cube(size=21.0)]) != base                             # resized
    assert _key(objects=[cube(), cube("two", cx=60.0)]) != base                # another object
    assert _key(process={"layer_height": "0.1", "initial_layer_print_height": "0.1"}) != base
    assert _key(printer={"printable_height": "200"}) != base
    assert _key(project={"wipe_tower_x": "10"}) != base
    over = cube()
    over.config_overrides = {"wall_loops": "4"}
    assert _key(objects=[over]) != base
    painted = cube()
    painted.face_support = np.ones(12, np.uint8)
    assert _key(objects=[painted]) != base


def test_the_key_ignores_threads_thumbnails_tags_and_object_order():
    base = _key(objects=[cube("a"), cube("b", cx=60.0)])
    shot = np.zeros((4, 4, 4), np.uint8)
    assert _key(objects=[cube("a"), cube("b", cx=60.0)], threads=2, thumbnails=[shot], tag="x") == base
    assert _key(objects=[cube("b", cx=60.0), cube("a")]) == base               # scene_key sorts the object keys


def test_the_engine_version_is_part_of_the_key(monkeypatch):
    base = _key()
    monkeypatch.setattr(sc, "version", lambda: {"version": "9.9.9", "api": (1, 0)})
    assert _key() != base


def test_thumbnails_and_threads_reach_the_job(runner, monkeypatch):
    seen = {}
    real = sc.SliceJob

    class Spy(real):
        def set_threads(self, n):
            seen["threads"] = n
            super().set_threads(n)

        def set_thumbnails(self, images):
            seen["thumbs"] = len(images)
            super().set_thumbnails(images)
    monkeypatch.setattr(sc, "SliceJob", Spy)
    runner.start(request(threads=3, thumbnails=[np.zeros((8, 8, 4), np.uint8)]))
    final = drive(runner)[-1]
    assert seen == {"threads": 3, "thumbs": 1} and final.result.stats["threads"] <= 3


# -- status text and structure ------------------------------------------------------------------------------

@pytest.mark.parametrize("status,text", [
    (jobs.JobStatus(), ""),
    (jobs.JobStatus(jobs.VALIDATING), "Validating…"),
    (jobs.JobStatus(jobs.SLICING, 0.42, "Generating supports"), "Slicing 42% · Generating supports"),
    (jobs.JobStatus(jobs.SLICING, 0.5, "x", cancelling=True), "Cancelling…"),
    (jobs.JobStatus(jobs.SLICING, 0.0, ""), "Slicing 0%"),
    (jobs.JobStatus(jobs.DONE, 1.0, "Done"), "Done"),
    (jobs.JobStatus(jobs.CANCELLED, message="Cancelled"), "Cancelled"),
])
def test_status_text(status, text):
    assert status.text == text


def test_error_messages_per_type():
    from slicewright.engine.adapter import ErrorReport as R
    assert jobs.error_message(R("SliceError", "ERROR", "bad mesh", "select_object", object_name="Bolt")) == \
        "Slicing failed: bad mesh (Bolt)"
    assert jobs.error_message(R("MemoryError", "ERROR", "Out of memory", "none")).startswith("Out of memory")
    assert jobs.error_message(R("TimeoutError", "WARNING", "The engine did not respond in time", "none")) == \
        "The engine did not respond in time"


def test_states_match_the_scene_enum_and_the_module_has_no_bpy():
    assert [k for k, _ in jobs.JOB_STATES] == ["idle", "validating", "slicing", "done", "failed", "cancelled"]
    for name in ("engine/jobs.py", "core/plate_data.py"):
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module}
        assert not imported & {"bpy", "gpu", "bmesh", "mathutils"}, name


# -- per-object setting overrides (03 section 4.1) ----------------------------------------------------------

def test_the_objects_overrides_are_handed_to_add_object(runner, monkeypatch):
    seen = {}
    real_add = sc.SliceJob.add_object

    def spy(self, name, vertices, triangles, **kw):
        seen[name] = kw.get("config_overrides")
        return real_add(self, name, vertices, triangles, **kw)
    monkeypatch.setattr(sc.SliceJob, "add_object", spy)
    over, plain = cube("over", cx=60.0), cube("plain", cx=190.0)
    over.config_overrides = {"wall_loops": "4"}
    runner.start(request(objects=[over, plain]))
    assert drive(runner)[-1].state == jobs.DONE
    assert seen == {"over": {"wall_loops": "4"}, "plain": None}


def test_an_override_changes_the_slice_key_and_the_filament_used(runner):
    plain_key = runner.start(request()).key
    drive(runner)
    thick = cube()
    thick.config_overrides = {"wall_loops": "6"}
    status = runner.start(request(objects=[thick]))
    assert status.key != plain_key
    final = drive(runner)[-1]
    assert final.state == jobs.DONE
    assert final.result.stats["filament_per_extruder"][0]["mm"] > 0


def test_an_override_the_engine_refuses_fails_the_slice_naming_the_object(runner):
    bad = cube("Bolt#2")
    bad.config_overrides = {"printable_height": "10"}        # not a per-object setting: add_object raises ConfigError
    status = runner.start(request(objects=[cube("ok", cx=60.0), bad]))
    assert status.state == jobs.FAILED and status.finished
    assert status.error.kind == "ConfigError" and status.error.object_name == "Bolt#2"
    assert status.message.startswith("Settings error: Bolt#2:") and "printable_height" in status.message
    issue = status.issues[0]
    assert (issue["code"], issue["opt_key"], issue["object_name"]) == ("config_error", "printable_height", "Bolt#2")
    assert not runner.live
