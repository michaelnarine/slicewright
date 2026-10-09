# Design 02: Native engine (`slicewright_engine`)

Status: draft for review, 2026-10-09. Owner: engine track.
Inputs: a source read of OrcaSlicer at `e72ace16` (2.5.0-dev tip, 2026-10-09), **re-verified at the pinned tag v2.4.2 (`8500fcdc`) in M1-B layer 1** (§10), and a local Blender 5.1.2 install.
Notation: **[V]** verified in the Orca source (file:line) or in Blender 5.1.2. **[U]** unverified; confirmed in the milestone named. **All Orca file:line references below are at v2.4.2** (`engine/third_party/OrcaSlicer`). §10 lists what moved, what is gone and which assumptions did not survive the move from the dev tip.

> **Interface:** [04-engine-api.md](04-engine-api.md) is authoritative; where this document differs, 04 wins. Layout and distribution: [01](01-architecture-overview.md). Schedule and risks: [the plan](../implementation/plan.md). Licensing: [compliance.md](../publishing/compliance.md).

---

## 0. Summary of decisions

| Topic | Decision |
|---|---|
| Engine form | `slicewright_engine`, built from `engine/` (AGPL-3.0-only) and published to PyPI as a self-contained wheel per platform. The add-on bundles that wheel unmodified. |
| Orca consumption | Git submodule pinned to an upstream **release tag** (not dev), plus a small public numbered patch series. The submodule is never edited in place. |
| Build | Our own top-level CMake building a trimmed `libslic3r` (no GUI, OCCT, OpenCV, Assimp, Draco, OpenVDB, OpenSSL, CURL, FreeType, GLFW/OpenGL, SLVS, mcut) and deps from Orca's own recipes, trimmed. Static with hidden visibility, except GMP/MPFR on Windows if Phase 0 chooses vendored DLLs (§3.4). |
| Binding | **nanobind**, CPython stable ABI (`cp312-abi3`). Loads on Blender 5.1's Python 3.13 and future 3.x. |
| Execution model | Pollable native job: `start()` spawns a `std::thread` that validates and slices; `poll()`, `cancel()`, `result()`. The engine never calls into Python. States: 04 §8. |
| Config | Python resolves `inherits`/`include` per preset; the engine composes with Orca's `PresetBundle::construct_full_config` and normalizes. |
| Output | Zero-copy numpy structure-of-arrays from `GCodeProcessorResult`; G-code is a file. |
| Profiles | Orca's profile JSON ships in the wheel as **one zip**, read with `zipfile` (`sc.profiles_archive()`). |
| Platforms | `macos-arm64` (11.2+), `windows-x64`, `linux-x64` (manylinux_2_28). Blender 5.0+ has no Intel Mac builds, so there is no macos-x64. |
| Dev backend | None in the engine. The add-on's fake (03 §9.2) is the single dev backend; an official Orca release binary is the golden-test oracle (§8). |

---

## 1. Goals and non-goals

### 1.1 Goals
1. Slice in-process with Orca's libslic3r, with G-code equal to OrcaSlicer at the pinned tag apart from header lines, for identical inputs.
2. Cover the v1 scope: multi-object, per-object overrides, filament assignment, multi-material, support and seam painting, arrange, validate, time and filament estimates per feature and per filament.
3. Return toolpaths as typed arrays the add-on can pack into GPU textures.
4. Never block or crash Blender: native threads, cancellable, contained errors, one job at a time.
5. Reproducible public builds for three platforms from one CI.

### 1.2 Non-goals (v1)
- SLA, STEP/SVG/DRC/textured-OBJ import, CAD, emboss, texture painting. Blender owns geometry.
- Multi-plate projects and 3MF project export. Single-plate `.gcode.3mf` **is** in v1.
- Network or printer communication (Python side).
- Incremental re-slicing (§5.10), free-threaded CPython.

Licensing consequences (AGPL-3.0-only engine, combination with the GPL add-on, store questions) are in [compliance.md](../publishing/compliance.md).

---

## 2. Source strategy

**Decision: git submodule plus a patch series**, no long-lived fork.

```
engine/                           (AGPL-3.0-only)
  third_party/OrcaSlicer/         submodule → upstream release tag, never edited in place
  patches/orca/0001-*.patch …     numbered, each with a header: purpose, upstreamable?, date
  cmake/ …                        our build (does NOT use Orca's top-level CMakeLists)
  src/binding/ …                  nanobind module
  src/stubs/ …                    stand-ins for GUI or excluded symbols (§3.2)
  src/glue/ …                     Orca-derived CLI/GUI glue we port (§5.9)
  tools/gen_tab_layout.py, tools/gen_third_party.py, …
  tests/ …
```

- **Pin a stable release tag, not dev.** The design was first read at a 2.5.0-dev commit. M1-B layer 1 pinned **v2.4.2** (commit `8500fcdccaa10b5099ac20d252af3a7c560046f1`, 2026-07-06; recorded in `engine/ORCA_PIN`), re-verified every file:line reference here (§10), and the commit is reported by `sc.version()`. Profiles come from the same tag. The submodule is shallow (`shallow = true`, about 370 MB checked out).
- **Applying patches.** `engine/tools/apply_patches.py` (run by configure) exports the pinned commit with `git archive` into `engine/build/orca-src` and runs `git apply` for each `patches/orca/NNNN-*.patch` in order, failing on any rejected hunk. The pristine submodule is what reviewers and the source tarball point at; the patch files are the AGPL §5a modification record. `engine/tools/lint_patch_headers.py` (CI) requires every patch to carry a header: purpose, date, author, SPDX and upstream status.
- **Keeping patches small.** Prefer excluding files in CMake and adding stub translation units in our repo over editing Orca sources. Edit Orca code only for include guards and `#if SLIC3R_HAS_X` around call sites. One purpose per patch.
- **Upstreaming.** A `SLIC3R_HEADLESS_MINIMAL` option and moving `Format/STEP.hpp` out of `Model.hpp` are reasonable upstream PRs.
- **Rebase** (per Orca release): bump, apply and fix conflicts, rebuild deps only if `deps/*` changed, run the golden suite against the official release binary of the new tag, regenerate schema and tab layout and diff them. Budget 1–3 days per minor release.

---

## 3. Build system

### 3.1 Why not Orca's top-level CMake
Orca's root `CMakeLists.txt` unconditionally runs `find_package` for Boost (`:584`), Eigen (`:587`), TBB, OpenSSL (`:632`), CURL, FreeType, ZLIB, EXPAT, PNG, **OpenGL, glfw3** (`:698`), cereal and NLopt (`:751`), and **fails hard without OpenVDB** (`:759-766`) [V]. At v2.4.2 there is no bundled-Python requirement and no `SLIC3R_CAD` option (both exist only at the dev tip): OCCT is required unconditionally by `libslic3r/CMakeLists.txt:541-543`. Patching that is more churn than a ~300-line root of our own that:

