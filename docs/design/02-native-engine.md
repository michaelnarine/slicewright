# Design 02: Native engine (`<engine>`)

Status: draft for review, 2026-10-09. Owner: engine track.
Inputs: a source read of OrcaSlicer at `e72ace16` (2.5.0-dev tip, 2026-10-09) and a local Blender 5.1.2 install.
Notation: **[V]** verified in the Orca source at that commit (file:line) or in Blender 5.1.2. **[U]** unverified; confirmed in the milestone named. **All Orca file:line references are at the 2.5.0-dev tip and are re-verified at the pinned stable release tag in M1-B layer 1.**

> **Interface:** [04-engine-api.md](04-engine-api.md) is authoritative; where this document differs, 04 wins. Layout and distribution: [01](01-architecture-overview.md). Schedule and risks: [the plan](../implementation/plan.md). Licensing: [compliance.md](../publishing/compliance.md).

---

## 0. Summary of decisions

| Topic | Decision |
|---|---|
| Engine form | `<engine>`, built from `engine/` (AGPL-3.0-only) and published to PyPI as a self-contained wheel per platform. The add-on bundles that wheel unmodified. |
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

- **Pin a stable release tag, not dev.** This design was read at a 2.5.0-dev commit. M1-B layer 1 pins the newest stable tag, re-verifies every file:line reference here, and records the commit in `sc.version()`. Profiles come from the same tag.
- **Applying patches.** Configure copies the submodule into the build tree and runs `git apply --3way patches/orca/*.patch`, failing on any rejected hunk. The pristine submodule is what reviewers and the source tarball point at; the patch files are the AGPL §5a modification record.
- **Keeping patches small.** Prefer excluding files in CMake and adding stub translation units in our repo over editing Orca sources. Edit Orca code only for include guards and `#if SLIC3R_HAS_X` around call sites. One purpose per patch.
- **Upstreaming.** A `SLIC3R_HEADLESS_MINIMAL` option and moving `Format/STEP.hpp` out of `Model.hpp` are reasonable upstream PRs.
- **Rebase** (per Orca release): bump, apply and fix conflicts, rebuild deps only if `deps/*` changed, run the golden suite against the official release binary of the new tag, regenerate schema and tab layout and diff them. Budget 1–3 days per minor release.

---

## 3. Build system

### 3.1 Why not Orca's top-level CMake
Orca's root `CMakeLists.txt` unconditionally runs `find_package` for Boost, Eigen, TBB, OpenSSL, CURL, FreeType, ZLIB, EXPAT, PNG, **OpenGL, glfw3**, cereal and NLopt, requires an exact bundled Python (`:990`), and **fails hard without OpenVDB** (`:1075-1082`) [V]. `SLIC3R_CAD` defaults to ON (`:113`). Patching that is more churn than a ~300-line root of our own that:

1. defines the interface targets `libslic3r/CMakeLists.txt` expects (`boost_libs`, `cereal::cereal`, `TBB::tbb`, `Eigen3::Eigen`, `NLopt::nlopt`, `noise::noise`, `PNG::PNG`, `JPEG::JPEG`, `ZLIB::ZLIB`, `EXPAT`);
2. adds only the needed `deps_src/` directories: admesh, clipper2, libigl, libnest2d, miniz, qhull, qoi, semver, nanosvg, fast_float, nlohmann, earcut, agg (if referenced), and imgui *headers only* (`imstb_truetype.h` for `Emboss.cpp:34`);
3. `add_subdirectory(src/libslic3r)` with `SLIC3R_HEADLESS_MINIMAL=ON` and `SLIC3R_CAD=OFF`.

### 3.2 Trimming inventory

Where each unwanted dependency enters libslic3r, and how it is removed [V at the 2.5.0-dev tip]:

