<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Determinism evidence: the cube slice at several thread counts, in Blender's GUI and background modes

Risk R22: the spike saw G-code that depended on the thread count in GUI Blender at 8 or more threads. The root
cause was uninitialised members (patches 0011 and 0012, 0013 for the remaining ones). This file is the evidence
that the fixed engine gives the reference output in the mode and at the thread counts where it diverged.

The CI runner (`macos-14`) has 3 vCPUs, so a request for 8 threads runs in an arena of 3 there, and CI runs
`blender -b` only. CI therefore cannot show this; this run, on a machine with more cores, does.

## Method

`engine/tests/blender/run_cube.py` imports the module built from the tip of the M2 stack (patches 0001 to 0013)
inside Blender 5.1.2, slices the spike cube (`engine/tests/fixtures/spike_cube`) with `set_threads(n)`, asserts that
`result.stats["threads"]` (the TBB arena the job really ran in) equals `min(n, cpu count)`, and compares the
normalised G-code line by line with `reference_orca_v2.4.2.gcode` (official OrcaSlicer v2.4.2 CLI output, see
`PROVENANCE.md`). The normaliser keeps the CONFIG block except for the keys listed in
`engine/tools/normalize_gcode.py:CONFIG_KNOWN_DIFFERENCES`.

    blender [-b] --factory-startup --python engine/tests/blender/run_cube.py -- <build>/python <threads>

## Result (2026-10-10)

Machine: Apple M1 Max, 10 cores, macOS 26.6.2, Blender 5.1.2 (arm64), Release build of the engine.

| Mode | Threads requested | Effective (arena) | Layers | Normalised diff lines vs reference |
|---|---|---|---|---|
| GUI | 1 | 1 | 100 | 0 |
| GUI | 4 | 4 | 100 | 0 |
| GUI | 8 | 8 | 100 | 0 |
| GUI | 10 | 10 | 100 | 0 |
| background (`-b`) | 1 | 1 | 100 | 0 |
| background (`-b`) | 8 | 8 | 100 | 0 |
| background (`-b`) | 10 | 10 | 100 | 0 |

Re-run it after any change to the patch series or to the job's threading and update the table.

## Locale of the worker threads

Every thread that takes part in a job's arena formats numbers with the C locale (04 section 9, rule 7). Orca sets it on
the TBB workers once per process, in the first arena it runs, so the engine does it per job: a
`tbb::task_scheduler_observer` bound to the job's arena sets the C locale on each thread when it enters the arena and
restores the previous one when it leaves (`CLocaleObserver` in `engine/src/binding/job.cpp`). Nothing waits for other
threads: TBB's concurrency is a maximum, so an earlier version, a barrier that needed all arena threads at once, could
wait forever (intermittent hang of the test suite on a 10-core machine). The binding tests run a `parallel_for` in a job
arena under a `de_DE.UTF-8` process locale and check the decimal point on every participating thread, and slice the
cube under that locale (`test_run_cube.py`).