1. defines the interface targets `libslic3r/CMakeLists.txt` expects (`boost_libs`, `cereal::cereal`, `TBB::tbb`, `Eigen3::Eigen`, `NLopt::nlopt`, `noise::noise`, `PNG::PNG`, `JPEG::JPEG`, `ZLIB::ZLIB`, `EXPAT`);
2. adds only the needed `deps_src/` directories: admesh, **clipper (v1) and clipper2** (`libslic3r/CMakeLists.txt:588-589` links both), **glu-libtess** (`:594`), libigl, libnest2d, miniz, qhull (in-tree; `dep_Qhull` is not needed), qoi, semver, nanosvg, fast_float, nlohmann, earcut, agg (if referenced), mcut only while 0006 is not applied, and imgui *headers only* (`imstb_truetype.h` for `Emboss.cpp:12`);
3. `add_subdirectory(src/libslic3r)` with `SLIC3R_HEADLESS_MINIMAL=ON` (patch 0001, which must also gate the OpenCASCADE, OpenCV, draco and OpenSSL finds; there is no `SLIC3R_CAD` to switch off at v2.4.2).

### 3.2 Trimming inventory

Where each unwanted dependency enters libslic3r, and how it is removed [V at v2.4.2]:

| Dependency | Where it enters | Removal |
|---|---|---|
| **OCCT** | `find_package(OpenCASCADE REQUIRED)` at `libslic3r/CMakeLists.txt:541-543`, `OCCT_LIBS` `:548-575`. `Format/STEP.*`, `Format/svg.cpp:13-22`, `Shape/TextShape.cpp`. **Only `Model.hpp:26`** includes `STEP.hpp` (XCAF headers); `Print.cpp`, `GCode.cpp` and `Model.cpp` do not at v2.4.2, and there is no `CAD/` directory. `Model.cpp:16` includes `svg.hpp` | CMake option drops the `find_package`. A forward-declaring `STEP_fwd.hpp` replaces the one include in `Model.hpp`. Guard `Model::read_from_step` (`Model.hpp:1586`, `Model.cpp:185`) and the STEP/SVG branches of `read_from_file`. Exclude STEP, svg, TextShape |
| **OpenCV** | `find_package(OpenCV REQUIRED core)` at `:500`; `opencv_world` linked PUBLIC at `:584`. In libslic3r only `ObjColorUtils.*` (`:316-317`) uses it; `TexturePainting.cpp` and `TextureToColor/*` do not exist at v2.4.2, and nothing in libslic3r calls `ObjColorUtils` (only the GUI and CLI) | Drop the `find_package` and link item; exclude `ObjColorUtils`. No call-site guard needed |
| **Assimp** | **Not present at v2.4.2** (no `AssimpImport.cpp`, no link item) | Nothing to do |
| **Draco** | `find_package(draco REQUIRED)` at `:546` (lowercase); `draco::draco` linked at `:593`; `Format/DRC.cpp` (`:187-188`), included by `Model.cpp:18` (`load_drc` at `:316-317`) and `AppConfig.cpp:3` (`DRC_BITS_DEFAULT_STR`, `:298-299`) | Drop; exclude DRC; guard two call sites |
| **OpenVDB** | `SLA/Hollowing.cpp` uses it unconditionally (`:4`, `:27-28`, `:74`, `:88`, `:96`); `generate_interior` is called from `SLAPrintSteps.cpp:136` (and `hollow_mesh` at `:361`); `CSGMesh/VoxelizeCSGMesh.hpp:8` includes `OpenVDBUtils.hpp`. `OpenVDBUtils.cpp` is already optional in CMake (`:16-18`, `:489`, `:623-624`). `Support/TreeSupport3D.cpp:44-50` includes the legacy OpenVDB path only under `#ifndef TREE_SUPPORT_ORGANIC_NUDGE_NEW`, and the macro is defined to 1 | Guard Hollowing's grid code; a `generate_interior` stub in `src/stubs` returns an empty interior (SLA-only path) |
| **OpenSSL** | MD5 only: the `bbl_calc_md5` functions at `utils.cpp:1600-1617`, `bbs_3mf.cpp:40,6383`, and the public header `Utils.hpp:18` (the only `openssl` includes); `OpenSSL::Crypto` on the link line (`:606`) and `find_package(OpenSSL REQUIRED)` in the root (`:632`) | Use `boost::uuids::detail::md5`, already used by `AppConfig.cpp:33,616-623`. Remove the `Utils.hpp:18` include and the link item. No vendored MD5 |
| **GUI header** | `FlushVolCalc.cpp:3` includes `slic3r/Utils/ColorSpaceConvert.hpp` for `RGB2HSV` (`:76-77`); its definition pulls in wx | A header-and-source stub at the same include path in `src/stubs` providing `RGB2HSV` |
| **mcut** | `MeshBoolean.cpp:30` include; `libslic3r_cgal` links `mcut` (`:528`) and so does libslic3r (`:597`); `ModelObject::make_boolean` (`Model.hpp:523`, no callers); CSG is GUI-only (`Plater.cpp:15151`); negative volumes use 2D clipping (`PrintObjectSlice.cpp:427-434`) | **Removed.** Guard the `MeshBoolean.cpp:30` include and its mcut functions behind `SLIC3R_HAS_MCUT=0`, and drop both link items. FFF slicing never reaches it. This also closes the mcut licence question |
| **CURL, GLFW/OpenGL, SLVS, Shiny** | not in libslic3r, or only with `SLIC3R_PROFILE` (`SLVS`/`SLIC3R_CAD` do not exist at v2.4.2) | Not configured |
| **FreeType** | linked on non-Windows for OCCT font code (`:609-611`) and found in the root (`:634`) | Goes with OCCT |
| **Apple ModelIO** | `Format/ModelIO.mm` (`:479-483`, link `:617-621`), `Model.cpp:44-46` | Exclude, guard the call site |

**Kept:** NLopt (arrange), libnoise (fuzzy skin), clipper v1 and glu-libtess (in-tree), libjpeg-turbo/libpng/qoi (thumbnails), expat/miniz (`bbs_3mf.cpp`, which also defines `save_object_mesh` used by `Model.cpp`), CGAL + GMP/MPFR, and TBB, Boost (filesystem, system, thread, log, locale, nowide, regex, iostreams, chrono, date_time, atomic), Eigen, cereal, Clipper2, admesh, libigl, libnest2d, qhull. `calib.cpp` is compiled into libslic3r (`CMakeLists.txt:87-88`) and stays; its licence is noted in compliance.md. `SLA/*` stays compiled (with Hollowing stubbed) because `Model.hpp`, `PrintObject.cpp`, `AABBMesh.cpp` and `ContourZ.cpp` reach into SLA types.