| Dependency | Where it enters | Removal |
|---|---|---|
| **OCCT** | `find_package(OpenCASCADE REQUIRED)` at `libslic3r/CMakeLists.txt:662`. `Format/STEP.*`, `Format/svg.cpp:14-38`, `Shape/TextShape.cpp`, `CAD/*`. `Model.hpp:29`, `Print.cpp:106`, `GCode.cpp:143` and `Model.cpp:3` include `STEP.hpp` (XCAF headers); `Model.cpp:34` includes `svg.hpp` | CMake option drops the `find_package`. A forward-declaring `STEP_fwd.hpp` replaces the header includes. Guard `Model::read_from_step` and the STEP/SVG branches of `read_from_file`. Exclude STEP, svg, TextShape, CAD |
| **OpenCV** | `find_package(OpenCV REQUIRED)` at `:610`; `opencv_world` on the link line. Used by `ObjColorUtils`, `TexturePainting.cpp`, `TextureToColor/*` | Drop the `find_package` and link item; exclude the sources; guard textured-OBJ branches in `Model::read_from_file` |
| **Assimp** | `Format/AssimpImport.cpp`; `assimp` linked PUBLIC (`:742-743`) | Exclude, guard the call site, drop the link item |
| **Draco** | `find_package(Draco REQUIRED)` at `:667`; `draco` on the link line; `Format/DRC.cpp` (from `Model.cpp` and `AppConfig.cpp`) | Drop; exclude DRC; guard two call sites |
| **OpenVDB** | `SLA/Hollowing.cpp` uses it unconditionally (`:20`, `:46-47`, `:93`, `:107`, `:115`); `generate_interior` is called from `SLAPrintSteps.cpp:165,390`. `TreeSupport3D.cpp:85` only under a macro that is undefined in practice | Guard Hollowing's grid code; a `generate_interior` stub in `src/stubs` returns an empty interior (SLA-only path) |
| **OpenSSL** | MD5 only: `utils.cpp:39,1903-1920`, `bbs_3mf.cpp:91,6651`, and the public header `Utils.hpp:35`; `OpenSSL::Crypto` on the link line | Use `boost::uuids::detail::md5`, already used by `AppConfig.cpp:51,730-737`. Remove the `Utils.hpp:35` include and the link item. No vendored MD5 |
| **GUI header** | `FlushVolCalc.cpp:6` includes `slic3r/Utils/ColorSpaceConvert.hpp` for `RGB2HSV` (`:78-79`); its definition pulls in wx | A header-and-source stub at the same include path in `src/stubs` providing `RGB2HSV` |
| **mcut** | `MeshBoolean.cpp:68` include; `ModelObject::make_boolean` (no callers); CSG is GUI-only (`Plater.cpp:19601`); negative volumes use 2D clipping (`PrintObjectSlice.cpp:497-504`) | **Removed.** Guard the `MeshBoolean.cpp:68` include and its mcut functions behind `SLIC3R_HAS_MCUT=0`. FFF slicing never reaches it. This also closes the mcut licence question |
| **CURL, GLFW/OpenGL, SLVS, Shiny** | not in libslic3r, or only with `SLIC3R_CAD`/`SLIC3R_PROFILE` | Not configured |
| **FreeType** | only for OCCT font code (`:767-779`) | Goes with OCCT |
| **Apple ModelIO** | `Format/ModelIO.mm` (`:586-592`), `Model.cpp:91` | Exclude, guard the call site |

**Kept:** NLopt (arrange), libnoise (fuzzy skin), libjpeg-turbo/libpng/qoi (thumbnails), expat/miniz (`bbs_3mf.cpp`, which also defines `save_object_mesh` used by `Model.cpp`), CGAL + GMP/MPFR, and TBB, Boost (filesystem, system, thread, log, locale, nowide, regex, iostreams, chrono, date_time, atomic), Eigen, cereal, Clipper2, admesh, libigl, libnest2d, qhull. `calib.cpp` is compiled into libslic3r (`CMakeLists.txt:102-103`) and stays; its licence is noted in compliance.md. `SLA/*` stays compiled (with Hollowing stubbed) because `Model.hpp`, `PrintObject.cpp`, `AABBMesh.cpp` and `ContourZ.cpp` reach into SLA types.

