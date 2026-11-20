# SPDX-License-Identifier: GPL-3.0-or-later
"""The on-disk slice result cache (``engine/cache.py``, 03 section 6.4) against fake engine results, no Blender."""
from __future__ import annotations

import json
import os
import shutil
import types

import fake_engine as sc
import numpy as np
import pytest
from slicewright.core.ticking import TickRunner
from slicewright.engine import cache as cache_mod
from slicewright.engine import jobs
from slicewright.engine.cache import CacheError, ResultCache
from test_jobs import cube, drive, request

KEY = "0123456789abcdef0123456789abcdef"
KEY2 = "fedcba9876543210fedcba9876543210"


@pytest.fixture(scope="module")
def sliced():
    """One real (fake-engine) result: moves, layers, stats, warnings, a G-code file."""
    runner = jobs.JobRunner(sc)
    runner.start(request())
    status = drive(runner)[-1]
    assert status.state == jobs.DONE
    return status.result


def tiny(n_moves=50, gcode=b"G1 X1\n") -> types.SimpleNamespace:
    """A hand-made result of a chosen size (for the LRU tests)."""
    def write_gcode(path):
        with open(path, "wb") as f:
            f.write(gcode)
    return types.SimpleNamespace(
        moves={"type": np.zeros(n_moves, np.uint8), "position": np.zeros((n_moves, 3), np.float32)},
        layers={"z": np.array([0.2], np.float32), "first": np.array([0], np.uint32),
                "last": np.array([n_moves - 1], np.uint32)},
        gcode_line_ends=np.array([6], np.uint64), stats={"time_s": {"normal": 1.0, "silent": 1.0}},
        warnings=[{"level": "warning", "code": "w", "message": "m", "opt_key": None, "object_name": None}],
        objects=["a"], wipe_tower=None, write_gcode=write_gcode)


@pytest.fixture
def cache(tmp_path):
    clock = types.SimpleNamespace(now=1_000_000.0)

    def tick():
        clock.now += 10.0
        return clock.now
    c = ResultCache(str(tmp_path / "slices"), cap_bytes=10 * cache_mod.MB, clock=tick)
    c.clock = clock
    return c


# -- round trip --------------------------------------------------------------------------------------------

def test_round_trip_of_every_array_and_json_field(cache, sliced):
    entry = cache.store_now(KEY, sliced)
    assert entry is not None and entry.key == KEY and entry.bytes > 0
    hit = cache.lookup(KEY)
    assert hit is not None
    got = hit.load()
    assert set(got.moves) == set(sliced.moves) and set(got.layers) == set(sliced.layers)
    for name, arr in sliced.moves.items():
        assert got.moves[name].dtype == arr.dtype and np.array_equal(got.moves[name], arr), name
    for name, arr in sliced.layers.items():
        assert np.array_equal(got.layers[name], arr), name
    assert np.array_equal(got.gcode_line_ends, sliced.gcode_line_ends)
    assert got.gcode_line_ends.dtype == np.uint64
    assert json.loads(json.dumps(got.stats)) == json.loads(json.dumps(sliced.stats))
    assert got.warnings == list(sliced.warnings) == hit.warnings
    assert got.objects == list(sliced.objects) and got.wipe_tower == sliced.wipe_tower
    assert not got.moves["position"].flags.writeable                      # read-only, like the engine's arrays
    out = os.path.join(os.path.dirname(cache.root), "copy.gcode")
    got.write_gcode(out)
    with open(out, "rb") as a, open(sliced.gcode_path, "rb") as b:
        assert a.read() == b.read()


def test_a_cached_result_drives_the_same_fields_the_preview_reads(cache, sliced):
    from slicewright.blender.preview.runtime import PreviewRuntime
    cache.store_now(KEY, sliced)
    result = cache.lookup(KEY).load()
    rt = PreviewRuntime(result)
    assert rt.n_moves == len(sliced.moves["type"]) and rt.layers is result.layers
    assert result.stats["time_s"] == sliced.stats["time_s"]


def test_a_miss_is_none_and_lookup_never_creates_anything(cache):
    assert cache.lookup(KEY) is None and not cache.has(KEY)
    assert not os.path.exists(cache.root)


# -- keys ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "abc", "../etc", "..", "0123456789ABCDEF0123", "a/b" * 4, "x" * 32, "0" * 65])
def test_only_hash_like_keys_are_accepted_so_a_key_cannot_escape_the_root(cache, bad):
    assert cache.lookup(bad) is None
    with pytest.raises(ValueError):
        cache.store_now(bad, tiny())