**Outside libslic3r:**
- `deps/CMakeLists.txt:337-416` unconditionally includes GLEW, GLFW, OpenCSG, Blosc, OpenEXR, OpenVDB, Draco, OpenSSL, CURL, wxWidgets, FreeType, OCCT and OpenCV, and `_dep_list` (`:418-440`, `add_custom_target(deps ALL …)` at `:450`) names most of them. There is no python3, wxInspector, FFMPEG, Assimp, DataChannel or SLVS at v2.4.2. Patch 0007 makes the dependency list overridable (`-DORCA_DEPS_ONLY=…`) and guards those includes.
- `tests/libslic3r/CMakeLists.txt` at v2.4.2 has no `test_step.cpp`, `test_drc.cpp` or Draco link, and `test_hollowing.cpp` is already conditional on OpenVDB (`:33-35`). Our CMake still lists the Catch2 test sources itself, so no Orca edit is needed.

### 3.3 Patch series and estimated size

| Patch | Files (Orca) |
|---|---|
| 0001 `SLIC3R_HEADLESS_MINIMAL`: drop REQUIRED finds (OpenCASCADE, draco, OpenCV) and link items (incl. OpenSSL, mcut, FreeType, ModelIO), source exclusions | `libslic3r/CMakeLists.txt` |
| 0002 STEP decoupling | new `STEP_fwd.hpp`, `Model.hpp` (`Model.cpp` only under the 0003 guard) |
| 0003 import guards (STEP, SVG, DRC, ModelIO; Assimp and textured OBJ are not present at v2.4.2) | `Model.cpp`, `AppConfig.cpp` |
| 0004 OpenVDB-free Hollowing | `SLA/Hollowing.cpp` |
| 0005 MD5 via Boost | `utils.cpp`, `bbs_3mf.cpp`, `Utils.hpp` |
| 0006 mcut removal | `MeshBoolean.cpp` (+ its CMake entry) |
| 0007 overridable deps list (`ORCA_DEPS_ONLY`; written in M1-B layer 2) | `deps/CMakeLists.txt`, `deps/deps-unix-common.cmake` |
| 0008 (only if chosen) GMP/MPFR from source on MSVC | `deps/GMP/GMP.cmake`, `deps/MPFR/MPFR.cmake` |

**Estimate: about 12–16 Orca files at v2.4.2 (the STEP and OBJ/OpenCV surface is smaller than at the dev tip), mostly CMake, a few hundred changed lines** [U, measured in M2]. Link errors from symbols referenced by excluded files are fixed with stubs in `src/stubs/` rather than more Orca edits. Patch layers in M2 aren't green until the link layer.

### 3.4 Dependency acquisition

**Decision: reuse Orca's own `deps/` ExternalProject recipes, trimmed** (patch 0007). vcpkg and conda-forge are rejected for releases because of version drift.

- *Why:* the same versions as Orca at v2.4.2 (Boost 1.84, oneTBB 2021.5, **CGAL 5.6.3**, Eigen 5.0.1, NLopt 2.5.0, libjpeg-turbo 3.0.1, GMP 6.2.1, MPFR 4.2.2, cereal 1.3.0, libnoise 1.0, zlib 1.2.13, libpng 1.6.35 [V]) keep golden parity achievable, and the recipes carry Orca's per-platform fixes. The dev tip uses CGAL 6.2.1; that is not what v2.4.2 ships, so we build 5.6.3.
- *Dep list* (`-DORCA_DEPS_ONLY=`, driver `engine/deps/`): `Boost;TBB;Cereal;NLopt;Eigen;CGAL;PNG;ZLIB;EXPAT;JPEG;libnoise`. CGAL pulls in GMP and MPFR. **`dep_Qhull` is dropped**: libslic3r links the in-tree `deps_src/qhull`, and Orca itself skips `dep_Qhull` on MSVC. At v2.4.2 `dep_JPEG` is not in Orca's own `_dep_list` (wxWidgets pulls it in), so the trimmed list names it explicitly.
- *Windows GMP/MPFR:* Orca's recipes copy **prebuilt DLLs that are committed in the Orca tree** (`deps/GMP/gmp/lib/win-*`, `deps/MPFR/mpfr/lib/win-*`) on Windows (`deps/GMP/GMP.cmake:8-20`, `deps/MPFR/MPFR.cmake:3-17`), so "everything static" does not hold there. Phase 0 spike (c) decides between (1) building them from source with MSVC and linking statically (patch 0008), or (2) vendoring the DLLs with `delvewheel` (name-mangled) and listing them, with provenance, in the licence files. Option 1 is preferred if it fits the timebox. The driver exposes the choice as `SLICEWRIGHT_GMP_SOURCE=ON|OFF` (`engine/deps/CMakeLists.txt`; OFF is the Windows default until the spike reports, and ON needs patch 0008, not yet written).
- *Linux:* `manylinux_2_28_x86_64` (GCC toolset 12+), deps from source in the container. glibc 2.28 matches Blender's Rocky 8 baseline [U, spike (c)].
- *macOS:* Xcode 15+, deployment target **11.2**, matching Blender 5.1.2 (`LSMinimumSystemVersion` and Mach-O `minos` are 11.2 [V]). Orca's default at v2.4.2 is **11.3** (`CMakeLists.txt:55-57`, `deps/CMakeLists.txt:28-33`), so the build passes `CMAKE_OSX_DEPLOYMENT_TARGET=11.2` explicitly; any 11.3-only API use is guarded in a patch.
- *Windows:* MSVC 2022, `/MD`, C++17.
- *Build times* [U, measured in Phase 0]: cold deps 25–45 min per platform; libslic3r (~213 .cpp at v2.4.2) plus binding 10–20 min on 4 cores with ccache/sccache; warm CI under 25 min.

### 3.5 Symbol hygiene and runtime coexistence
What Blender 5.1.2 loads on macOS [V, `otool -L` and a loaded-image list]: `@rpath/libtbb.dylib` (**oneTBB 2022.3**). It ships `libtbbmalloc_proxy` but does **not** load it. Windows and Linux are unverified and are checked in spike (c).

So **two oneTBB runtimes coexist** in one process: ours (2021.5, static, hidden) and Blender's (2022.3, dynamic). They have separate schedulers and thread pools; that is supported but means our `set_threads` default (cores − 1) can oversubscribe while Blender is busy. Rules:
- Compile everything with `-fvisibility=hidden -fvisibility-inlines-hidden`.
- Linux: `-Wl,--exclude-libs,ALL`, a version script exporting only `PyInit_slicewright_engine`, and `-Bsymbolic`.
- macOS: two-level namespaces isolate us. Windows: DLL isolation, plus mangled names for any vendored DLL.
- Spike (b)/(c) pass criteria: `nm -gU`, `dumpbin /dependents` and `ldd` show only the expected exports and dependencies; Blender's own TBB users (Geometry Nodes, remesh, a Cycles CPU render) still work after a slice.

---

## 4. Python binding

