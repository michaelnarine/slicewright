# Design 03: the Blender add-on

Status: draft v3, 2026-10-09. Add-on track.
Basis: source reading of OrcaSlicer (2.5.0-dev tip) and API checks against Blender 5.1.2 (Python 3.13.9).
Tags: **[V]** verified in Blender 5.1.2 (background mode unless noted) or measured on the Orca checkout. **[U]** unverified; most are Phase 0 spike checks. **[O]** an Orca file:line used as a **behaviour reference** only (see the provenance rule).

Names: `<Product>` is the unchosen product name; `<product>` is its Python package and extension id, and `<PRODUCT>_` the operator prefix. All come from one constant. `<engine>` is the engine package, imported as `sc` (04 §2.1).

> **Interface:** [04-engine-api.md](04-engine-api.md) is authoritative; engine calls here are illustrations. Architecture and layout: [01](01-architecture-overview.md). Schedule and risks: [the plan](../implementation/plan.md). Licensing and store policy: [compliance.md](../publishing/compliance.md).

> **Provenance rule.** Everything under `addon/` is GPL-3.0-or-later and is **implemented from this spec and its tests, never translated from Orca, BambuStudio or libvgcode C++**. Orca references below describe *observable behaviour* to match, verified by tests. Colour values and protocol facts may be used as data. Any code that is a translation of Orca code belongs in `engine/` (AGPL-3.0-only). Details: compliance.md §3.

---

## 0. Decisions at a glance

| Topic | Decision |
|---|---|
| UI home | A **"Slicer" tab in the 3D Viewport N-panel**; a scene-level **Prepare/Preview** switch in the viewport header. |
| Coordinates | **World origin = G-code origin**, world XY = bed XY, mm = BU × `scale_length` × 1000. A "millimetre scene" is offered, never required. |
| Bed | Drawn **procedurally** from `printable_area`, `bed_exclude_area`, `printable_height`. No vendor assets. |
| Profiles | Orca's profile JSON ships inside the engine wheel (`sc.profiles_archive()`). Python owns indexing, `inherits`/`include` resolution, compatibility and user presets; the engine owns composition and normalisation. |
| Settings UI | A typed `PropertyGroup` generated from `sc.config_schema()`; pages from `sc.tab_layout()`; a hand-written show/enable rule table. |
| Painting | Per-face **INT (32-bit) FACE attributes** on the base mesh, edited by edit-mode Select + Assign (baseline) or an object-mode brush. |
| Slicing | **No Python threads.** `job.start()` returns at once; a persistent `bpy.app.timers` callback polls. One job per process. |
| Preview | Vertex pulling from **per-chunk data textures** (~1M moves each) with one `draw_instanced` call per chunk; scrubbing changes uniforms and per-chunk instance ranges only. |
| Bake | To hair **Curves** (default) or a mesh, in v1. |
| Send | Stdlib only, tick-driven sockets. OctoPrint and Moonraker over REST; Bambu over LAN Developer Mode (implicit FTPS + MQTT), labelled "unsupported by Bambu". Gated on `bpy.app.online_access`; secrets in the OS keychain. |
| Distribution | Self-hosted extension repository (guaranteed) and extensions.blender.org (if moderators agree). macos-arm64, windows-x64, linux-x64. |

---

## 1. UX

