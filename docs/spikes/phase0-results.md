# Phase 0 spike results

Status as of 2026-10-09. Plan: [implementation/plan.md](../implementation/plan.md) §1. Branches: [`spike/gpu`](https://github.com/michaelnarine/slicewright/tree/spike/gpu), [`spike/native`](https://github.com/michaelnarine/slicewright/tree/spike/native).

## Summary and decisions

| Spike | Result |
|---|---|
| (a) GPU preview | **Passed** on Metal, OpenGL and Vulkan. Integrated-GPU check outstanding, tracked to M6. |
| (b) Native engine, macOS | **Passed.** |
| (c) Native engine, Windows/Linux | **Paused by user decision (2026-10-09).** Development continues macOS-first. |

**Decision (2026-10-09): in-process engine on macOS.** The out-of-process fallback is not adopted. The Windows/Linux in-process verdict is deferred until those platforms resume.

## (a) GPU spike

Hardware: Apple M1 Max (Metal), 10M moves in 10 chunks. CI run: [actions/runs/37978153596](https://github.com/michaelnarine/slicewright/actions/runs/37978153596) (artifacts `shots-opengl`, `shots-vulkan`, `compare-report`). Backend flag: `--gpu-backend opengl|vulkan`.

| Check | Result |
|---|---|
| Chunk build (median) | 8.5 ms (budget 40 ms) |
| FPS at 5M visible segments | 58-60 |
| FPS at 2M visible segments | 122-124 |
| Chunk seams | 9 of 9 boundaries exact; a deliberately broken seam is detected |
| Scrubbing | 0 texture creations |
| Depth occlusion | Correct on all backends |
| Cross-backend comparison (worst case of 9) | SSIM >= 0.9989; dE2000 > 5 on <= 0.376% of pixels |

**Outstanding: iGPU check.** Needs >= 30 FPS at 2M segments within a 384 MB VRAM budget:

```
Blender --factory-startup --enable-event-simulate --gpu-vsync off --python spikes/gpu/spike.py -- --mode full --out <dir>
```

### Findings

| Finding | Detail |
|---|---|
| Ribbon template | 4-vertex `TRI_STRIP`, about 2x faster than 6-vertex `TRIS`; fetch `t_val` only when the view mode needs it |
| `corner` attribute | Must be U32/`UVEC4`. A 1-byte U8 attribute aborts Blender on Metal (vertex descriptor stride 1) |
| `t_meta` packing | RG32F, 23 payload bits under exponent `0x3F800000`, decoded with `floatBitsToUint`. RG32UI from a float view also worked in a small diagnostic but was not fully exercised |
| Role mask | INT push constant. UINT push constants can't be set from Python, and a stale mask rejected everything on GL |
| Screenshots/tests | `GPUOffScreen.draw_view3d` doesn't run `POST_VIEW` handlers; capture via a `POST_PIXEL` `read_color`. `screen.screenshot` is black on GL under xvfb |
| Buffer readback | Multi-dimensional `gpu.types.Buffer` reports reversed strides: `np.asarray(memoryview(buf)).T.reshape(-1)` |
| `draw_instanced(instance_count=0)` | Draws ONE instance on Metal, GL and Vulkan (the earlier doc said "all instances"). The planner still skips empty ranges (zero draw calls) |
| `Buffer` from numpy | Zero-copy, ~0.01 ms for 4 MB, keeps the array alive |
| Side facts | `bpy.app.timers` never fire in `-b`. Workspaces only via `ops.workspace.duplicate()` plus rename (`bpy.data.workspaces` has no `new()`). `WorkSpaceTool` registration and activation work; the click-through raycast is untested |

Applied to [03 §7](../design/03-blender-addon.md).

## (b) Native spike, macOS

Fix commit `35137f8` on `spike/native`.

| Check | Result |
|---|---|
| Plain Python vs `Blender -b` | Identical output (17254 moves, 100 layers) |
| Import time | 0.01-0.11 s |
| 50 slices | ~50 ms each; RSS +2.25 MB over slices 10-50 (+1.92 MB in GUI) |
| Blender after slicing (GUI and `-b`) | Geometry Nodes (32k verts), remesh (43k polys), Cycles CPU render and denoise all pass |
| Exports (`nm -gU`) | Only `_PyInit_slicewright_engine` |
| Loaded libraries | Blender's `libtbb.dylib`, `libsystem_malloc`, `libc++`, embree4, OIDN |
| G-code vs official OrcaSlicer v2.4.2 CLI | **0 diff lines (10,438 lines).** The CLI needed process `compatible_printers` set to `[""]` |

The release wheel's CI confirmation in Blender is pending; M1-B layer 3 covers it.

### Findings

| Area | Finding |
|---|---|
| Config loading | Load each profile into an EMPTY `DynamicPrintConfig`, then `apply()` onto `full_print_config()` (as Orca's CLI does). Loading JSON directly into `full_print_config()` segfaults: default enum-vector options (`z_hop_types`, `extruder_type`) have a null `keys_map` in `ConfigOptionEnumsGenericTempl<false>::deserialize` |
| Uninitialised `Print::m_isBBLPrinter` | Must be false. Uninitialised, it flips G-code to the Bambu dialect (`; FEATURE:`, M486, M981) |
| Uninitialised `Print::m_origin` | Must be zero. Otherwise, on a non-fresh heap, coordinates like `X9223372036854775.807` |
| Sanitizers | Orca has uninitialised members: engine CI needs ASan/UBSan (macOS, Linux), and MSan/Valgrind on Linux when resumed. Both fixes are upstreamable ("Upstream-Status: to be submitted") |
| TBB (macOS) | Only Blender's `libtbb.dylib` is loaded; the module's static oneTBB had no visible effect. No tbbmalloc, rename or exported-symbols mitigation needed |
| TBB (Linux) | Blender 5.1.2 loads `libtbb.so.12.17`, `libtbbmalloc.so.2.17` and `libtbbmalloc_proxy.so.2.17`: process-wide malloc interposition is real on Linux |
| Resources | Orca logs a non-fatal `nozzle_info.json` resources-dir error on every slice; the engine must set Orca's resources dir to its bundled data |
| Local dev | zlib 1.2.11's `fdopen` macro breaks against the macOS 26 SDK. CI on macos-14 is unaffected; local builds need a patch |

### Thread-count-dependent G-code (open)

With 8+ threads, in GUI Blender only, output is deterministic but differs slightly from the reference (17228 vs 17254 moves; seam/start positions). `-b` at any thread count and GUI at <= 4 threads match exactly. Ruled out: heap contents, FPCR, `rand()`. Unchecked: whether the official Orca GUI shows the same effect. Plan: investigate in M2, pin determinism via `set_threads` if needed; golden tests must cover GUI-mode slicing.

### Trimming findings at v2.4.2

| Topic | Finding |
|---|---|
| OpenVDB | Already optional; only `SLA/Hollowing.cpp` needs an OpenVDB-free patch |
| Removed | OCCT, OpenCV, Draco, OpenSSL; ModelIO, STEP, svg, DRC and ObjColorUtils sources |
| Stubs | `ColorSpaceConvert` (RGB2HSV); a nanosvg implementation (lives in GUI `BitmapCache.cpp`). OpenSSL MD5 replaced via Boost (`Md5Shim.hpp`) |
| mcut | The spike kept mcut compiled; removal remains planned for M2; licence GPL-3.0-or-later is compatible either way |
| libnest2d | Must be added explicitly |
| Build | Our own ~170-line root CMake works |
| macOS | `CMAKE_FIND_FRAMEWORK=LAST` avoids Mono's png/jpeg headers |
| STEP | `Format/STEP_fwd.hpp` must keep `namespace fs = boost::filesystem` |
| Patch series | 5 patches, ~485 diff lines across 9 Orca files plus 2 new headers |

## (c) Windows and Linux (paused)

Builds were measured before the pause; the in-process verdict for these platforms is deferred.

| Measurement | macOS | Linux (manylinux_2_28) | Windows |
|---|---|---|---|
| Cold deps build | ~6.5 min | ~8 min (281 MB deps tree) | ~16 min |
| libslic3r module build | ~10 min | ~18 min | not measured |
| Module size | 10 MB | 22 MB | not measured |

Platform notes:
- **Linux:** link with `-Wl,-z,defs` to catch missing symbols such as `nsvgDelete`. auditwheel reported `manylinux_2_31` (max GLIBC 2.28, GLIBCXX 3.4.25) and the repair produced nothing; investigate when Linux resumes.
- **Windows:** needs `-D_UNICODE -DUNICODE` (`Emboss.cpp`, `PostProcessor.cpp`). Orca's prebuilt GMP/MPFR DLLs are in-tree at `deps/GMP/gmp/lib/win-x64/libgmp-10.dll` and `deps/MPFR/mpfr/lib/win-x64/libmpfr-4.dll`; no final decision yet.

Applied to [02](../design/02-native-engine.md) and the plan's risk register (R1, R22, R23).