### 4.1 nanobind
nanobind ≥ 2.x supports the stable ABI for Python ≥ 3.12 (`STABLE_ABI`), interoperates with numpy through DLPack and the buffer protocol without numpy headers, and gives zero-copy owner capsules; binaries are several times smaller than pybind11's. Orca vendors pybind11 only for a GUI plugin host. **Decision: nanobind, `cp312-abi3`.** abi3 survives Blender's Python bumps and halves the matrix if two Blender lines are ever supported. If `extension build` rejects abi3 tags (checked in M7), fall back to `cp313` tags without code changes.

### 4.2 Execution model
Authoritative semantics are 04 §4 and §8.
```
job.start()        # takes the engine lock, spawns std::thread, state = validating; returns at once
  thread:          # apply + validate (unless cached) → failed(ValidationError) or running → process → export → convert
job.poll()         # (state, pct, msg): lock + copy
job.cancel()       # atomic flag → Print::cancel(); returns immediately
job.result(timeout=None)   # waits with the GIL released; raises the mapped exception on failure
```
- **Threading.** The engine thread and all TBB workers never touch the CPython API. `Print::set_status_callback` (`PrintBase.hpp:479`) fires from TBB workers, so the lambda only writes `{percent, text}` into a mutex-protected slot and appends warnings. The add-on polls from `bpy.app.timers`.
- **Cancellation.** `Print::cancel()` sets `CANCELED_BY_USER` (`PrintBase.hpp:501`); steps call `throw_if_canceled()`. Expect < 0.5 s for most stages and a few seconds in tree supports. G-code finalization and post-processing are not cancellable, so cancel has a tail at the end of a job [U, M5].
- **Exception mapping.** The engine thread catches everything and stores it: `CanceledException` → `Cancelled`; `SlicingError(s)` → `SliceError(object_name)`; validate errors (`StringObjectException`, `PrintBase.hpp:30-38`) → `ValidationError(issues)`; `BadOptionValueException`/`UnknownOptionException` → `ConfigError`; `std::bad_alloc` → `MemoryError`; anything else → `EngineError`.
- **One job per process**, enforced by a module-global mutex held from `start()` to a terminal state. Process-global state makes concurrent jobs unsafe [V]:
  - `GCodeProcessor::s_IsBBLPrinter`, written during export (`GCode.cpp:2047`, `Print.cpp:3761`) and also flipped by the wipe-tower constructors (`WipeTower.cpp:535`, `WipeTower2.cpp:582`); reset from config at every start;
  - `Model::extruderParamsMap`, `Model::printSpeedMap` and the incompatible-filaments list, set per job by the ported glue (§5.9);
  - lazily filled function-static caches (`Print.cpp:3080`, `FlushVolPredictor.cpp:310`), the Boost.Log core, and process-global `resources_dir`/`temporary_dir`.
  `config_schema()`, `eval_condition()` and `normalize_config()` read only static definitions, so they are safe during a job.
- **TBB.** A `tbb::global_control(max_allowed_parallelism, n)` wraps the job (`set_threads`).
- **Locale.** The engine thread body and `config_schema()` run under `CNumericLocalesSetter`.
- **Logging.** `set_logging_level(1)` at import; `sc.set_log(level, path)` routes Boost.Log to a file.
- **i18n.** `I18N::translate_fn` stays null (English) in v1.
- **Process-global dirs**, set once at import: resources (`info/`, `flush/`, ~32 KB; `filament_mixing/` does not exist at v2.4.2) and `<user tmp>/slicewright_engine/<pid>`. `Model::need_backup` defaults to false (`Model.hpp:1730`), so `save_object_mesh` writes nothing.

### 4.3 Memory and ownership
- Inputs arrive as `nb::ndarray<const float, shape<-1,3>, c_contig>` / `int32` and are copied once into `indexed_triangle_set` in `add_object`.
- Outputs are built on the engine thread into vectors owned by a `ResultStore`; each array is an `nb::ndarray<numpy>` whose capsule holds a `shared_ptr<ResultStore>`. The AoS `GCodeProcessorResult::moves` is released chunk by chunk during conversion (§5.7).
- `SliceJob` owns a `Model`, a `Print` and a `DynamicPrintConfig`; dropping a live job cancels and joins with the GIL released.

---

## 5. Mapping the API onto libslic3r

Reference flows: `tests/fff_print/test_data.cpp` `init_print()` (`:199`) + `gcode()` (`:284`) (there is no `test_helpers.cpp` at v2.4.2), and the CLI per-plate path `OrcaSlicer.cpp:5978-6227` [V].

### 5.1 Config
- `set_config(flat)`: `DynamicPrintConfig::full_print_config()`, then `set_deserialize` per key with `ForwardCompatibilitySubstitutionRule::EnableSilent` (runs `handle_legacy`, `PrintConfig.hpp:566-654`), then `handle_legacy_composite()` and `normalize_fdm`. Substitutions become issues.
- `compose_config`: build `Preset` objects from the resolved dicts and call `PresetBundle::construct_full_config(..., apply_extruder=false, filament_maps)`, **as the GUI does** (the GUI goes through `PresetBundle::full_config(false, f_maps)`, `Plater.cpp:7983,17493` and `PresetBundle.cpp:3858`; `construct_full_config` is called directly by `CalibUtils.cpp:937`; v2.4.2 has no `f_volume_maps` parameter); `Print::process` recomposes per-extruder values itself (`Print::update_filament_maps_to_config`, `Print.cpp:3166`, called at `:2491`). Filament keys unknown to the schema are filtered first, because they cause a null dereference at `PresetBundle.cpp:154-157`. This covers vector concatenation, `filament_map` and extruder-variant collapse (`update_values_to_printer_extruders`, `PresetBundle.cpp:68-190`).
- `normalize_config`: the `set_config` pipeline plus `DynamicPrintConfig::validate()`, without slicing.
- Before `apply`, mirror the CLI: `filament_map` of length `filament_count`, a default `nozzle_volume_type` (`OrcaSlicer.cpp:6027-6040`), and `is_BBL_printer` from `printer_model` before validation (`:6046-6059`).
- `eval_condition`: `set_deserialize` into a `DynamicPrintConfig`, then `PlaceholderParser::evaluate_boolean_expression` (`PlaceholderParser.hpp:67`); parse errors raise `ConfigError`.

### 5.2 Objects
```
ModelObject* o = model.add_object(); o->name = name;
TriangleMesh m(indexed_triangle_set{verts, tris});   // no repair, no reordering [V TriangleMesh.cpp:60-75]
o->add_volume(std::move(m));                         // recentres, compensates with volume offset
o->add_instance();
o->config.set("extruder", extruder);                 // on the OBJECT; 1-based, 0 = default
for (k, v) in overrides: o->config.set_deserialize(k, v)   // keys checked against PrintObjectConfig ∪ PrintRegionConfig
```
- Mesh repair is off by default; `repair=True` only merges vertices and reports open edges.
- The volume offset is moved into the instance (`center_around_origin`, instance offset = bbox centre) so arrange and sequential printing behave like Orca.
- After all objects: optional `ensure_on_bed()` (default off; a `moved_to_bed` issue if Z moved) and `print.auto_assign_extruders(o)`.