**Outside libslic3r:**
- `deps/CMakeLists.txt:466-498` unconditionally includes OCCT, OpenCV, python3, wxInspector, OpenVDB, OpenCSG, GLFW, FFMPEG, Assimp, DataChannel and Draco. A patch makes the dependency list overridable (`-DORCA_DEPS_ONLY=…`) and guards those includes.
- `tests/libslic3r` always builds `test_step.cpp` and `test_drc.cpp` and links Draco (`:25`, `:49`, `:90`). Our CMake lists the Catch2 test sources itself, so no Orca edit is needed.

### 3.3 Patch series and estimated size

| Patch | Files (Orca) |
|---|---|
| 0001 `SLIC3R_HEADLESS_MINIMAL`: drop REQUIRED finds and link items, source exclusions | `libslic3r/CMakeLists.txt` |
| 0002 STEP decoupling | new `STEP_fwd.hpp`, `Model.hpp`, `Print.cpp`, `GCode.cpp`, `Model.cpp` |
| 0003 import guards (STEP, SVG, DRC, Assimp, textured OBJ, ModelIO) | `Model.cpp`, `AppConfig.cpp` |
| 0004 OpenVDB-free Hollowing | `SLA/Hollowing.cpp` |
| 0005 MD5 via Boost | `utils.cpp`, `bbs_3mf.cpp`, `Utils.hpp` |
| 0006 mcut removal | `MeshBoolean.cpp` (+ its CMake entry) |
| 0007 overridable deps list | `deps/CMakeLists.txt` |
| 0008 (only if chosen) GMP/MPFR from source on MSVC | `deps/GMP/GMP.cmake`, `deps/MPFR/MPFR.cmake` |

**Estimate: about 15–18 Orca files, mostly CMake, a few hundred changed lines** [U, measured in M2]. Link errors from symbols referenced by excluded files are fixed with stubs in `src/stubs/` rather than more Orca edits. Patch layers in M2 aren't green until the link layer.

### 3.4 Dependency acquisition

**Decision: reuse Orca's own `deps/` ExternalProject recipes, trimmed** (patch 0007). vcpkg and conda-forge are rejected for releases because of version drift.

- *Why:* the same versions as Orca (Boost 1.84, oneTBB 2021.5, CGAL 6.2.1, Eigen 5.0.1, NLopt 2.5, libjpeg-turbo 3.0.1, GMP 6.2.1, MPFR 4.2.2 [V]) keep golden parity achievable, and the recipes carry Orca's per-platform fixes.
- *Dep list:* `dep_Boost;dep_TBB;dep_Cereal;dep_NLopt;dep_Eigen;dep_CGAL;dep_PNG;dep_ZLIB;dep_EXPAT;dep_JPEG;dep_libnoise;dep_Qhull`; CGAL pulls in GMP and MPFR.
- *Windows GMP/MPFR:* Orca's recipes download **prebuilt DLLs** on Windows (`deps/GMP/GMP.cmake:8-21`, `deps/MPFR/MPFR.cmake:3-17`), so "everything static" does not hold there. Phase 0 spike (c) decides between (1) building them from source with MSVC and linking statically (patch 0008), or (2) vendoring the DLLs with `delvewheel` (name-mangled) and listing them, with provenance, in the licence files. Option 1 is preferred if it fits the timebox.
- *Linux:* `manylinux_2_28_x86_64` (GCC toolset 12+), deps from source in the container. glibc 2.28 matches Blender's Rocky 8 baseline [U, spike (c)].
- *macOS:* Xcode 15+, deployment target **11.2**, matching Blender 5.1.2 (`LSMinimumSystemVersion` and Mach-O `minos` are 11.2 [V]). Orca defaults to 12.0 (`CMakeLists.txt:59-61`); any 12-only API use is guarded in a patch.
- *Windows:* MSVC 2022, `/MD`, C++17.
- *Build times* [U, measured in Phase 0]: cold deps 25–45 min per platform; libslic3r (~257 .cpp) plus binding 10–20 min on 4 cores with ccache/sccache; warm CI under 25 min.