def test_two_keys_never_share_an_entry_and_the_same_key_is_idempotent(cache):
    a, b = tiny(10), tiny(20)
    cache.store_now(KEY, a)
    cache.store_now(KEY2, b)
    assert len(cache.lookup(KEY).load().moves["type"]) == 10
    assert len(cache.lookup(KEY2).load().moves["type"]) == 20
    again = cache.store_now(KEY, tiny(99))                                  # same key: the existing entry stands
    assert again.key == KEY and len(cache.lookup(KEY).load().moves["type"]) == 10


def test_an_entry_copied_under_another_name_is_not_trusted(cache):
    cache.store_now(KEY, tiny())
    shutil.copytree(os.path.join(cache.root, KEY), os.path.join(cache.root, KEY2))
    assert cache.lookup(KEY2) is None                                       # its manifest says KEY
    assert cache.lookup(KEY) is not None


# -- corrupted and partial entries -------------------------------------------------------------------------

def _entry_file(cache, name):
    return os.path.join(cache.root, KEY, name)


@pytest.mark.parametrize("damage", ["no_manifest", "bad_json", "truncated_array", "missing_array",
                                    "missing_gcode", "wrong_format", "missing_warnings", "missing_stats"])
def test_a_corrupted_or_partial_entry_is_a_miss(cache, damage):
    cache.store_now(KEY, tiny())
    if damage == "no_manifest":
        os.remove(_entry_file(cache, "manifest.json"))
    elif damage == "bad_json":
        with open(_entry_file(cache, "manifest.json"), "w") as f:
            f.write("{nope")
    elif damage == "truncated_array":
        with open(_entry_file(cache, "moves.position.npy"), "r+b") as f:
            f.truncate(20)
    elif damage == "missing_array":
        os.remove(_entry_file(cache, "layers.z.npy"))
    elif damage == "missing_gcode":
        os.remove(_entry_file(cache, "out.gcode"))
    elif damage == "missing_warnings":
        os.remove(_entry_file(cache, "warnings.json"))
    elif damage == "missing_stats":
        os.remove(_entry_file(cache, "stats.json"))
    else:
        path = _entry_file(cache, "manifest.json")
        data = json.load(open(path))
        data["format"] = 999
        json.dump(data, open(path, "w"))
    assert cache.lookup(KEY) is None
    assert cache.store_now(KEY, tiny(7)) is not None                       # and a new store replaces it
    assert len(cache.lookup(KEY).load().moves["type"]) == 7


def test_same_size_garbage_in_an_array_fails_the_load_not_the_lookup(cache):
    cache.store_now(KEY, tiny())
    path = _entry_file(cache, "moves.type.npy")
    size = os.path.getsize(path)
    with open(path, "wb") as f:
        f.write(b"\x00" * size)
    entry = cache.lookup(KEY)
    assert entry is not None                                                # the size matches: only a read tells
    with pytest.raises(CacheError):
        entry.load()


def test_a_mismatching_moves_length_is_a_miss(cache):
    cache.store_now(KEY, tiny(10))
    path = _entry_file(cache, "manifest.json")
    data = json.load(open(path))
    data["n_moves"] = 11
    json.dump(data, open(path, "w"))
    assert cache.lookup(KEY) is None


# -- atomic writes ------------------------------------------------------------------------------------------

def test_a_write_is_invisible_until_the_last_step_and_leaves_no_part_files(cache):
    gen = cache.store(KEY, tiny())
    next(gen)                                                               # the first step has run
    assert cache.lookup(KEY) is None and not os.path.exists(os.path.join(cache.root, KEY))
    staging = [n for n in os.listdir(cache.root) if n.startswith(".tmp-")]
    assert len(staging) == 1
    for _ in gen:
        pass
    assert cache.lookup(KEY) is not None
    assert os.listdir(cache.root) == [KEY]                                  # staging was renamed, not copied
    assert not [n for n in os.listdir(os.path.join(cache.root, KEY)) if n.endswith(".part")]


def test_files_are_written_through_a_part_name_and_renamed(cache, monkeypatch):
    seen = []
    real = os.replace

    def spy(src, dst, *a, **k):
        seen.append((os.path.basename(src), os.path.basename(dst)))
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", spy)
    cache.store_now(KEY, tiny())
    files = [(s, d) for s, d in seen if s.endswith(".part")]
    assert files and all(s == d + ".part" for s, d in files)
    assert {d for _, d in files} >= {"out.gcode", "moves.type.npy", "manifest.json", "stats.json", "warnings.json"}


def test_a_failure_midway_removes_the_staging_and_publishes_nothing(cache):
    result = tiny()
    result.layers["first"] = np.array([object()], dtype=object)             # cannot be saved without pickle
    with pytest.raises(CacheError):
        cache.store_now(KEY, result)
    assert os.listdir(cache.root) == []