### 5.3 Paint → `FacetsAnnotation`
```
TriangleSelector sel(v->mesh());                  // same face indexing as the input
for i: if (face_support[i]) sel.set_facet(i, EnforcerBlockerType(face_support[i]));   // 1 ENFORCER, 2 BLOCKER
v->supported_facets.set(sel);                     // seams: v->seam_facets; MMU: v->mmu_segmentation_facets
```
The encodings map 1:1 onto `EnforcerBlockerType` (Extruder1 == 1 … **Extruder16 == `ExtruderMax`** at v2.4.2, `TriangleSelector.hpp:13-37`; the dev tip extends it to 32). `face_extruder` above 16 is therefore unrepresentable and is rejected with `paint_out_of_range` regardless of `filament_count`. Paint is per original triangle. The engine itself rejects `face_extruder` values above `filament_count` (`paint_out_of_range`), because Orca silently ignores them (`MultiMaterialSegmentation.cpp:2198`, `:1839-1852`). M5 pins enforcer behaviour when `enable_support = 0`.

### 5.4 Arrange
`arrange_objects(model, bed, ArrangeParams{…})` (`ModelArrange.hpp:33`). The bed is `printable_area` minus `bed_exclude_area`, with the wipe tower as an obstacle. Orca's CLI prepares both through GUI code (`preprocess_exclude_areas`, `OrcaSlicer.cpp:4761,4789`), which we port (§5.9). Returns placements (04 §4.5); `ArrangeError` when things don't fit.

### 5.5 Validate
`print.apply(model, cfg)`, then `Print::validate(StringObjectException* warning, Polygons* collision_polygons, std::vector<std::pair<Polygon,float>>* height_polygons)` (`Print.hpp:935`) plus our mesh and paint checks. **At v2.4.2 `validate` reports a single warning through one `StringObjectException*`, not a vector** (the dev tip takes `std::vector<StringObjectException>*`). Multiple Orca warnings per run therefore need either a validate patch (0009, upstream candidate) or only the warnings the single slot can carry; decided in M2. Runs on the engine thread in the `validating` state, or synchronously in `validate()`. Results use the `Issue` shape (04 §2.6).

### 5.6 Process and export
On the engine thread:
1. `print.process()` (status → slot).
2. `print.export_gcode(tmp_path, &gcode_result, thumbnail_cb)`. This runs GCodeProcessor and moves its result into ours (`GCode.cpp:2082-2178`). Export returns early if the step is done and the file exists (`GCode.cpp:2042`); jobs are single-use with unique temp paths, so this never fires, but it matters for v1.1 `keep_state`. `thumbnail_cb` serves the `set_thumbnails` images; Bambu printers skip it in G-code (`GCode.cpp:2644`, `else if (thumbnail_cb != nullptr)` at `:2659`).
3. Collect `print_statistics`, `PrintEstimatedStatistics`, `gcode_result.warnings`, `conflict_result` and `gcode_check_result`.
4. Convert moves (§5.7), then `done`.

Profile `post_process` scripts are **not** run (Orca runs them in the GUI layer, and they execute arbitrary commands).

### 5.7 Moves → numpy
The output fields are 04 §5.2. Mapping notes:
- `filament` ← `MoveVertex.extruder_id`, which is the filament index (`GCodeProcessor.cpp:5676`); −1 → 255. `nozzle` ← the filament → nozzle map from `get_filament_maps()`.
- `time` is per-move duration (Orca stores durations, `GCodeProcessor.cpp:472`); `object_id` ← `object_label_id`; `gcode_line` ← `gcode_id`; `gcode_line_ends` ← `lines_ends`.
- `layers` comes from a scan over `layer_id`, renumbered if needed to meet 04 §5.3.
- About 70 B/move; 5M moves ≈ 350 MB. Peak memory during conversion is AoS + SoA, so conversion runs in 1M-move chunks, releasing the AoS tail as it goes [U, M5].

### 5.8 Stats
The shape is 04 §5.4. Sources: `modes[i].time`; `time_by_role_s` and `time_by_move_type_s` summed natively from `moves.time`, as the GUI does (`slic3r/GUI/GCodeViewer.cpp:1326`; `roles_times` does not exist at v2.4.2 either); `filament_per_extruder` from the per-extruder volume maps with diameter, density and cost; `used_filament_per_role` (from `used_filaments_per_role`, `GCodeProcessor.hpp:76`); `flush_per_filament_g`; change counts; `display` from `print_statistics` strings.

### 5.9 CLI/GUI glue we port
Orca's CLI produces correct output partly by calling GUI-layer code. We port that glue into `engine/src/glue/` (Orca-derived, AGPL, per the provenance rule in compliance.md). This table is the parity checklist for the golden suite:

| Glue | Orca source | Feeds | Effort |
|---|---|---|---|
| `.gcode.3mf` PlateData filling: ~15 fields Bambu firmware reads (`gcode_prediction`, `gcode_weight`, `layer_filaments`, `filament_change_sequence`, `nozzle_change_sequence`, `is_label_object_enabled`, `parse_filament_info`, …) | CLI uses GUI `PartPlateList` (`OrcaSlicer.cpp:3705`, `:6351` `store_to_3mf_structure`; `PartPlate.cpp:6141-6228`) | `write_gcode_3mf` | 2–3 ed |
| Arrange exclude areas and wipe-tower obstacle | `preprocess_exclude_areas` (`OrcaSlicer.cpp:4761,4789`) | `arrange` | 1–2 ed |
| `Model::setExtruderParams` / `setPrintSpeedTable` | `OrcaSlicer.cpp:6138-6139`; read by `Brim.cpp:138` | brim and speeds | ≤ 1 ed |
| `print->set_extruder_filament_info` | `OrcaSlicer.cpp:5987-6023` | multi-nozzle printers | ≤ 1 ed |
| `construct_full_config(..., apply_extruder=false, filament_maps)`, unknown-key filtering | `Plater.cpp:7983,17493`, `Print.cpp:3166`, `PresetBundle.cpp:154` | `compose_config` | ≤ 1 ed |

Multi-nozzle (H2D-style) profiles compose and slice in v1, and H2D send is in v1 (03 §8.4).

### 5.10 Multi-plate and incremental slicing
v1 is single plate, origin (0, 0); `wipe_tower_x/y` index 0. `Print::apply()` already invalidates only changed steps, so keeping the `Print` alive across starts gives incremental re-slicing; that is v1.1 behind `keep_state` (mind the export early return, §5.6).

---

## 6. `config_schema()` and UI layout

