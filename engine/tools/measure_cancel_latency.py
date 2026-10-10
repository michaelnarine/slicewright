# SPDX-License-Identifier: AGPL-3.0-only
"""Cancel latency per pipeline stage (04 section 9 rule 8, 02 section 4.2).

    PYTHONPATH=<build>/python python engine/tools/measure_cancel_latency.py [--subdivisions 5] [--samples 30]

Runs one uninterrupted job to learn its duration, then cancels fresh jobs at evenly spread moments and records,
for the stage that poll() showed at the moment of cancel(), the time until the job reached a terminal state.
`finalize` and post-processing are not cancellable, so cancels that land there finish as `done` and the latency is
the remaining tail. Prints a table (max and median per stage) and, with --json, the raw samples."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "binding"))
import heavy  # noqa: E402
import slicewright_engine as sc  # noqa: E402

TERMINAL = ("done", "failed", "cancelled")


def stage_of(message: str) -> str:
    return re.sub(r": layer \d+$", " (per layer)", message) or "(validating)"


def one_run(delay: float | None, **kw):
    job = heavy.heavy_job(sc, **kw)
    job.start()
    t_start = time.perf_counter()
    if delay is None:
        while job.poll()[0] not in TERMINAL:
            time.sleep(0.001)
        return time.perf_counter() - t_start, None, None
    while time.perf_counter() - t_start < delay:
        if job.poll()[0] in TERMINAL:
            return None, None, None
        time.sleep(0.0002)
    state, _, msg = job.poll()
    if state in TERMINAL:
        return None, None, None
    t0 = time.perf_counter()
    job.cancel()
    while job.poll()[0] not in TERMINAL:
        time.sleep(0.0002)
    return time.perf_counter() - t0, f"{state}: {stage_of(msg)}", job.poll()[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subdivisions", type=int, default=5)
    ap.add_argument("--samples", type=int, default=30)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--json", type=str, default=None)
    a = ap.parse_args()
    kw = {"subdivisions": a.subdivisions, "threads": a.threads}
    total, _, _ = one_run(None, **kw)
    print(f"uncancelled job: {total:.2f} s")
    samples = []
    for i in range(a.samples):
        delay = total * (i + 0.5) / a.samples
        lat, stage, final = one_run(delay, **kw)
        if lat is not None:
            samples.append({"at_s": round(delay, 3), "stage": stage, "latency_s": round(lat, 4), "final": final})
    by_stage: dict[str, list] = {}
    for s in samples:
        by_stage.setdefault(s["stage"], []).append(s)
    print(f"{'stage at cancel()':58s} {'n':>3s} {'median ms':>10s} {'max ms':>9s}  final states")
    for stage, rows in by_stage.items():
        lat = [r["latency_s"] * 1000 for r in rows]
        finals = ",".join(sorted({r["final"] for r in rows}))
        print(f"{stage:58s} {len(rows):3d} {statistics.median(lat):10.1f} {max(lat):9.1f}  {finals}")
    if a.json:
        Path(a.json).write_text(json.dumps({"total_s": total, "samples": samples}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