def test_a_failing_gcode_copy_is_a_cache_error_and_cleans_up(cache):
    result = tiny()

    def boom(path):
        with open(path, "wb") as f:
            f.write(b"half")
        raise OSError("disk full")
    result.write_gcode = boom
    with pytest.raises(CacheError, match="disk full"):
        cache.store_now(KEY, result)
    assert os.listdir(cache.root) == []


def test_closing_the_generator_early_removes_the_staging(cache):
    gen = cache.store(KEY, tiny())
    next(gen)
    next(gen)
    gen.close()                                                             # what the tick runner does on cancel
    assert os.listdir(cache.root) == []


# -- one array per tick ---------------------------------------------------------------------------------------

def test_each_step_finishes_exactly_one_array(cache):
    gen = cache.store(KEY, tiny())
    staging = None
    counts = []
    for _ in gen:
        found = [n for n in os.listdir(cache.root) if n.startswith(".tmp-")]
        staging = os.path.join(cache.root, found[0]) if found else None
        counts.append(None if staging is None else len([n for n in os.listdir(staging) if n.endswith(".npy")]))
    # the G-code, then 2 moves + 3 layers + line ends = 6 arrays, then the summary; the commit runs inside the
    # last step and the staging directory is gone after it
    assert counts == [0, 1, 2, 3, 4, 5, 6, 6, None]


def test_the_tick_runner_spreads_the_write_over_ticks(cache):
    runner = TickRunner(budget_s=1e-9)                                      # a budget of nothing: one step per tick
    done = []
    runner.submit("cache", cache.store(KEY, tiny()), on_done=done.append)
    ticks = runner.run_until_idle()
    assert ticks >= 8 and done and done[0].key == KEY and cache.has(KEY)


def test_loading_is_also_one_array_per_step(cache):
    cache.store_now(KEY, tiny())
    steps = list(_steps(cache.lookup(KEY).load_steps()))
    assert len(steps) == 6 + 1


def _steps(gen):
    while True:
        try:
            yield next(gen)
        except StopIteration:
            return


# -- LRU eviction --------------------------------------------------------------------------------------------

def _sized(cache, key, mb):
    """Store an entry of about ``mb`` MB (the G-code file carries the weight)."""
    result = tiny()
    payload = b"x" * int(mb * cache_mod.MB)

    def write_gcode(path):
        with open(path, "wb") as f:
            f.write(payload)
    result.write_gcode = write_gcode
    return cache.store_now(key, result)


def keys(n):
    return [f"{i:032x}" for i in range(1, n + 1)]


def test_the_oldest_used_entries_go_first_when_over_the_cap(cache):
    cache.cap_bytes = int(5.5 * cache_mod.MB)
    a, b, c, d = keys(4)
    for k in (a, b, c):
        _sized(cache, k, 2)                                                 # a, b stay under 5.5 MB; c pushes a out
    assert [e.key for e in cache.entries()] == [b, c]
    assert not cache.has(a)
    cache.lookup(b)                                                         # b is used: c is now the oldest
    _sized(cache, d, 2)
    assert sorted(e.key for e in cache.entries()) == sorted([b, d]) and not cache.has(c)


def test_a_hit_refreshes_the_entry_and_a_miss_changes_nothing(cache):
    a, b = keys(2)
    _sized(cache, a, 1)
    _sized(cache, b, 1)
    assert [e.key for e in cache.entries()] == [a, b]
    cache.lookup(a)
    assert [e.key for e in cache.entries()] == [b, a]
    cache.lookup(a, touch=False)
    assert [e.key for e in cache.entries()] == [b, a]


def test_the_cap_holds_over_many_stores(cache):
    cache.cap_bytes = 4 * cache_mod.MB
    for k in keys(12):
        _sized(cache, k, 1)
        assert cache.usage() <= cache.cap_bytes
    assert len(cache.entries()) == 3                                        # 1 MB each plus a few KB of arrays


def test_the_entry_just_stored_is_protected_even_above_the_cap(cache):
    cache.cap_bytes = cache_mod.MB // 2
    a = keys(1)[0]
    _sized(cache, a, 2)
    assert cache.has(a)                                                     # alone, bigger than the cap: kept
    b = keys(2)[1]
    _sized(cache, b, 2)
    assert cache.has(b) and not cache.has(a)


def test_a_cap_of_zero_stores_nothing(cache):
    cache.cap_bytes = 0
    assert cache.store_now(KEY, tiny()) is None
    assert not os.path.exists(cache.root)