`config_schema()` iterates `print_config_def.options` (`ConfigOptionDef`, `Config.hpp:2204+`) into the 04 §6.4 shape: `mode` from `comSimple..comDevelop`, `preset` from `Preset::{print,filament,printer}_options()` (`Preset.hpp:399-402`), `scope` from `PrintObjectConfig`/`PrintRegionConfig` membership, `variant` from `*_options_with_variant` (`PrintConfig.hpp:684-687`).

**Page and group layout is GUI-only**, in `slic3r/GUI/Tab.cpp` (`TabPrint::build()` `:2311`, `TabFilament` `:3892`, `TabPrinter` `:4437`). Calls are mostly regular (`add_options_page` → `new_optgroup` → `append_single_option_line("key", "wiki#anchor")`), so `tools/gen_tab_layout.py` tokenizes them into `tab_layout.json`. It **won't extract everything**: about 80 of the 585 `append_single_option_line` lines at v2.4.2 are commented out, about 36 pass `Option` objects instead of keys [grep counts, approximate], and some pages are conditional on printer type. The generator therefore merges a hand-maintained override file (`tools/tab_layout_overrides.json`) for those cases and the ~63 custom widget lines. Budget **2–3 ed** plus upkeep per rebase. CI fails if any key is missing from the schema.

Visibility and enable logic (`ConfigManipulation.cpp`, 181 `toggle_field`/`toggle_line` lines at v2.4.2) is imperative C++ and not extracted; the add-on owns a rule table (03 §2.4).

---

## 7. Packaging

