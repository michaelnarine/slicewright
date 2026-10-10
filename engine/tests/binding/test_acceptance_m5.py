# SPDX-License-Identifier: AGPL-3.0-only
"""M5 acceptance items that are measurements: RSS growth over repeated slices (02 section 8, item 2) and the
construct_full_config unknown-key filter (02 section 5.9). Cancel latency per stage is in test_job_threading.py and
engine/tools/measure_cancel_latency.py; the others have their own files."""
import gc
import os
import subprocess

import pytest

sc = pytest.importorskip("slicewright_engine")
import cube_case  # noqa: E402
import heavy  # noqa: E402


def rss_mb():
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, check=True).stdout
    return int(out.strip()) / 1024.0


def one_slice(job_factory):
    job = job_factory()
    result = job.run()
    assert result.stats["layer_count"] > 0
    del result, job
    gc.collect()


@pytest.mark.parametrize("make", [lambda: cube_case.build_job(sc), lambda: heavy.heavy_job(sc, subdivisions=1)], ids=["cube", "sphere_with_supports"])
def test_rss_growth_over_slices_10_to_50_is_under_20_mb(make, record_property):
    """A leak per job (a Print, a result store, a thread) would show as a slope. Slices 10 to 50 of the same job."""
    for _ in range(10):
        one_slice(make)
    base = rss_mb()
    for _ in range(40):
        one_slice(make)
    growth = rss_mb() - base
    record_property("rss_growth_mb", round(growth, 2))
    print(f"\nRSS growth over slices 10-50: {growth:.2f} MB (base {base:.0f} MB)")
    assert growth < 20.0, f"RSS grew by {growth:.1f} MB over slices 10-50"


def test_non_filament_keys_in_a_filament_preset_are_filtered_out_of_compose_config():
    """02 section 5.1 / 5.9: construct_full_config takes the filament preset's filament keys only; a process or printer
    key inside it must not override the process preset, and an unknown key must not crash the composition."""
    p = cube_case.profiles()
    filament = {**p["filament"], "layer_height": "0.9", "printable_area": ["0x0", "10x0", "10x10", "0x10"], "wall_loops": "9",
                "filament_not_a_real_key": "x"}
    flat = sc.compose_config(p["machine"], p["process"], [filament])
    assert flat["layer_height"] == p["process"]["layer_height"]
    assert flat["wall_loops"] == p["process"]["wall_loops"]
    assert flat["printable_area"] == sc.compose_config(p["machine"], p["process"], [p["filament"]])["printable_area"]
    # the filament's own keys are still taken
    assert flat["nozzle_temperature"] == sc.compose_config(p["machine"], p["process"], [p["filament"]])["nozzle_temperature"]