### 1.1 Where the UI lives
- **N-panel tab "Slicer"** with collapsible panels: Printer, Filaments, Process, Object (active object's settings), Paint, Slice, Preview and Legend (Preview mode only), Export & Send.
- **Header** (appended to `VIEW3D_HT_header`): `[Prepare | Preview]`, a Slice button with live progress, an Export/Send menu.
- **Settings pages**: the Process panel shows a row of page buttons from `tab_layout` (Quality, Strength, Speed, Support, Multimaterial, Others), a filter field, and the selected page's groups. The same drawing code serves an `invoke_props_dialog` popup (~500 px) for per-object, per-filament and printer settings.
- **Optional "Slicer" workspace**, created in code by duplicating the current one (N-panel on the Slicer tab, solid shading, mm grid) **[U exact API]**. No `.blend` asset ships; other workspaces are never modified.

### 1.2 Workflow
1. **Printer**: a picker of vendor (72, searchable) → model (text only) → nozzle variant; "Custom printer" starts from `Custom/fdm_*_common` with an editable bed. "My printers" lives in preferences; the scene stores only the selection.
2. **Filaments**: a UIList of slots (colour, compatible preset, AMS/MMU slot), defaulting to the extruder count.
3. **Process**: a compatible dropdown seeded from `default_print_profile`; edits show a diff count and Save / Save as / Revert.
4. **Place** objects in the **"Print plate" collection**; Drop to bed, Center, Arrange (§4.6); out-of-volume objects are outlined red.
5. **Per-object settings**: filament slot and "Overrides…" (object and region scope only).
6. **Paint** (§5). 7. **Slice** (Ctrl+Shift+R; X or Esc cancels). 8. **Preview**: Up/Down and Shift+Up/Down move the top and bottom layers, Left/Right the moves within the layer, mirrored by N-panel sliders. 9. **Export / Send** (§8).

### 1.3 Bed visualisation
A `POST_VIEW` handler draws the `printable_area` polygon with a 10 mm grid, each `bed_exclude_area` hatched red, the build-volume wireframe to `printable_height`, the origin and axes, and the prime-tower footprint when it applies. The tower is positioned by an Empty named "Prime tower" whose location writes `wipe_tower_x/y` (index 0) into the project overrides; its size comes from the last result's `wipe_tower`, or a heuristic before the first slice. `bed_model`/`bed_texture` keys are ignored.

### 1.4 Units
`mm_per_BU = scale_length * 1000`. 5.1 defaults are `scale_length = 1.0`, `length_unit = 'METERS'` [V]. A notice appears when the bed would be < 0.5 BU or > 1000 BU. "Use millimetre scene" sets `scale_length=0.001`, `length_unit='MILLIMETERS'`, grid and clip distances. Panels always show mm.

---

## 2. Data model

### 2.1 Scene: `Scene.<product>`

| Property | Type | Notes |
|---|---|---|
| `printer_id`, `process_id` | String | `sys:<vendor>/<name>` or `user:<name>`, with `StringProperty(search=…)` [V]; enums over 10k presets are slow |
| `filaments` | Collection of `FilamentSlot{preset_id, color, ams_slot (-1 = auto), overrides: ConfigPG}` | Order gives filament 1..N |
| `printer_edits`, `process_edits` | Pointer → `ConfigPG` | Unsaved "modified" state |
| `mode` | Enum PREPARE/PREVIEW | |
| `preview` | Pointer → `PreviewProps` | view_type, layer_lo/hi, move_pos, role_mask (32 bools), show_travel/retracts/seams/toolchanges, model_display (HIDE/GHOST/SHOW), quality (TUBES/LINES), range log/fixed |
| `plate_collection` | Pointer(Collection) | |
| `last_cache_key` | String | Reconnects the preview cache after reload |
| `embedded_presets` | String (JSON) | Written in `save_pre` |

Runtime objects (job, arrays, GPU textures) live in a module registry keyed by `scene.session_uid` [V], never in RNA, and are rebuilt or invalidated on `load_post`, `undo_post` and `redo_post`.

### 2.2 Object and mesh
`Object.<product>`: `filament` (0 = inherit, 1..N), `overrides` (→ `ConfigPG`), `role` (PART only in v1; others reserved for v1.1 volumes). Paint lives on the **mesh** (§5), so linked duplicates share it.

### 2.3 Generated typed config PropertyGroup
At register the add-on calls `sc.config_schema()` and builds `<PRODUCT>_PG_Config` with `type(...)` and `__annotations__`; ~800 annotated properties register in **2.8 ms** [V]. If the engine fails to import, only a diagnostic panel registers. No schema snapshot ships (labels are AGPL engine data).

| Schema type | Blender prop | Serialised |
|---|---|---|
| bool | BoolProperty | `"1"`/`"0"` |
| int / float | Int/FloatProperty, soft min/max, tooltip as description, `sidetext` as unit | `%g` |
| percent | FloatProperty `subtype='PERCENTAGE'` | `"15%"` |
| floatOrPercent | StringProperty with a validator | as typed |
| enum (closed) | EnumProperty from `enum` | value |
| enum (open), string | StringProperty with `search` | raw |
| point(s), strings, G-code (`is_code`) | StringProperty; G-code edits via a Text datablock | raw |
| per-extruder / variant vectors | StringProperty plus a per-element sub-UI | Orca vector string |

One class serves three roles: the preset edit buffer (every key set); per-object overrides (a key is overridden iff `is_property_set(key)` [V]; Reset calls `property_unset`); per-filament-slot overrides. Keys that disappear in a newer engine survive as orphan ID properties; `normalize_config` maps legacy keys.

### 2.4 Settings pages, filter, modes and rules
- **Layout** from `sc.tab_layout()`. Keys missing from it fall back to schema `category` (which covers only 381 options [O]). `{"custom": name}` lines get hand-written drawers: bed shape, extruder count, per-extruder pages.
- **Modes**: simple (70), advanced (476), expert (14), develop (43, hidden) [O]; a pref filters them.
- **Filter**: a basic substring filter over label, key and tooltip on the current page. Cross-page search with jump-to-row is a cut candidate (plan §8).
- **Show/enable rules (add-on owned).** Orca's logic is imperative GUI code (`ConfigManipulation.cpp` [O]) and isn't extractable. `blender/settings_rules.py` is **our own declarative table**, written from observed behaviour: each rule is `key(s) → enabled_if / visible_if`, a Python predicate over the flat config. v1 groups: supports (`enable_support`, normal vs tree, raft, interface), infill (density 0, pattern-specific keys), walls (Arachne-only keys), ironing, brim and skirt, `spiral_mode`, prime tower and flush, fuzzy skin, cooling, printer retraction and machine limits, acceleration and jerk. About 40 rules over 150 keys; the cut line trims this to ~10 groups. Keys without a rule are always enabled. A test asserts every key named in a rule exists in the schema.

### 2.5 Storage summary

| What | Where |
|---|---|
| System presets | `sc.profiles_archive()` (zip in the engine wheel, read-only) |
| Profile index cache | `extension_path_user(__package__, path="cache", create=True)/profile-index-<orca_commit>.json` |
| User presets | `…/presets/{machine,filament,process}/<name>.json`, Orca format |
| Physical printers | AddonPreferences (name, kind, host, port, serial, TLS options), no secrets |
| Secrets | OS keychain (§8.5) |
| Project selection and edits | Scene RNA |
| Portable copy of user and modified presets | `scene.<product>.embedded_presets` (flattened, host keys stripped by allowlist) |
| Slice cache | `…/cache/slices/<key>/` |
| Recovery copies | `…/recovery/` (§6.1) |

On load, a missing user preset falls back to the embedded copy ("Save to my presets"); a missing system preset falls back through `renamed_from`, then to the embedded config.

---

## 3. Profile subsystem

### 3.1 What ships
The engine wheel carries Orca's profile JSON as one zip: 14,102 files, 24.1 MB raw [V], JSON only. Keeping it in the AGPL wheel keeps the extension's own files GPL-only and asset-free, and guarantees profile keys match the engine. **No subset, no download, no updater**: profile updates arrive only with new releases.

### 3.2 Who owns what

| Step | Owner | API |
|---|---|---|
| Vendors, models, variants; searchable index | Python | `core/profiles/index.py` over the zip |
| Resolve one preset (defaults → `inherits` → `include`s → own keys) | Python | `resolve.py` |
| Compatibility filtering | Python, conditions evaluated by the engine | `compat.py` + `sc.ConditionContext` |
| User presets: create, diff, save, import, export | Python | `user.py`, `importer.py` |
| Compose printer + process + N filaments + project overrides | Engine | `sc.compose_config` |
| Legacy keys, normalisation, per-key validation | Engine | `sc.normalize_config` |
| Final apply | Engine | `job.set_config` |

Python never pads per-variant vectors. Per-object overrides go to `add_object(config_overrides=…)`.

### 3.3 Module layout (`<product>/core/profiles/`, pure Python, no `bpy`)
`source.py` (zip access), `index.py`, `resolve.py` (LRU), `compat.py`, `models.py` (vendor → model → nozzle → concrete printer), `user.py`, `importer.py`, `compose.py` (wrapper over `compose_config` + `normalize_config`).

### 3.4 Index build without threads
Parsing every header takes **0.51 s** in CPython [V, warm cache]. `index.py` is a generator that parses ~300 files per step; a timer runs steps for ~25 ms per tick, so the build finishes in 1–3 s of wall time with a responsive UI ("Loading printer library… 43%"). The index is cached per `orca_commit` and loads in ~50 ms later. Entries hold the fields needed for picking, resolution and compatibility. Resolution is lazy (2–5 ms per preset, LRU).

### 3.5 Resolution behaviour
Behaviour to match (reference: Orca's preset loading [O `PresetBundle.cpp:7400-7470, 6790-6830`]), specified here and pinned by tests:
1. Start from schema defaults for keys whose `preset` matches the type.
2. Apply the parent chain (`inherits`, within the vendor or `OrcaFilamentLibrary`).
3. Apply each `include` in order. Includes are G-code template presets; a file without `instantiation` whose name contains "gcode" is a template.
4. Apply the preset's own keys.

Everything after that is the engine's. **Tests:** the golden profile suite compares `resolve` → `compose_config` → `normalize_config` with Orca CLI `--export-settings` for ~50 combinations (volatile keys allowlisted), plus unit cases for each rule above.

### 3.6 Compatibility behaviour
Behaviour to match (reference [O `Preset.cpp:869-948, 4131`]). For a filament or process preset P and printer M:
- **Library exclusion.** P in `OrcaFilamentLibrary` with empty `compatible_printers` is hidden if a printer-specific preset with the same alias lists M (or `M.inherits`).
- **Condition.** If `compatible_printers` is empty and a condition is set, evaluate it with `ConditionContext(M.resolved + {printer_preset, num_extruders})`. A `ConfigError` counts as compatible (as Orca does) and is logged.
- **List.** Otherwise P is compatible iff the list is empty, contains M.name, or M is a user preset whose `inherits` is listed.
- **Process.** Filaments must also pass `compatible_prints(_condition)`.
- **Vendor.** Cross-vendor matches are allowed.

Results are cached per (printer, process). **Tests:** a table of fixture presets per rule, and a sampled comparison against the compatible lists Orca shows for ~10 printers (captured by hand once per rebase).

### 3.7 Variants and defaults
The printer picker lists variants from `machine_model.nozzle_diameter`; the concrete preset matches `printer_model` + `printer_variant`. Extruder variants (`extruder_variant_list`, per-variant filament vectors) are collapsed by `compose_config`; Python exposes only a "nozzle volume type" choice where offered. New printers take `default_print_profile` and `default_materials`.

### 3.8 User presets
Save diffs the edit buffer against the resolved parent and writes `{"from":"User","inherits":parent,"name":…,"version":…, <diff>}`, the shape Orca uses, so files interchange. Duplicate, rename, delete and a diff popup with per-row revert. `print_host`, `printhost_apikey`, `printhost_cafile` are **stripped** on save, import and embed.

### 3.9 Import from OrcaSlicer / BambuStudio
On request only (`files` permission). Scans the user preset folders (`~/Library/Application Support/{OrcaSlicer,BambuStudio}/user/*/…` on macOS; `%APPDATA%\…` on Windows, `~/.config/…` and the Flatpak path on Linux **[U]**), lists found files with checkboxes, keeps `inherits` when the parent is known and otherwise imports a flattened copy with a warning. Reads the user's own data only. (Cut candidate.)

---

## 4. Mesh extraction

### 4.1 Pipeline (main thread)

```python
dg = context.evaluated_depsgraph_get()
for inst in dg.object_instances:                    # includes GN and collection instances
    ob = inst.object
    if ob.type != 'MESH' or not in_plate(inst): continue
    me = ob.to_mesh()                               # evaluated mesh; keeps generic attributes [V]
    n = len(me.loop_triangles)
    tri  = np.empty(n*3, np.int32);  me.loop_triangles.foreach_get("vertices", tri)        # [V]
    poly = np.empty(n,   np.int32);  me.loop_triangles.foreach_get("polygon_index", poly)  # [V]
    co   = np.empty(len(me.vertices)*3, np.float32); me.vertices.foreach_get("co", co)    # [V]
    M = np.asarray(inst.matrix_world, np.float64)
    v = ((co.reshape(-1,3) @ M[:3,:3].T + M[:3,3]) * mm_per_BU).astype(np.float32)
    t = tri.reshape(-1,3)
    if np.linalg.det(M[:3,:3]) < 0: t = t[:, ::-1].copy()      # keep outward winding
    face_support = attr_or_none(me, "<product>_support")[poly].astype(np.uint8)   # per-face → per-triangle
    ob.to_mesh_clear()
    job.add_object(name, v, t, extruder=extruder, config_overrides=overrides,
                   face_extruder=face_extruder, face_support=face_support, face_seam=face_seam)  # 04 §4.1
```

- **Modifiers** apply because the evaluated mesh is read. Paint attributes survive subdivision [V]; Boolean and remesh may drop them, and we warn when a painted base mesh evaluates without the attribute.
- **Instances** each become their own `add_object` (`"Bolt#3"`); v1.1 adds `add_instance`.
- Transforms are computed in float64 and stored as float32 mm. The engine copies inputs, so arrays are freed right away.
- `extruder` is 1-based (0 = default) and applies to the whole object; `ensure_on_bed=False` (what you see is what you print).

### 4.2 Coordinate system
World XY in mm is G-code XY; `printable_area` uses the same frame, so no offset is applied. Z = 0 is the bed.

### 4.3 Pre-slice checks
- **Open or non-manifold edges** (edges encoded as `min<<32|max`, `np.unique(return_counts=True)`, count ≠ 2): run **only on Slice or an explicit "Check plate" operator**, not on depsgraph updates. Reported per object with a "Select non-manifold" helper; slicing proceeds.
- **Out of volume**: XY convex hull vs `printable_area` and `bed_exclude_area`, max Z vs `printable_height`, min Z vs −0.01 mm. In Prepare mode this runs on depsgraph updates, throttled to 200 ms, for live outlines.
- **Degenerate objects** (no triangles or zero volume) are skipped with a warning.
- **Field validation**: `normalize_config(...)["errors"]` highlights invalid values before slicing.

### 4.4 Input key
Per object `blake2b(v, t, face arrays, overrides JSON, extruder)` (~10 ms per 1M triangles). Scene key = `hash(sc.version()["version"], composed config, sorted object keys)`. It drives staleness and the slice cache.

### 4.5 Large meshes
Warn above 5M triangles and suggest Decimate. Transient memory is ~50 B per triangle.

### 4.6 Arrange
A Prepare-mode operator that **moves Blender objects** with undo: build a transient `SliceJob` with convex-hull meshes, call `job.arrange(spacing_mm, allow_rotation)`, and apply each placement's `transform` as `S⁻¹·T·S·M` (04 §4.5). `ArrangeError` is reported; Arrange is disabled while the engine is `Busy`. Slicing never arranges. (Engine Arrange is a cut candidate; Drop and Center stay.)

---

## 5. Painting

### 5.1 Storage
Three **INT (32-bit) attributes on the FACE domain** of the base mesh. INT8 would be smaller, but bmesh cannot see INT8 face layers, while INT layers are exposed as `bm.faces.layers.int` [V]:

| Attribute | Values |
|---|---|
| `<product>_support` | 0 none, 1 enforce, 2 block |
| `<product>_seam` | 0 none, 1 enforce, 2 block |
| `<product>_filament` | 0 object default, 1..N slot |

They are saved in the .blend, undoable, propagate through modifiers, and map 1:1 to the engine's per-triangle arrays via `polygon_index` (cast to uint8 at extraction). Rejected: sculpt Face Sets (single purpose, conflict with sculpting) and colour attributes (vertex-paint brushes don't write FACE data).

### 5.2 Input methods
1. **Edit-mode Select + Assign (baseline).** In face select mode the panel and context menu offer Support Enforce/Block/Clear, Seam Enforce/Block/Clear and Filament ▸ 1..N, written through the bmesh INT layer. Helpers: **Select overhangs** (normal·(−Z) > cos θ, θ from `support_threshold_angle`) plus Blender's linked, grow and by-angle selection.
2. **Object-mode brush** (cut candidate). A `WorkSpaceTool` (`bpy.utils.register_tool` [V]) starting a modal operator: raycast a cached `BVHTree.FromPolygons` of the **base** mesh in world space, collect faces within a radius via a KDTree of face centres, optional smart fill under a normal-angle delta, LMB paints and Shift erases, one stroke per undo step, a `POST_PIXEL` circle for size.

v1 is **per-face only**; the UI suggests subdividing for finer boundaries. Importing Orca 3MF paint is out of scope.

### 5.3 Visual feedback
A `POST_VIEW` overlay per object draws painted faces only: enforcer green, blocker red, seam enforce cyan, seam block magenta, filament regions in slot colours. A clip-space depth bias (`gl_Position.z -= 1e-4*gl_Position.w`) replaces polygon offset, which `gpu.state` lacks [V]. It rebuilds on geometry updates, debounced to 100 ms.

---

## 6. Slicing orchestration (no Python threads)

### 6.1 Flow

```
<PRODUCT>_OT_slice.execute  (main thread)
  ├─ index ready?  flat = sc.compose_config(printer, process, filaments, project)
  ├─ report = sc.normalize_config(flat); block if report["errors"]
  ├─ extract meshes + paint (§4), pre-slice checks; key = input_key(...)
  ├─ cache hit? → load cached preview (chunked, §7.8) → mode = PREVIEW → FINISHED
  ├─ render thumbnails (§6.2); save recovery copy if due
  ├─ job = sc.SliceJob(); job.set_config(report["config"]); job.set_threads(prefs.threads)
  ├─ add_object × N; job.set_thumbnails(images)
  ├─ job.start()                         # returns at once; state "validating" (04 §4.3); may raise sc.Busy
  └─ bpy.app.timers.register(poll_job, first_interval=0.1, persistent=True)

poll_job()  (main thread, every 0.1 s)
  state, pct, msg = job.poll()
  update progress; tag_redraw header + panel
  if state in ("validating", "running", "cancelling"): return 0.1
  if state == "done": result = job.result() → start PreviewUpload and CacheWrite (chunked)
  if state == "failed": job.result() raises → typed report (ValidationError carries issues)
  if state == "cancelled": status "Cancelled"
  return None
```

- **One job per process.** A new Slice cancels the running job and waits (via the timer) for a terminal state before starting the next.
- **Timers** are registered with `persistent=True` so a file load doesn't silently drop them; a `load_pre` handler cancels any live job, and the timer finishes cleanly when the job ends.
- **Threads.** The add-on has **no `threading` usage** (CI greps for it). `set_threads` defaults to cores − 1.
- **Progress UI.** The panel uses `UILayout.progress(factor=…, type='BAR', text=…)` [V, an RNA function in 5.1] plus the stage message; the header shows `Slicing 42% · Generating supports`; `WindowManager.progress_begin/update/end` drives the cursor.
- **Cancel.** X or Esc (a pass-through modal watcher) calls `job.cancel()`. "Cancelling…" shows in `cancelling`; G-code finalization isn't cancellable, so there can be a short tail.
- **Typed exceptions** (04 §7): `Cancelled` → info; `SliceError` → select the object; `ValidationError` → jump to `opt_key` or select `object_name`, list all issues; `ConfigError` → highlight the key; `ArrangeError`, `Busy`; `MemoryError` → suggest fewer threads; `EngineError` → "Copy diagnostics".
- **Recovery copy** (pref, default on). Before `start()`, if the file is dirty and the last copy is older than 5 minutes, `bpy.ops.wm.save_as_mainfile(filepath=<extension_path_user>/recovery/<name>-<n>.blend, copy=True)`. It never touches the user's file or its path; the last three copies are kept. A native crash would take Blender down, so this is cheap insurance.

### 6.2 Thumbnails
The add-on renders thumbnails at the sizes in the `thumbnails` key (e.g. `"300x300/PNG"`) with `gpu.types.GPUOffScreen` + `draw_view3d` from a fixed iso view, before `start()`, and passes RGBA arrays to `job.set_thumbnails` (04 §4.1). The engine encodes and embeds them; the add-on never edits G-code. Bambu printers get them only inside the `.gcode.3mf`. Background mode makes none.

### 6.3 Invalidation
A persistent `depsgraph_update_post` handler sets `runtime.stale = True` when a plate object has `is_updated_geometry` or `is_updated_transform`, or scene settings change. No hashing in the handler; the key is recomputed on Slice or entering Preview. A stale preview stays visible, desaturated, with a "Preview out of date" banner. Auto re-slice (debounced 1.5 s, off by default) is a cut candidate.

### 6.4 Result cache
`cache/slices/<key>/` holds `out.gcode` (via `write_gcode`), one `.npy` per moves field, `layers.npy`, `stats.json`, `warnings.json`, `gcode_line_ends.npy`, written one field per timer tick. LRU capped by a pref (2 GB). After reopening a file, `last_cache_key` restores the preview without slicing and export uses the cached G-code. (The cross-reload part is a cut candidate.)

### 6.5 Issues
`result.warnings`, a `ValidationError`'s `issues` and our pre-checks merge into one list of `Issue` dicts `{level, code, message, opt_key, object_name}` (04 §2.6), shown in a UIList; clicking a row selects the object or opens the setting.

---

## 7. Preview renderer

Written from this spec. libvgcode (AGPL) is a behaviour reference for what a slicer preview shows, not a source to translate.

### 7.1 What the Python `gpu` API allows [V, background mode]
- **Shaders.** `GPUShaderCreateInfo` accepts `vertex_in`, `vertex_out` (smooth/flat/no_perspective), `push_constant` (incl. `UINT`, `IVEC2`, arrays), `uniform_buf` + `typedef_source`, `sampler` (`FLOAT_2D`, `UINT_2D`, `*_BUFFER`), `define`, `fragment_out`, `depth_write`. Compiling and drawing need a GUI context (Phase 0 spike (a)).
- **Vertex formats.** `attr_add` with `I8…U32, F32, I10` and fetch modes `FLOAT`, `INT`, `INT_TO_FLOAT_UNIT`.
- **Batches.** `draw`, `draw_range`, `draw_instanced(instance_start, instance_count)` (exposes `gl_InstanceID`). **`instance_count=0` means "all instances"**, so an empty range must skip the draw call. No per-instance attributes, no SSBOs.
- **Textures and buffers.** `GPUTexture(size, format, data=Buffer)` supports RGBA32F, RG32UI, RG16F, R32F and more, but is **filled whole at construction: there is no sub-region update**. `GPUVertBuf` has only whole-buffer `attr_fill`. Whether `gpu.types.Buffer` wraps numpy without a list copy is **[U]** (spike (a)).
- **State.** Depth test and mask, blend, line width (> 1 px unreliable on Metal/Vulkan), point size, clip distances. No polygon offset.

**Conclusion: vertex pulling from per-chunk textures.** Moves are split into chunks of up to C = 2²⁰ (~1M) moves; each chunk owns its own small textures, built once when its data is ready. A 6-vertex template is drawn with **one `draw_instanced` call per chunk**, and the vertex shader computes the segment from `gl_InstanceID` plus a per-chunk offset uniform (no reliance on base-instance semantics).

### 7.2 Chunk layout
Fields come from 04 §5.2. Chunk *c* covers moves `[s_c, e_c]`. Its textures hold `n_c + 1` texels at width W = 8192: **texel 0 duplicates move `s_c − 1`** (move 0 for the first chunk), and texel *j* ≥ 1 holds move `s_c − 1 + j`. Every segment's start point is therefore in the same chunk.

| Texture (per chunk) | Format | Contents | B/move |
|---|---|---|---|
| `t_pos` | RGBA32F | x, y, z, width | 16 |
| `t_meta` | RG32UI | `.r` = role (5 b) \| type (4 b) \| filament (8 b) \| nozzle (3 b) \| flags; `.g` = layer_id | 8 |
| `t_val` | RG16F | height, scalar for the active view | 4 |

28 B/move: 10M moves ≈ 280 MB VRAM. A view change rebuilds only each chunk's `t_val`. A chunk is 128 rows, far below any texture limit. The palette is a std140 UBO (`role_colors[32]`, `option_colors[16]`, `range_colors[11]`, `range_minmax`, slot colours) with the values in Appendix A.

### 7.3 Draw passes
1. **Extrusions as tubes** (default): 6-vertex template with a U8 `corner` attribute. The vertex shader fetches A (texel *j*−1), B (texel *j*) and the meta texel, rejects non-extrusions and masked roles by emitting a clipped position, and otherwise builds a view-facing ribbon of the move's width, extended by width/2 at the ends. The fragment shader reconstructs a cylinder normal and shades Lambert + specular. A "box" option uses an 8-vertex template for close-ups.
2. **Travel**: a 2-vertex `LINES` template, type == Travel, off by default.
3. **Markers** (seams, retracts, unretracts, tool/colour changes, pauses): per-chunk index lists (`np.flatnonzero(type == X)`) in R32UI textures, drawn as screen-space quads in option colours under the same ranges.
4. **Nozzle marker**: a cone at the scrub position.
5. **Lines LOD**: the extrusion pass with a `LINES` template, no shading; automatic above a visible-segment threshold (pref, default 12M) or manual.

`POST_VIEW` with `depth_test_set('LESS_EQUAL')` and `depth_mask_set(True)` should occlude against scene depth **[U]**. Alpha blending only for the ghost model and greyed-out layers.

### 7.4 Range and scrubbing
For a layer range [lo, hi] and a move position p in the top layer, the global move range is:
```
g_first = layers.first[lo]
g_last  = min(layers.first[hi] + p, layers.last[hi], uploaded_last)
```
For each chunk *c* with moves `[s_c, e_c]`:
```
first_c = max(g_first, s_c);  last_c = min(g_last, e_c)
if last_c < first_c: skip            # never draw_instanced(instance_count=0): that draws everything
u_first = first_c - s_c + 1          # texel of move first_c within the chunk
draw_instanced(batch, instance_start=0, instance_count=last_c - first_c + 1)
```
Chunks outside the range cost nothing. No buffer work happens while scrubbing. "Grey out layers below the top" is one push constant, `u_grey_below`. `uploaded_last` caps the range while chunks are still arriving, so a partial preview is drawable.

### 7.5 View modes
Scalars for `t_val`, computed in numpy on view change:

| View | Source | Palette |
|---|---|---|
| Feature type | `role` | role colours |
| Speed / actual speed | `feedrate` / `actual_feedrate` | range |
| Height, width | `height`, `width` | range |
| Volumetric flow | `mm3_per_mm × feedrate` | range |
| Fan, temperature | `fan`, `temperature` | range |
| Layer time (lin/log) | `np.add.reduceat(time[:,0], layers.first)` mapped by `layer_id` | range |
| Filament / nozzle | `filament` / `nozzle` → slot colour | UBO slot colours |
| Colour print | `color_id` → slot colour | UBO slot colours |
| Pressure advance, acceleration, jerk | same-named fields | range |

The range defaults to the 0.5–99.5 percentile; a fixed min/max can be set.

### 7.6 Source objects
In Preview, plate objects are **ghosted by our own translucent pass**; "Hide" uses `obj.hide_set(True)` and restores it on leaving Preview.

### 7.7 Legend, summary, inspector
- **Legend.** Per role: visibility toggle (a `role_mask` bit), colour chip, time (`time_by_role_s`), percentage, filament (`used_filament_per_role`). Chips are generated in `bpy.utils.previews` via `image_pixels_float` [V]. Range views show a gradient chip.
- **Summary.** Normal and silent time, filament per extruder (mm, g, cost), layer count, changes, flush, travel.
- **Move inspector** (cut candidate): position, speeds, width, height, fan, temperature, layer, `gcode_line` at the scrub position; "Show in G-code" seeks via `gcode_line_ends` and loads a ±200-line window into a Text datablock.

### 7.8 Memory, upload and budgets
- **CPU.** The engine SoA is ~70 B/move (5M ≈ 350 MB). Packing runs per chunk: the transient peak is the SoA plus one chunk's staging arrays and its `Buffer` copy (~60–90 MB at C = 1M), not a whole-print copy. After all chunks are built, only fields needed for view changes stay (float16 scalars, ~25 B/move); `position` is dropped from CPU memory.
- **Upload.** One chunk per timer tick: pack, build `Buffer`s, construct the three textures. Target ≤ 40 ms per chunk **[U, spike (a)]**.
- **VRAM budget** (pref). Defaults are conservative on integrated GPUs: 384 MB when `gpu.platform.device_type_get()` reports Intel or when Apple Silicon has ≤ 8 GB unified memory; 1 GB otherwise. Over budget, travel is dropped from the textures first, then lines LOD is forced; the user is told which.

### 7.9 Shader sketch

```glsl
// samplers: 0 FLOAT_2D t_pos, 1 UINT_2D t_meta, 2 FLOAT_2D t_val (all per chunk); UBO 0 Palette pal
// push: MAT4 ViewProjectionMatrix; VEC3 u_eye; INT u_first; INT u_grey_below;
//       UINT u_role_mask; INT u_view_mode
ivec2 tc(int j) { return ivec2(j % 8192, j / 8192); }
void main() {
  int j = u_first + gl_InstanceID;                 // texel in this chunk; j >= 1
  vec4 B = texelFetch(t_pos, tc(j), 0), A = texelFetch(t_pos, tc(j - 1), 0);
  uvec2 m = texelFetch(t_meta, tc(j), 0).rg;
  uint role = m.r & 31u, type = (m.r >> 5) & 15u; int layer = int(m.g);
  if (type != EXTRUDE || ((u_role_mask >> role) & 1u) == 0u) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return;    // rejected: clipped
  }
  /* expand ribbon from corner id; v_side for the cylinder normal; grey if layer < u_grey_below */
}
```

### 7.10 Lifecycle
One `Renderer` per scene runtime, owning the chunk textures and template batches; shaders compile lazily on first draw. Draw handlers are added in `register()` and removed in `unregister()`, and return early when idle. `load_pre` releases GPU resources and cancels any job; `load_post` reconnects through `last_cache_key`. Exceptions in handlers are logged once per session.

### 7.11 Bake to Curves / Mesh (v1)
- **Curves (default)**: a `Curves` datablock [V]; one curve per maximal run with the same role and filament and no travel (breaks via `np.flatnonzero`); point radius = width/2; a `role` attribute; one material per role (base colour from Appendix A). Renders as tubes in Cycles and EEVEE.
- **Mesh**: a hexagonal prism per segment (12 vertices); warn above ~2M segments.
- Both bake only the **visible range and roles** into a "<Product> bake" collection that slicing excludes, filled with `foreach_set` in chunks across ticks.

---

## 8. Export and send

### 8.1 Export
- **G-code**: copy the cached `out.gcode`; default name from `result.output_filename(input_basename)`.
- **`.gcode.3mf`**: `result.write_gcode_3mf(path, plate_meta)` (04 §5.1). Firmware acceptance is tested on the hardware matrix.

### 8.2 OctoPrint
Behaviour reference [O `OctoPrint.cpp`]: test with `GET /api/version` (`X-Api-Key`); upload with `POST /api/files/local` multipart (`file`, `path`, `select`, `print`).

### 8.3 Moonraker / Klipper
Behaviour reference [O `Moonraker.cpp:178-300`]: `GET /server/info`; `POST /server/files/upload` (multipart `file`, `root=gcodes`); read `result.item.path`; `POST /printer/print/start {"filename": path}`. `X-Api-Key` if configured.

### 8.4 Bambu LAN (Developer Mode only, unsupported by Bambu)
The UI labels this mode **"LAN Developer Mode — unsupported by Bambu"**. It uses only the printer's **access code** over open protocols on the local network.

**Do not**, in code, docs or support answers:
- use Bambu's cloud API or any cloud endpoint;
- bundle, load, download or emulate Bambu's network plugin;
- extract or use Bambu Connect keys or certificates;
- identify as, or imitate the client fingerprint of, Bambu Studio, Bambu Connect or Bambu Handy;
- work around, retry around or otherwise bypass the printer's authorization mechanism.

Protocols:
- **FTPS, implicit TLS, port 990**, user `bblp`, password = access code. `ftplib.FTP_TLS` is explicit-TLS only [V], so a subclass wraps the socket on connect and **reuses the TLS session on the data channel** **[U]**. Uploads `<name>.gcode.3mf` to the storage root.
- **MQTT 3.1.1 over TLS, port 8883**, same credentials; publish to `device/<serial>/request`:
  ```
  {"print":{"command":"project_file","param":"Metadata/plate_1.gcode","url":"ftp://<name>.gcode.3mf","use_ams":…,"ams_mapping":[…],"bed_leveling":…,"timelapse":…,…}}
  ```
  and subscribe to `device/<serial>/report`. Our own minimal client (CONNECT, SUBSCRIBE, PUBLISH QoS 0, PINGREQ; ~200 lines); no paho.
- **TLS trust**: trust on first use; the leaf SHA-256 is pinned per printer after the user confirms it.
- **Authorization.** Since early 2025, firmware blocks third-party control outside LAN-only mode with Developer Mode enabled (Bambu wiki: "Enable Developer Mode"; affected firmware versions unverified). On any authorization-denied reply or refused login, the client **stops** (no retry, no fallback) and shows a message pointing to Developer Mode.
- **AMS mapping** from each slot's `ams_slot` (auto = index). Multi-nozzle (H2D) profiles slice in v1, and **H2D send is in v1**: it maps AMS slots per nozzle from the engine's `nozzle` field and the filament maps, and is checked on real hardware before release.

### 8.5 Credentials
Printer records in AddonPreferences, without secrets. Secrets in the OS keychain via the stdlib: macOS `/usr/bin/security` (`-s <product> -a <printer-uuid>`), Windows `advapi32.CredWriteW/CredReadW` via `ctypes`, Linux `secret-tool` when present. Fallback: a `0600` file with an explicit "stored unencrypted" notice. No `keyring` wheel. **Secrets never enter the .blend.**

### 8.6 Async I/O, permissions, online access
- Network operations are **tick-driven state machines** on non-blocking sockets (`ssl` with `SSLWantRead/Write`, `select(…, 0)`), advanced by a timer with ~15 ms per tick, streaming multipart in 256 KB chunks. The same code runs blocking in unit tests against fake servers.
- Every network operator is user-initiated and its `poll()` requires `bpy.app.online_access` [V]; LAN printers count as network access.
- No telemetry, update checks, downloads or remote code.

---

## 9. Code structure, testing, logging

### 9.1 Package layout
```
addon/
  LICENSE                               # GPL-3.0 text
  <product>/                            # extension root
    blender_manifest.toml  __init__.py  NOTICE
    prefs.py                            # printers, secrets UI, cache, modes, About & licences
    core/          # pure Python + numpy; NO bpy
      profiles/  geometry.py  hashing.py  units.py  gcode_export.py
      preview_data.py   # moves → chunk arrays, view scalars, marker lists, bake run-splitting
      stats.py  ticking.py
      network/ (multipart nbsocket octoprint moonraker ftps_implicit mqtt_min bambu secrets)
    engine/  adapter.py (import, API check, error mapping)  jobs.py  cache.py
    blender/       # everything importing bpy
      props.py config_pg.py settings_rules.py registry.py handlers.py extract.py bed_draw.py recovery.py
      operators/  ui/  paint/  preview/ (renderer shaders glsl/ thumbnails bake)
    wheels/        # <engine>-<ver>-cp312-abi3-<platform>.whl (from PyPI, pinned + hashed)
  tools/ (fetch_wheels build_all make_repo)
  tests/ (unit blender gui contract fixtures fake_engine)
```
Registration order: props → config_pg (after engine import) → operators → ui → handlers; unregister reverses it. A test asserts no handlers, timers or draw handlers remain.

### 9.2 Fake engine (`tests/fake_engine`)
Implements all of 04, including the state machine (with `validating`), typed exceptions and `Busy`, and passes the shared contract suite (04 §11). It is the single dev backend.
- **Schema and layout** from fixtures exported by the real engine. They are AGPL data, so they live in `engine/tests/fixtures/exported/` and are never packaged in the add-on.
- **`eval_condition`**: a small evaluator (`==`, `!=`, `=~`, `and`, `or`, `not`, indexing, numeric comparisons). **`compose_config`**: naive concatenation.
- **Synthetic slicing**: bounding-box layers with walls, zigzag infill, travel, retracts and plausible times; advances on `poll()`.
- **`from_gcode(path)`**: a parser for Orca G-code in both tag dialects (plain `;TYPE:`, `;WIDTH:`, `;HEIGHT:`, `;LAYER_CHANGE`; Bambu `; FEATURE:`, `; LINE_WIDTH:`, `; LAYER_HEIGHT:`, `; CHANGE_LAYER`), relative/absolute E, G2/G3. Written from the dialect description, so real 1M–20M-move prints can drive the preview before the native module exists.

### 9.3 Testing
- **Unit** (stock Python, three OSes): resolve, compat, user presets, importer, geometry, hashing, preview packing (incl. chunk boundaries and empty ranges), stats, export naming, multipart, MQTT packets, FTPS/HTTP against fake servers, ticking.
- **Golden profiles** (engine-enabled job): §3.5.
- **Headless Blender** (`-b --factory-startup`): register hygiene, ConfigPG generation, extraction (transforms, negative scale, modifiers, instances, unit scale), paint attributes via bmesh and `foreach_set`, orchestration with the fake (whether timers fire in `-b` is **[U]**; otherwise drive `poll_job` directly), cache reconnect, recovery copies, bake counts.
- **GPU** (scripted, per platform): slice a fixture, scrub, switch views, save screenshots, compared to references with a perceptual-diff threshold.
- **CI**: `extension build --split-platforms` and `extension validate` on every build.

### 9.4 Logging
`logging.getLogger("<product>")` to the console and a rotating file under `extension_path_user(...)/logs/`; engine logs via `sc.set_log`. "Copy diagnostics" collects versions, OS, GPU backend and log tails.

---

## 10. Packaging the extension

Licences, store policy, naming, the manifest's `license`/`copyright` rationale and the Bambu legal context are in [compliance.md](../publishing/compliance.md).

### 10.1 Build pipeline
1. `tools/fetch_wheels.py` downloads the pinned `<engine>` wheels **unmodified from PyPI**, verifying hashes.
2. `blender --command extension build --split-platforms` produces `<product>-<ver>-{macos_arm64,windows_x64,linux_x64}.zip`.
3. `blender --command extension validate` on each zip, plus our checks: size, no `__pycache__`, no binary assets, SPDX headers, manifest `copyright` format (each entry starts with a year).
4. The zips are attached to a GitHub Release (`addon-vX.Y.Z`). The `gh-pages` branch holds only `index.json` and HTML from `extension server-generate`, with `archive_url`s rewritten to the Release asset URLs (zips over 100 MB can't live in git).
5. If the moderators agree, the same zips go to extensions.blender.org with the store manifest variant.

### 10.2 `blender_manifest.toml` (self-hosted variant)

```toml
schema_version = "1.0.0"
id = "<product>"
version = "0.1.0"
name = "<Product>"
tagline = "An FDM slicer for Blender: slice, preview and send"   # ≤64 chars, no end punctuation
maintainer = "<maintainer> <contact@…>"
type = "add-on"
blender_version_min = "5.1.0"
license = ["SPDX:GPL-3.0-or-later", "SPDX:AGPL-3.0-only"]
copyright = [                       # illustrative; generated from THIRD_PARTY_LICENSES; every entry starts with a year
  "2026 <Product> contributors",
  "2016-2026 OrcaSlicer contributors",
  "2011-2026 BambuStudio, PrusaSlicer and Slic3r contributors",
  "2013-2026 Ultimaker CuraEngine contributors",
  "1995-2026 CGAL, Boost, oneTBB, Eigen, GMP, MPFR and other contributors (see THIRD_PARTY_LICENSES)",
]
website = "https://<owner>.github.io/<repo>/"
tags = ["Import-Export", "Mesh", "Object"]
platforms = ["macos-arm64", "windows-x64", "linux-x64"]
wheels = [
  "./wheels/<engine>-<ver>-cp312-abi3-macosx_11_0_arm64.whl",
  "./wheels/<engine>-<ver>-cp312-abi3-win_amd64.whl",
  "./wheels/<engine>-<ver>-cp312-abi3-manylinux_2_28_x86_64.whl",
]

[permissions]
files   = "Import slicer presets, save presets and export G-code"
network = "Send G-code to OctoPrint, Klipper or Bambu printers on LAN"

[build]
paths_exclude_pattern = ["__pycache__/", "*.pyc", "/tests/", "/tools/", "/.git*"]
```

Permission keys are limited to `files`, `network`, `clipboard`, `camera`, `microphone`, with terse values and no trailing period [V `blender_ext.py:1799-1835`]. The store variant (GPL-only `license` plus a disclosure sentence, used only with moderator consent) is specified in compliance.md §8.

### 10.3 Size budget per platform zip
Add-on Python ≤ 1 MB; engine wheel ≤ 60 MB (estimate 12–25 MB); **zip ≤ 150 MB hard cap, ≤ 65 MB target**. The store's upload limit is about 200 MB.

### 10.4 About & licences panel
A box in AddonPreferences, also reachable from the N-panel "ⓘ" button, shows (requirements: compliance.md §6):
- add-on version; engine `version`, `orca_tag`/`orca_commit`, `patches`, build info;
- **"Open engine source"** (`source_url` at the exact tag) and the source-tarball link;
- a licence summary: add-on GPL-3.0-or-later, engine AGPL-3.0-only, combined work under both;
- the **no-warranty** statement;
- copyright notices of the LGPL libraries (GMP, MPFR, libnest2d, libnoise) and the main upstreams;
- a note that AGPL §13 obliges only those who run a *modified* engine for remote users;
- "View licences", opening `sc.licenses()` texts and our `LICENSE` as temporary Text datablocks, never saved into the user's file;
- non-affiliation with OrcaSlicer, Bambu Lab, Prusa Research, the Blender Foundation and other vendors.

---

## 11. Open questions
- One plate per scene in v1 (proposed: yes).
- Modifier and negative volumes in v1.1, once the engine exposes them.
- Risks are in the plan's risk register.

---

## Appendix A. Preview colours

Values match Orca's preview so users see familiar colours (data, not code; reference [O `libvgcode/src/ViewerImpl.cpp:295-319`, `ColorRange.hpp:17`]).

| Role | Hex | Role | Hex |
|---|---|---|---|
| None | #E6B3B3 | Gap infill | #FFFFFF |
| Inner wall | #FFE64D | Skirt | #00876E |
| Outer wall | #FF7D38 | Support | #00FF00 |
| Overhang wall | #1F1FFF | Support interface | #008000 |
| Sparse infill | #B03029 | Prime tower | #B3E3AB |
| Internal solid infill | #9654CC | Custom | #5ED194 |
| Top surface | #F04040 | Bottom surface | #665CC7 |
| Ironing | #FF8C69 | Internal bridge | #4D80BA |
| Bridge | #4D80BA | Brim | #003B6E |
| Support transition | #004000 | Mixed | #808080 |

Options: Travel #38489B, Wipe #FFFF00, Retract #CD22D6, Unretract #49ADCF, Seam #E6E6E6, Tool change #C1BE63, Colour change #DA948B, Pause #52F083, Custom G-code #E2D243.
Range, low → high: `#0B2C7A #135985 #1C8891 #04D60F #AAF200 #FCF903 #F5CE0A #E38820 #D16830 #C2523C #942616`. Layer time has linear and logarithmic modes.