### 7.1 Wheels
- **Distribution:** PyPI project `slicewright-engine` (import `slicewright_engine`). Avoids "Orca", "Blender" and vendor marks. `License-Expression: AGPL-3.0-only` with `License-File`s.
- **Wheels only, no sdist.** An sdist would be incomplete (the Orca submodule and dep sources) and over PyPI's 100 MB limit; corresponding source is a release tarball (compliance.md).
- **Tags:** `cp312-abi3-{macosx_11_0_arm64, win_amd64, manylinux_2_28_x86_64}`.
- **Tooling:** `scikit-build-core` + nanobind via `cibuildwheel`. Repair: `auditwheel` (Linux), `delocate` (macOS), `delvewheel` (Windows; vendors `msvcp140.dll` and, if chosen, GMP/MPFR DLLs with mangled names).
- **Package data:** `resources/{info,flush}`; `profiles.zip` (Orca's `resources/profiles/**/*.json` only, **12,006 files, 20.9 MB raw at v2.4.2** [V]; 8.9 MB with per-file deflate, so the dev-tip estimate of ~1.6 MB compressed must be re-measured in M7 [U]; one zip read via `zipfile` avoids Windows MAX_PATH limits on deep vendor paths, and excludes the strays `check_unused_setting_id.py` and `FlyingBear/error_hull_show`); `tab_layout.json`; `LICENSE`, `THIRD_PARTY_LICENSES`, `licenses/`, `NOTICE`, `SOURCE.txt`. No logos, covers, STLs, textures or fonts.
- **PyPI long description** carries the corresponding-source link (compliance.md).
- **Size** [U, M7]: module 25–50 MB stripped; wheel 12–25 MB compressed. Debug symbols go to GitHub Release assets.

### 7.2 macOS signing
Arm64 Mach-O needs at least an ad-hoc signature; re-sign after `delocate`. Developer ID signing plus notarization is optional (01 §9). Files written by Blender's extension installer don't normally carry the quarantine attribute, so ad-hoc should suffice [U, M7]. Signing happens in CI **before** PyPI upload, because the store requires unmodified PyPI wheels.

### 7.3 Consumption by the add-on
The add-on build downloads the pinned wheels (version and hashes) into `./wheels/`, lists them in `blender_manifest.toml`, and runs `blender --command extension build --split-platforms`. The add-on checks the API at register (04 §10). CI may build against a local wheel for development; release zips always bundle the published PyPI wheels.

### 7.4 CI (GitHub Actions, public)
- Matrix: `macos-14` (arm64), `windows-2022`, `ubuntu-24.04` with the manylinux_2_28 container.
- **deps** job: key = hash of the pinned Orca `deps/**`, our deps patch, toolchain image digest and compiler. Cached and uploaded as release assets (`deps-<key>-<platform>.tar.zst`), **with** licence files, NOTICE, a source pointer and the dep source tarball, since that is already a binary publication.
- **wheel** job: apply patches, build, C++ tests and pytest, repair, `gen_third_party.py`, then load in **real Blender 5.1.2** headless on each OS and slice a cube (the symbol-clash test).
- **publish** (tag): PyPI trusted publishing plus a GitHub Release with the corresponding-source tarball.
- sccache per platform; `SOURCE_DATE_EPOCH`, `-ffile-prefix-map`, pinned toolchains ("rebuildable from the tag", not bit-identical).

---

## 8. Testing

1. **Orca's C++ tests** (`tests/fff_print`: `test_print`, `test_gcode`, `test_gcodewriter`, `test_printgcode`, `test_support_material`, `test_skirt_brim`, `test_gcode_timing`, `test_fill`, `test_flow`; `test_seam_placer` and `test_multifilament` do not exist at v2.4.2; and `tests/libslic3r` minus STEP/DRC) against `libslic3r_min`, via our own test source list.
2. **Binding tests (pytest):** schema completeness; `normalize_config` round trip; `compose_config` against `--export-settings` from ~10 printers including a multi-nozzle one; cube slice; multi-object with overrides; filament assignment; paint (support enforcer/blocker regions, seam enforcer, MMU regions, `paint_out_of_range`); cancel latency per stage; `Busy`; GIL release (a Python counter thread advances during `result()`); **RSS growth under 20 MB over slices 10–50** of a repeated job, compared with the official Orca binary at the same tag; stats sums within 1 %.
3. **Golden G-code.** The oracle is the **official OrcaSlicer release binary at the pinned tag**, run as a CLI (no source build). ~20 models × ~8 profiles (Bambu, Prusa, Voron/Klipper, Creality, a multi-material and a multi-nozzle printer). Normalize header, timestamp, version lines and config-block order. Target: identical; diffs traced to compiler or dependency differences are allowlisted with an explanation; any other diff blocks release. Painted inputs reach the CLI through project 3MFs exported by `slicewright_engine._testing`. The §5.9 glue is checked field by field against the official binary's `.gcode.3mf`.
4. **Determinism:** the same job with 1 and N threads gives identical G-code, or the differing features are documented.
5. **Benchmarks** (tracked): Benchy, a 2M-triangle sculpt, a 12-object plate with tree supports, the 10M-move reference. Wall time, peak RSS, cancel latency, K.

---

## 9. Open questions

1. ~~Exact stable Orca tag to pin~~ **Resolved: v2.4.2** (M1-B layer 1). Whether its profiles match the add-on's expectations is still open (M3).
2. Windows GMP/MPFR: build from source or vendor DLLs (spike (c)).
3. Support enforcers with `enable_support = 0` (M5).

Risks are in the plan's risk register.

---

## 10. Re-verification at v2.4.2 (M1-B layer 1)

Every Orca file:line reference in this document was re-checked against v2.4.2 (`8500fcdc`) on 2026-10-09. Of 88 references: **2 still valid, 76 moved, 10 gone**. Paths are under `src/libslic3r/` unless they start with `src/`, `deps/` or `tests/`, or are `CMakeLists.txt`/`OrcaSlicer.cpp` (root and `src/`). The body of this document already carries the new values; this table is the audit trail.

### 10.1 Assumptions that did not survive the move from the dev tip

| # | Design assumed (dev tip) | At v2.4.2 | Consequence |
|---|---|---|---|
| 1 | `Print::validate` takes `std::vector<StringObjectException>* warnings` (§5.5) | One `StringObjectException* warning` (`Print.hpp:935`) | The engine can surface at most one Orca warning per validate pass unless a validate patch (proposed 0009) is carried; affects the 04 `Issue` list for warnings. Decide in M2 |
| 2 | CGAL 6.2.1 (§3.4) | **CGAL 5.6.3** (`deps/CGAL/CGAL.cmake:10-11`) | We build 5.6.3. Golden parity is with 5.6.3. Do not copy dev-tip CGAL API assumptions |
| 3 | Paint extruders up to `Extruder32` (§5.3) | `ExtruderMax = Extruder16` (`TriangleSelector.hpp:13-37`) | MMU paint above 16 filaments is unrepresentable; `paint_out_of_range` must use min(`filament_count`, 16). v1 target printers are within this |
| 4 | Orca root requires an exact bundled Python and has `SLIC3R_CAD` (§3.1) | Neither exists; OCCT is unconditional in `libslic3r/CMakeLists.txt:541-543` | Patch 0001 must gate the OpenCASCADE, draco, OpenCV and OpenSSL finds itself; there is no CAD switch to reuse |
| 5 | STEP.hpp is pulled in by `Model.hpp`, `Print.cpp`, `GCode.cpp`, `Model.cpp` (§3.2) | Only `Model.hpp:26` | Patch 0002 is smaller than planned; `Print.cpp` and `GCode.cpp` need no edit |
| 6 | Assimp, textured-OBJ (`TexturePainting`, `TextureToColor`) and `CAD/*` need trimming | Not present; OpenCV is used only by `ObjColorUtils`, which libslic3r never calls | Patch 0003 shrinks to STEP, SVG, DRC and ModelIO guards |
| 7 | deps unconditionally include python3, wxInspector, FFMPEG, Assimp, DataChannel (§3.2) | Not present; `_dep_list` is `:418-440` | Patch 0007 targets a smaller surface. `dep_JPEG` is **not** in Orca's own `_dep_list` and `dep_Qhull` is skipped on MSVC; our list names JPEG and omits Qhull (in-tree `deps_src/qhull`) |
| 8 | libslic3r needs clipper2 only (§3.1) | Also links clipper v1 and glu-libtess (`CMakeLists.txt:591-594`) | Our root adds `deps_src/clipper` and `deps_src/glu-libtess` |
| 9 | macOS Orca default 12.0 (§3.4) | 11.3 (`CMakeLists.txt:55-57`) | Still above Blender's 11.2: we must pass `CMAKE_OSX_DEPLOYMENT_TARGET=11.2` for both deps and libslic3r and check 11.3-only APIs |
| 10 | `resources/filament_mixing/` exists (§4.2, §7.1) | Absent; `info/` and `flush/` only | Package data and `set_resources_dir` need no `filament_mixing` |
| 11 | Profiles: 14,102 files, 24.1 MB, ~1.6 MB zipped (§7.1) | 12,006 files, 20.9 MB raw, **8.9 MB** with per-file deflate | Wheel size estimate rises by about 7 MB until measured with a better codec in M7 |
| 12 | `test_seam_placer`, `test_multifilament`, `tests/fff_print/test_helpers.cpp` (§5, §8) | Not present; helpers are `test_data.cpp` | The Catch2 subset list is adjusted (§8) |
| 13 | `GCodeProcessor.cpp:1932` documents per-move durations | Comment gone; `time[mode] = block_time` still holds (`:472`) | None; keep the golden check |
| 14 | Tab layout: 625 option lines, 222 toggles (§6) | 585 and 181 | Same generator approach; smaller overrides file |

Still true at v2.4.2: OpenVDB is a hard `FATAL_ERROR` in the root (`:759-766`) but `OpenVDBUtils.cpp` is already optional in libslic3r; Hollowing still needs the stub; the dependency versions other than CGAL (Boost 1.84, oneTBB 2021.5, Eigen 5.0.1, NLopt 2.5.0, libjpeg-turbo 3.0.1, GMP 6.2.1, MPFR 4.2.2); GMP/MPFR prebuilt DLLs on MSVC; `Model::need_backup` default; single `OrcaSlicer.cpp` per-plate flow with PartPlateList glue.

### 10.2 Reference table

| File | Doc reference (dev tip) | v2.4.2 | Status |
|---|---|---|---|
| `CMakeLists.txt` | :990 exact bundled Python | no such requirement (dev-tip only) | gone |
| `CMakeLists.txt` | :1075-1082 OpenVDB FATAL_ERROR | :759-766 | moved |
| `CMakeLists.txt` | :113 `SLIC3R_CAD` option | option does not exist; OCCT is unconditional | gone |
| `CMakeLists.txt` | :59-61 macOS target 12.0 | :55-57, value is 11.3 | moved |
| `libslic3r/CMakeLists.txt` | :662 find OpenCASCADE | :541-543 | moved |
| `libslic3r/CMakeLists.txt` | :610 find OpenCV | :500 | moved |
| `libslic3r/CMakeLists.txt` | :742-743 opencv_world, assimp | :584 (opencv_world); assimp absent | moved |
| `libslic3r/CMakeLists.txt` | :667 find Draco | :546 | moved |
| `libslic3r/CMakeLists.txt` | :767-779 FreeType | :609-611 | moved |
| `libslic3r/CMakeLists.txt` | :586-592 ModelIO | :479-483, :617-621 | moved |
| `libslic3r/CMakeLists.txt` | :102-103 calib.cpp | :87-88 | moved |
| `Format/svg.cpp` | :14-38 OCCT includes | :13-22 | moved |
| `Model.hpp` | :29 STEP.hpp include | :26 | moved |
| `Print.cpp` | :106 STEP.hpp include | no STEP include at v2.4.2 | gone |
| `GCode.cpp` | :143 STEP.hpp include | no STEP include at v2.4.2 | gone |
| `Model.cpp` | :3 STEP.hpp include | no STEP include at v2.4.2 | gone |
| `Model.cpp` | :34 svg.hpp include | :16 | moved |
| `Model.cpp` | :91 ModelIO.hpp include | :44-46 | moved |
| `SLA/Hollowing.cpp` | :20 OpenVDBUtils include | :4 | moved |
| `SLA/Hollowing.cpp` | :46-47 grid members | :27-28 | moved |
| `SLA/Hollowing.cpp` | :93 mesh_to_grid | :74 | moved |
| `SLA/Hollowing.cpp` | :107 redistance_grid | :88 | moved |
| `SLA/Hollowing.cpp` | :115 grid_to_mesh | :96 | moved |
| `SLAPrintSteps.cpp` | :165 generate_interior | :136 | moved |
| `SLAPrintSteps.cpp` | :390 hollow_mesh | :361 | moved |
| `TreeSupport3D.cpp` | :85 OpenVDB legacy macro | `Support/TreeSupport3D.cpp:44-50` (macro defined to 1) | moved |
| `utils.cpp` | :39 openssl/md5.h include | no include; `Utils.hpp:18` carries it | gone |
| `utils.cpp` | :1903-1920 MD5 function | :1600-1617 | moved |
| `Format/bbs_3mf.cpp` | :91 md5 include | :40 | moved |
| `Format/bbs_3mf.cpp` | :6651 MD5_CTX | :6383 | moved |
| `Utils.hpp` | :35 openssl include | :18 | moved |
| `AppConfig.cpp` | :51 boost md5 include | :33 | moved |
| `AppConfig.cpp` | :730-737 boost md5 use | :616-623 | moved |
| `FlushVolCalc.cpp` | :6 ColorSpaceConvert include | :3 | moved |
| `FlushVolCalc.cpp` | :78-79 RGB2HSV | :76-77 | moved |
| `MeshBoolean.cpp` | :68 mcut include | :30 | moved |
| `slic3r/GUI/Plater.cpp` | :19601 mcut CSG | :15151 | moved |
| `PrintObjectSlice.cpp` | :497-504 negative-volume clipping | :427-434 | moved |
| `deps/CMakeLists.txt` | :466-498 unconditional includes | :337-450, different content (no python3/wxInspector/FFMPEG/Assimp/DataChannel) | moved |
| `tests/libslic3r/CMakeLists.txt` | :25 test_drc.cpp | not present | gone |
| `tests/libslic3r/CMakeLists.txt` | :49 test_step.cpp | not present | gone |
| `tests/libslic3r/CMakeLists.txt` | :90 draco link | not present | gone |
| `deps/GMP/GMP.cmake` | :8-21 prebuilt DLLs on MSVC | :8-20 | valid |
| `deps/MPFR/MPFR.cmake` | :3-17 prebuilt DLLs on MSVC | :3-17 | valid |
| `PrintBase.hpp` | :487 set_status_callback | :479 | moved |
| `PrintBase.hpp` | :509 cancel() | :501 | moved |
| `PrintBase.hpp` | :37-45 StringObjectException | :30-38 | moved |
| `GCode.cpp` | :2610 s_IsBBLPrinter | :2047 | moved |
| `Print.cpp` | :5298 s_IsBBLPrinter | :3761 | moved |
| `GCode/WipeTower.cpp` | :742 s_IsBBLPrinter | :535 | moved |
| `GCode/WipeTower2.cpp` | :358 s_IsBBLPrinter | :582 | moved |
| `Print.cpp` | :4145 function-static cache | :3080 | moved |
| `FlushVolPredictor.cpp` | :320 static predictors | :310 | moved |
| `OrcaSlicer.cpp` | :6778-7060 per-plate path | :5978-6227 | moved |
| `PrintConfig.hpp` | :811-922 handle_legacy | :566-654 | moved |
| `slic3r/GUI/Plater.cpp` | :11355 construct_full_config | :7983, :17493 via `full_config(false, f_maps)` | moved |
| `Print.cpp` | :4231 recompose per extruder | :3166 (`update_filament_maps_to_config`) | moved |
| `PresetBundle.cpp` | :280 null deref | :154-157 | moved |
| `PresetBundle.cpp` | :163-280 variant collapse | :68-190 | moved |
| `OrcaSlicer.cpp` | :6828-6840 filament_map, nozzle_volume_type | :6027-6040 | moved |
| `OrcaSlicer.cpp` | :6847-6860 is_BBL_printer | :6046-6059 | moved |
| `PlaceholderParser.hpp` | :77 evaluate_boolean_expression | :67 | moved |
| `TriangleMesh.cpp` | :79-88 ctor, no repair | :60-75 | moved |
| `MultiMaterialSegmentation.cpp` | :2265 facet states | :2198 | moved |
| `MultiMaterialSegmentation.cpp` | :1905-1918 out-of-range extruders | :1839-1852 | moved |
| `ModelArrange.hpp` | :39 arrange_objects | :33 | moved |
| `OrcaSlicer.cpp` | :5359, :5391 preprocess_exclude_areas | :4761, :4789 | moved |
| `Print.hpp` | :1112 Print::validate | :935, **signature changed** (single warning) | moved |
| `GCode.cpp` | :2655-2796 processor and result | :2082-2178 | moved |
| `GCode.cpp` | :2605 early return | :2042 | moved |
| `GCode.cpp` | :3833 Bambu thumbnails | :2644-2659 | moved |
| `GCode/GCodeProcessor.cpp` | :7236 extruder_id is the filament | :5676 | moved |
| `GCode/GCodeProcessor.cpp` | :543 time is a duration | :472 | moved |
| `GCode/GCodeProcessor.cpp` | :1932 duration comment | comment gone; behaviour unchanged at :472 | gone |
| `slic3r/GUI/GCodeViewer.cpp` | :1732 per-type time sums | :1326 | moved |
| `OrcaSlicer.cpp` | :4234 PartPlateList | :3705 | moved |
| `OrcaSlicer.cpp` | :7190 store_to_3mf_structure | :6351 | moved |
| `slic3r/GUI/PartPlate.cpp` | :6501-6600 PlateData filling | :6141-6228 | moved |
| `OrcaSlicer.cpp` | :6946-6947 setExtruderParams | :6138-6139 | moved |
| `Brim.cpp` | :161 extruderParamsMap | :138 | moved |
| `OrcaSlicer.cpp` | :6787-6820 set_extruder_filament_info | :5987-6023 | moved |
| `Config.hpp` | :2282+ ConfigOptionDef | :2204+ | moved |
| `Preset.hpp` | :446-449 option lists | :399-402 | moved |
| `PrintConfig.hpp` | :973-976 *_with_variant | :684-687 | moved |
| `slic3r/GUI/Tab.cpp` | :2790 TabPrint::build | :2311 | moved |
| `slic3r/GUI/Tab.cpp` | :4510 TabFilament::build | :3892 | moved |
| `slic3r/GUI/Tab.cpp` | :5195 TabPrinter::build | :4437 | moved |
| `Emboss.cpp` | :34 imstb_truetype include | :12 | moved |
