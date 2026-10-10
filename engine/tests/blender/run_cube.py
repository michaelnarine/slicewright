# SPDX-License-Identifier: AGPL-3.0-only
"""M2 acceptance inside Blender: import the real module in-process, slice the spike's cube through the public
API, require G-code equal to official OrcaSlicer v2.4.2, and use the read-only functions during the slice.

    blender -b --factory-startup --python engine/tests/blender/run_cube.py -- <dir with slicewright_engine/> [threads]

Exit code 0 on success, 1 on any failure (the process exits via os._exit so Blender never lingers in GUI mode)."""
import os
import sys
import time
import traceback
from pathlib import Path

argv = sys.argv[sys.argv.index("--") + 1:]
site = argv[0]
threads = int(argv[1]) if len(argv) > 1 else None
sys.path.insert(0, site)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "binding"))

code = 1
try:
    import bpy
    import slicewright_engine as sc
    import cube_case

    print("BLENDER", bpy.app.version_string, "ENGINE", sc.version()["version"], sc.__file__, flush=True)
    assert tuple(sc.version()["api"]) == (1, 0)
    job = cube_case.build_job(sc, threads=threads)
    job.start()
    calls = 0
    while job.poll()[0] not in ("done", "failed", "cancelled"):
        assert len(sc.config_schema()) > 500 and sc.normalize_config({"layer_height": "0.2"})["errors"] == {}
        calls += 1
        time.sleep(0.002)
    result = job.result()
    diff = cube_case.diff_against_reference(open(result.gcode_path, errors="replace").read())
    mode = "background" if bpy.app.background else "gui"
    print("MODE", mode, "CPUS", os.cpu_count(), "THREADS_REQUESTED", threads, "THREADS_EFFECTIVE", result.stats["threads"], flush=True)
    if threads:
        assert result.stats["threads"] == min(threads, os.cpu_count()), "the job did not run with the thread count asked for"
    print("LAYERS", result.stats["layer_count"], "READONLY_CALLS_DURING_SLICE", calls, "DIFF_LINES", len(diff), flush=True)
    assert not diff, "\n".join(diff[:30])
    print("OK", flush=True)
    code = 0
except BaseException:
    traceback.print_exc()
finally:
    sys.stdout.flush()
    os._exit(code)