def test_evict_sweeps_invalid_entries_and_old_staging_but_not_foreign_files(cache):
    cache.store_now(KEY, tiny())
    os.makedirs(os.path.join(cache.root, KEY2))                             # key-named, no manifest: not an entry
    stale = os.path.join(cache.root, f".tmp-{KEY}-1-1")
    fresh = os.path.join(cache.root, f".tmp-{KEY}-1-2")
    foreign = os.path.join(cache.root, "notes")
    for d in (stale, fresh, foreign):
        os.makedirs(d)
    old = cache.clock.now - cache_mod.STAGING_MAX_AGE_S - 100
    os.utime(stale, (old, old))
    cache.evict()
    assert not os.path.exists(os.path.join(cache.root, KEY2)) and not os.path.exists(stale)
    assert os.path.exists(fresh) and os.path.exists(foreign) and cache.has(KEY)


def test_clear_removes_entries_and_staging_only(cache):
    cache.store_now(KEY, tiny())
    os.makedirs(os.path.join(cache.root, ".tmp-x"))
    os.makedirs(os.path.join(cache.root, "keep"))
    assert cache.clear() == 1
    assert sorted(os.listdir(cache.root)) == ["keep"]


def test_an_entry_in_use_survives_a_failed_removal(cache, monkeypatch):
    """Windows cannot delete a file another process holds: eviction ignores the error and carries on."""
    a, b = keys(2)
    cache.cap_bytes = 1
    _sized(cache, a, 1)
    real = shutil.rmtree
    monkeypatch.setattr(shutil, "rmtree", lambda *x, **k: None)
    cache.evict()                                                           # does not raise
    monkeypatch.setattr(shutil, "rmtree", real)
    assert not cache.has(a)                                                 # the manifest went first: invalid now


# -- the runner short-circuits on a hit ------------------------------------------------------------------------

def _count_starts(monkeypatch):
    starts = []
    real = sc.SliceJob.start
    monkeypatch.setattr(sc.SliceJob, "start", lambda job: (starts.append(1), real(job))[1])
    return starts


def test_the_runner_serves_a_cached_key_without_creating_an_engine_job(cache, monkeypatch):
    starts = _count_starts(monkeypatch)
    runner = jobs.JobRunner(sc, cache)
    runner.start(request())
    first = drive(runner)[-1]
    assert first.state == jobs.DONE and not first.from_cache and len(starts) == 1
    cache.store_now(first.key, first.result)
    status = runner.start(request())                      # the same plate and settings
    assert (status.state, status.finished, status.from_cache) == (jobs.DONE, True, True)
    assert status.key == first.key and status.result is None and status.progress == 1.0
    assert status.text == "Done (from cache)" and status.cached.key == first.key
    assert len(starts) == 1 and not runner.live            # no job was made, nothing to poll
    assert runner.poll().finished is False                 # and the snapshot is reported once


def test_a_hit_carries_the_cached_warnings_and_the_requests_own_issues(cache):
    runner = jobs.JobRunner(sc, cache)
    pre = {"level": "warning", "code": "pre", "message": "pre-check", "opt_key": None, "object_name": "cube"}
    runner.start(request(issues=[pre], printer={"thumbnails": "48x48/PNG"}))
    first = drive(runner)[-1]
    assert "thumbnail_missing" in [i["code"] for i in first.issues]
    cache.store_now(first.key, first.result)
    hit = runner.start(request(issues=[pre], printer={"thumbnails": "48x48/PNG"}))
    assert [i["code"] for i in hit.issues] == [i["code"] for i in first.issues]


def test_use_cache_false_and_a_different_key_slice_normally(cache, monkeypatch):
    starts = _count_starts(monkeypatch)
    runner = jobs.JobRunner(sc, cache)
    runner.start(request())
    first = drive(runner)[-1]
    cache.store_now(first.key, first.result)
    runner.start(request(use_cache=False))
    assert not drive(runner)[-1].from_cache and len(starts) == 2
    runner.start(request([cube(size=30.0)]))
    assert not drive(runner)[-1].from_cache and len(starts) == 3


def test_a_cache_that_raises_is_ignored(monkeypatch):
    class Broken:
        def lookup(self, key):
            raise OSError("disk gone")
    runner = jobs.JobRunner(sc, Broken())
    runner.start(request())
    assert drive(runner)[-1].state == jobs.DONE


def test_a_hit_replaces_a_waiting_job_like_any_request(cache):
    runner = jobs.JobRunner(sc, cache)
    runner.start(request())
    first = drive(runner)[-1]
    cache.store_now(first.key, first.result)
    runner.start(request([cube(size=30.0)]))              # a live job on another plate ...
    runner.poll()
    runner.start(request())                               # ... superseded by one the cache holds
    seen = drive(runner)
    assert seen[-1].state == jobs.DONE and seen[-1].from_cache and seen[-1].finished
    assert not runner.live