### 3.5 Symbol hygiene and runtime coexistence
What Blender 5.1.2 loads on macOS [V, `otool -L` and a loaded-image list]: `@rpath/libtbb.dylib` (**oneTBB 2022.3**). It ships `libtbbmalloc_proxy` but does **not** load it. Windows and Linux are unverified and are checked in spike (c).

So **two oneTBB runtimes coexist** in one process: ours (2021.5, static, hidden) and Blender's (2022.3, dynamic). They have separate schedulers and thread pools; that is supported but means our `set_threads` default (cores − 1) can oversubscribe while Blender is busy. Rules:
- Compile everything with `-fvisibility=hidden -fvisibility-inlines-hidden`.
- Linux: `-Wl,--exclude-libs,ALL`, a version script exporting only `PyInit_<engine>`, and `-Bsymbolic`.
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
- **Threading.** The engine thread and all TBB workers never touch the CPython API. `Print::set_status_callback` (`PrintBase.hpp:487`) fires from TBB workers, so the lambda only writes `{percent, text}` into a mutex-protected slot and appends warnings. The add-on polls from `bpy.app.timers`.
- **Cancellation.** `Print::cancel()` sets `CANCELED_BY_USER` (`PrintBase.hpp:509`); steps call `throw_if_canceled()`. Expect < 0.5 s for most stages and a few seconds in tree supports. G-code finalization and post-processing are not cancellable, so cancel has a tail at the end of a job [U, M5].
- **Exception mapping.** The engine thread catches everything and stores it: `CanceledException` → `Cancelled`; `SlicingError(s)` → `SliceError(object_name)`; validate errors (`StringObjectException`, `PrintBase.hpp:37-45`) → `ValidationError(issues)`; `BadOptionValueException`/`UnknownOptionException` → `ConfigError`; `std::bad_alloc` → `MemoryError`; anything else → `EngineError`.
- **One job per process**, enforced by a module-global mutex held from `start()` to a terminal state. Process-global state makes concurrent jobs unsafe [V]:
  - `GCodeProcessor::s_IsBBLPrinter`, written during export (`GCode.cpp:2610`, `Print.cpp:5298`) and also flipped by the wipe-tower constructors (`WipeTower.cpp:742`, `WipeTower2.cpp:358`); reset from config at every start;
  - `Model::extruderParamsMap`, `Model::printSpeedMap` and the incompatible-filaments list, set per job by the ported glue (§5.9);
  - lazily filled function-static caches (`Print.cpp:4145`, `FlushVolPredictor.cpp:320`), the Boost.Log core, and process-global `resources_dir`/`temporary_dir`.
  `config_schema()`, `eval_condition()` and `normalize_config()` read only static definitions, so they are safe during a job.
- **TBB.** A `tbb::global_control(max_allowed_parallelism, n)` wraps the job (`set_threads`).
- **Locale.** The engine thread body and `config_schema()` run under `CNumericLocalesSetter`.
- **Logging.** `set_logging_level(1)` at import; `sc.set_log(level, path)` routes Boost.Log to a file.
- **i18n.** `I18N::translate_fn` stays null (English) in v1.
- **Process-global dirs**, set once at import: resources (`info/`, `flush/`, `filament_mixing/`, ~40 KB) and `<user tmp>/<engine>/<pid>`. `Model::need_backup` defaults to false, so `save_object_mesh` writes nothing.

### 4.3 Memory and ownership
- Inputs arrive as `nb::ndarray<const float, shape<-1,3>, c_contig>` / `int32` and are copied once into `indexed_triangle_set` in `add_object`.
- Outputs are built on the engine thread into vectors owned by a `ResultStore`; each array is an `nb::ndarray<numpy>` whose capsule holds a `shared_ptr<ResultStore>`. The AoS `GCodeProcessorResult::moves` is released chunk by chunk during conversion (§5.7).
- `SliceJob` owns a `Model`, a `Print` and a `DynamicPrintConfig`; dropping a live job cancels and joins with the GIL released.

---

## 5. Mapping the API onto libslic3r

Reference flows: `tests/fff_print/test_helpers.cpp` `init_print()` + `gcode()`, and the CLI per-plate path `OrcaSlicer.cpp:6778-7060` [V].

### 5.1 Config
- `set_config(flat)`: `DynamicPrintConfig::full_print_config()`, then `set_deserialize` per key with `ForwardCompatibilitySubstitutionRule::EnableSilent` (runs `handle_legacy`, `PrintConfig.hpp:811-922`), then `handle_legacy_composite()` and `normalize_fdm`. Substitutions become issues.
- `compose_config`: build `Preset` objects from the resolved dicts and call `PresetBundle::construct_full_config(..., apply_extruder=false, filament_maps)`, **as the GUI does** (`Plater.cpp:11355`); `Print::process` recomposes per-extruder values itself (`Print.cpp:4231`). Filament keys unknown to the schema are filtered first, because they cause a null dereference at `PresetBundle.cpp:280`. This covers vector concatenation, `filament_map` and extruder-variant collapse (`update_values_to_printer_extruders`, `PresetBundle.cpp:163-280`).
- `normalize_config`: the `set_config` pipeline plus `DynamicPrintConfig::validate()`, without slicing.
- Before `apply`, mirror the CLI: `filament_map` of length `filament_count`, a default `nozzle_volume_type` (`OrcaSlicer.cpp:6828-6840`), and `is_BBL_printer` from `printer_model` before validation (`:6847-6860`).
- `eval_condition`: `set_deserialize` into a `DynamicPrintConfig`, then `PlaceholderParser::evaluate_boolean_expression` (`PlaceholderParser.hpp:77`); parse errors raise `ConfigError`.

### 5.2 Objects
```
ModelObject* o = model.add_object(); o->name = name;
TriangleMesh m(indexed_triangle_set{verts, tris});   // no repair, no reordering [V TriangleMesh.cpp:79-88]
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
The encodings map 1:1 onto `EnforcerBlockerType` (Extruder1 == 1 … Extruder32). Paint is per original triangle. The engine itself rejects `face_extruder` values above `filament_count` (`paint_out_of_range`), because Orca silently ignores them (`MultiMaterialSegmentation.cpp:2265`, `:1905-1918`). M5 pins enforcer behaviour when `enable_support = 0`.

### 5.4 Arrange
`arrange_objects(model, bed, ArrangeParams{…})` (`ModelArrange.hpp:39`). The bed is `printable_area` minus `bed_exclude_area`, with the wipe tower as an obstacle. Orca's CLI prepares both through GUI code (`preprocess_exclude_areas`, `OrcaSlicer.cpp:5359,5391`), which we port (§5.9). Returns placements (04 §4.5); `ArrangeError` when things don't fit.

### 5.5 Validate
`print.apply(model, cfg)`, then `Print::validate(std::vector<StringObjectException>* warnings, Polygons* collision_polygons, std::vector<std::pair<Polygon,float>>* height_polygons)` (`Print.hpp:1112`) plus our mesh and paint checks. Runs on the engine thread in the `validating` state, or synchronously in `validate()`. Results use the `Issue` shape (04 §2.6).

### 5.6 Process and export
On the engine thread:
1. `print.process()` (status → slot).
2. `print.export_gcode(tmp_path, &gcode_result, thumbnail_cb)`. This runs GCodeProcessor and moves its result into ours (`GCode.cpp:2655-2796`). Export returns early if the step is done and the file exists (`GCode.cpp:2605`); jobs are single-use with unique temp paths, so this never fires, but it matters for v1.1 `keep_state`. `thumbnail_cb` serves the `set_thumbnails` images; Bambu printers skip it in G-code (`GCode.cpp:3833`).
3. Collect `print_statistics`, `PrintEstimatedStatistics`, `gcode_result.warnings`, `conflict_result` and `gcode_check_result`.
4. Convert moves (§5.7), then `done`.

Profile `post_process` scripts are **not** run (Orca runs them in the GUI layer, and they execute arbitrary commands).

### 5.7 Moves → numpy
The output fields are 04 §5.2. Mapping notes:
- `filament` ← `MoveVertex.extruder_id`, which is the filament index (`GCodeProcessor.cpp:7236`); −1 → 255. `nozzle` ← the filament → nozzle map from `get_filament_maps()`.
- `time` is per-move duration (Orca stores durations, `GCodeProcessor.cpp:543,1932`); `object_id` ← `object_label_id`; `gcode_line` ← `gcode_id`; `gcode_line_ends` ← `lines_ends`.
- `layers` comes from a scan over `layer_id`, renumbered if needed to meet 04 §5.3.
- About 70 B/move; 5M moves ≈ 350 MB. Peak memory during conversion is AoS + SoA, so conversion runs in 1M-move chunks, releasing the AoS tail as it goes [U, M5].

### 5.8 Stats
The shape is 04 §5.4. Sources: `modes[i].time`; `time_by_role_s` and `time_by_move_type_s` summed natively from `moves.time`, as the GUI does (`GCodeViewer.cpp:1732`; `roles_times` no longer exists); `filament_per_extruder` from the per-extruder volume maps with diameter, density and cost; `used_filament_per_role`; `flush_per_filament_g`; change counts; `display` from `print_statistics` strings.

### 5.9 CLI/GUI glue we port
Orca's CLI produces correct output partly by calling GUI-layer code. We port that glue into `engine/src/glue/` (Orca-derived, AGPL, per the provenance rule in compliance.md). This table is the parity checklist for the golden suite:

| Glue | Orca source | Feeds | Effort |
|---|---|---|---|
| `.gcode.3mf` PlateData filling: ~15 fields Bambu firmware reads (`gcode_prediction`, `gcode_weight`, `layer_filaments`, `filament_change_sequence`, `nozzle_change_sequence`, `is_label_object_enabled`, `parse_filament_info`, …) | CLI uses GUI `PartPlateList` (`OrcaSlicer.cpp:4234`, `:7190` `store_to_3mf_structure`; `PartPlate.cpp:6501-6600`) | `write_gcode_3mf` | 2–3 ed |
| Arrange exclude areas and wipe-tower obstacle | `preprocess_exclude_areas` (`OrcaSlicer.cpp:5359,5391`) | `arrange` | 1–2 ed |
| `Model::setExtruderParams` / `setPrintSpeedTable` | `OrcaSlicer.cpp:6946-6947`; read by `Brim.cpp:161` | brim and speeds | ≤ 1 ed |
| `print->set_extruder_filament_info` | `OrcaSlicer.cpp:6787-6820` | multi-nozzle printers | ≤ 1 ed |
| `construct_full_config(..., apply_extruder=false, filament_maps)`, unknown-key filtering | `Plater.cpp:11355`, `Print.cpp:4231`, `PresetBundle.cpp:280` | `compose_config` | ≤ 1 ed |

Multi-nozzle (H2D-style) profiles compose and slice in v1, and H2D send is in v1 (03 §8.4).

### 5.10 Multi-plate and incremental slicing
v1 is single plate, origin (0, 0); `wipe_tower_x/y` index 0. `Print::apply()` already invalidates only changed steps, so keeping the `Print` alive across starts gives incremental re-slicing; that is v1.1 behind `keep_state` (mind the export early return, §5.6).

---

## 6. `config_schema()` and UI layout

`config_schema()` iterates `print_config_def.options` (`ConfigOptionDef`, `Config.hpp:2282+`) into the 04 §6.4 shape: `mode` from `comSimple..comDevelop`, `preset` from `Preset::{print,filament,printer}_options()` (`Preset.hpp:446-449`), `scope` from `PrintObjectConfig`/`PrintRegionConfig` membership, `variant` from `*_options_with_variant` (`PrintConfig.hpp:973-976`).

**Page and group layout is GUI-only**, in `slic3r/GUI/Tab.cpp` (`TabPrint::build()` `:2790`, `TabFilament` `:4510`, `TabPrinter` `:5195`). Calls are mostly regular (`add_options_page` → `new_optgroup` → `append_single_option_line("key", "wiki#anchor")`), so `tools/gen_tab_layout.py` tokenizes them into `tab_layout.json`. It **won't extract everything**: about 80 of the 625 `append_single_option_line` calls are commented out, about 40 pass `Option` objects instead of keys, and some pages are conditional on printer type. The generator therefore merges a hand-maintained override file (`tools/tab_layout_overrides.json`) for those cases and the ~63 custom widget lines. Budget **2–3 ed** plus upkeep per rebase. CI fails if any key is missing from the schema.

Visibility and enable logic (`ConfigManipulation.cpp`, 222 `toggle_field`/`toggle_line` calls) is imperative C++ and not extracted; the add-on owns a rule table (03 §2.4).

---

## 7. Packaging

### 7.1 Wheels
- **Distribution:** PyPI project `<engine>` (name open; `slicer-core` and `stratum` are taken). Avoids "Orca", "Blender" and vendor marks. `License-Expression: AGPL-3.0-only` with `License-File`s.
- **Wheels only, no sdist.** An sdist would be incomplete (the Orca submodule and dep sources) and over PyPI's 100 MB limit; corresponding source is a release tarball (compliance.md).
- **Tags:** `cp312-abi3-{macosx_11_0_arm64, win_amd64, manylinux_2_28_x86_64}`.
- **Tooling:** `scikit-build-core` + nanobind via `cibuildwheel`. Repair: `auditwheel` (Linux), `delocate` (macOS), `delvewheel` (Windows; vendors `msvcp140.dll` and, if chosen, GMP/MPFR DLLs with mangled names).
- **Package data:** `resources/{info,flush,filament_mixing}`; `profiles.zip` (Orca's `resources/profiles/**/*.json` only, 14,102 files, 24.1 MB raw, ~1.6 MB compressed [V]; one zip read via `zipfile` avoids Windows MAX_PATH limits on deep vendor paths, and excludes the strays `check_unused_setting_id.py` and `FlyingBear/error_hull_show`); `tab_layout.json`; `LICENSE`, `THIRD_PARTY_LICENSES`, `licenses/`, `NOTICE`, `SOURCE.txt`. No logos, covers, STLs, textures or fonts.
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

1. **Orca's C++ tests** (`tests/fff_print`: `test_print`, `test_gcode`, `test_support_material`, `test_seam_placer`, `test_multifilament`, `test_gcode_timing`; and `tests/libslic3r` minus STEP/DRC) against `libslic3r_min`, via our own test source list.
2. **Binding tests (pytest):** schema completeness; `normalize_config` round trip; `compose_config` against `--export-settings` from ~10 printers including a multi-nozzle one; cube slice; multi-object with overrides; filament assignment; paint (support enforcer/blocker regions, seam enforcer, MMU regions, `paint_out_of_range`); cancel latency per stage; `Busy`; GIL release (a Python counter thread advances during `result()`); **RSS growth under 20 MB over slices 10–50** of a repeated job, compared with the official Orca binary at the same tag; stats sums within 1 %.
3. **Golden G-code.** The oracle is the **official OrcaSlicer release binary at the pinned tag**, run as a CLI (no source build). ~20 models × ~8 profiles (Bambu, Prusa, Voron/Klipper, Creality, a multi-material and a multi-nozzle printer). Normalize header, timestamp, version lines and config-block order. Target: identical; diffs traced to compiler or dependency differences are allowlisted with an explanation; any other diff blocks release. Painted inputs reach the CLI through project 3MFs exported by `<engine>._testing`. The §5.9 glue is checked field by field against the official binary's `.gcode.3mf`.
4. **Determinism:** the same job with 1 and N threads gives identical G-code, or the differing features are documented.
5. **Benchmarks** (tracked): Benchy, a 2M-triangle sculpt, a 12-object plate with tree supports, the 10M-move reference. Wall time, peak RSS, cancel latency, K.

---

## 9. Open questions

1. Exact stable Orca tag to pin (M1-B layer 1), and whether its profiles match the add-on's expectations.
2. Windows GMP/MPFR: build from source or vendor DLLs (spike (c)).
3. Support enforcers with `enable_support = 0` (M5).

Risks are in the plan's risk register.
