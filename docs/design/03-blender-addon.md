# Design 03: the Blender add-on

Status: draft v3, 2026-10-09. Add-on track.
Basis: source reading of OrcaSlicer (2.5.0-dev tip) and API checks against Blender 5.1.2 (Python 3.13.9).
Tags: **[V]** verified in Blender 5.1.2 (background mode unless noted) or measured on the Orca checkout. **[U]** unverified; most are Phase 0 spike checks. **[O]** an Orca file:line used as a **behaviour reference** only (see the provenance rule).

Names: `Slicewright` is the product name; `slicewright` is its Python package and extension id, and `SLICEWRIGHT_` the operator prefix. All come from one constant. `slicewright_engine` is the engine package, imported as `sc` (04 §2.1).

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
| Bake | To hair **Curves** (default) or a mesh, in v1; plain data in a `Slicewright Bake` collection, one object per source (03 §7.11). |
| Send | Stdlib only, tick-driven sockets. OctoPrint and Moonraker over REST; Bambu over LAN Developer Mode (implicit FTPS + MQTT), labelled "unsupported by Bambu". Gated on `bpy.app.online_access`; secrets in the OS keychain. |
| Distribution | Self-hosted extension repository (guaranteed) and extensions.blender.org (if moderators agree). macos-arm64, windows-x64, linux-x64. |

---

## 1. UX

### 1.1 Where the UI lives
- **N-panel tab "Slicer"** with collapsible panels, in workflow order (`names.PANEL_ORDER`; every panel sets `bl_order = panel_order(key)`, so the order is Blender's own and does not depend on which module registers first; `check_registration.py` asserts it, and the screenshot tool reads the same `bl_order` when it re-registers panels). Top to bottom: **Slice** button and **Mode** row (headerless), **Plate**, **Printer and materials** (printer, process, filaments), **Settings**, **Object settings**, **Paint**, **Preview** (Preview mode only), **Issues**, **Export**, **Bake**, **Send**, and the engine diagnostics panel last. *Open by default* (what a first-time user needs): Plate, Printer and materials, Preview, Issues and Export (the last two are one or two lines until there is something to say); *closed by default*: Settings, Object settings, Paint, Bake, Send and the engine panel. Blender keeps a user's own open/closed choice per panel.
- **Header** (appended to `VIEW3D_HT_header`): `[Prepare | Preview]`, a Slice button with live progress, an Export/Send menu.
- **Prepare / Preview switch (implemented, `blender/mode.py`, `core/mode_rules.py`).** The `[Prepare | Preview]` row is appended to `VIEW3D_HT_header` (`mode.install_header`, removed again on unregister) and sits as a header-less "Mode" panel right under the Slice button in the Slicer tab; both are two operators, `slicewright.mode_prepare` and `slicewright.mode_preview`, with the current mode drawn pressed (`depress`). Preview is greyed with the tooltip "Slice the plate to preview it" while the scene holds no slice result (a loaded or loading preview); Prepare is always possible. The Slice and Export/Send entries are *not* in the header yet (the Slice panel and Export panel carry them). Rules, decided here: (1) a slice that finishes (also from the cache) switches to Preview, as before; nothing else switches by itself. (2) Editing the plate or a setting after a slice does **not** flip the mode: the preview stays, flagged "Preview out of date" (§6.3). (3) Things that only make sense in Prepare are greyed in Preview with the tooltip "Switch to Prepare to do this" (`mode.require_prepare` in their `poll`): Arrange, the object-mode paint brush, the edit-mode Assign and Select overhangs buttons and Clear all paint (their overlay is not drawn in Preview, so painting there would be blind); the Paint panel says so. Drop to bed and Center stay available (they move objects and make the preview stale). (4) The Preview panel and the preview arrow keys (`preview_step`) exist only in Preview: in Prepare nothing of the preview is drawn and the keys stay Blender's. (5) The red out-of-volume outlines and the Plate panel's "Outside the build volume" box are Prepare-only (`volume.shown`); returning to Prepare marks them and the paint overlay dirty and recomputes at once, through the `mode` property's update callback (so a script's `sw.mode = ...` does the same). (6) On file load the saved mode follows what comes back (`mode_rules.mode_after_load`): a file saved in Preview opens in Preview while its cached slice loads, and in Prepare when there is nothing to load (never sliced, cache entry gone, cache entry unreadable). The preview itself (`preview/runtime.py`) is released on `load_pre`, so no preview of the old file survives. Not implemented: the "ghost / hide source objects" display of §7.6 (`model_display`); plate objects are not hidden or restored when switching.
- **Settings pages**: the Process panel shows a row of page buttons from `tab_layout` (Quality, Strength, Speed, Support, Multimaterial, Others), a filter field, and the selected page's groups. The same drawing code serves an `invoke_props_dialog` popup (~500 px) for per-object, per-filament and printer settings.
- **Optional "Slicer" workspace**, created in code by duplicating the current one (N-panel on the Slicer tab, solid shading, mm grid) (`ops.workspace.duplicate()` plus rename [V]; `bpy.data.workspaces` has no `new()`). No `.blend` asset ships; other workspaces are never modified.

### 1.2 Workflow
1. **Printer**: a picker of vendor (72, searchable) → model (text only) → nozzle variant; "Custom printer" starts from `Custom/fdm_*_common` with an editable bed. "My printers" lives in preferences; the scene stores only the selection.
2. **Filaments**: a UIList of slots (colour, compatible preset, AMS/MMU slot), defaulting to the extruder count.
3. **Process**: a compatible dropdown seeded from `default_print_profile`; edits show a diff count and Save / Save as / Revert.
4. **Place** objects in the **"Print plate" collection**; Drop to bed, Center, Arrange (§4.6); out-of-volume objects are outlined red.
5. **Per-object settings**: filament slot and the object's setting overrides (object and region scope only), in the "Slicewright object settings" panel (§2.2).
6. **Paint** (§5). 7. **Slice** (Ctrl+Shift+R; Esc or a right click cancels while it runs, in the operator's modal watcher; X is not bound because it deletes in the viewport). 8. **Preview**: Up/Down and Shift+Up/Down move the top and bottom layers, Left/Right the moves within the layer, mirrored by N-panel sliders. 9. **Export / Send** (§8).

### 1.3 Bed visualisation
A `POST_VIEW` handler draws the `printable_area` polygon with a 10 mm grid, the `bed_exclude_area` polygon (a single polygon; empty means none) hatched red, the build-volume wireframe to `printable_height`, the origin and axes, and the prime-tower footprint when it applies. The tower is positioned by an Empty named "Prime tower" whose location writes `wipe_tower_x/y` (index 0) into the project overrides; its size comes from the last result's `wipe_tower`, or a heuristic before the first slice. `bed_model`/`bed_texture` keys are ignored.

The three bed keys are read from the printer in use (the printer edit buffer, §2.3: the selected preset plus unsaved edits), through one function, `blender/bed_source.current_bed`; the scene has no bed properties of its own, so the bed is changed in the printer settings. With no printer selected the buffer holds the engine schema defaults (Orca's `print_config_def`: a 200 × 200 mm bed, 100 mm tall, nothing excluded); an unusable `printable_area` (under three points) gives no bed, and Center, Arrange and Check plate report an error.

### 1.4 Units
`mm_per_BU = scale_length * 1000`. 5.1 defaults are `scale_length = 1.0`, `length_unit = 'METERS'` [V]. A notice appears when the bed would be < 0.5 BU or > 1000 BU. "Use millimetre scene" sets `scale_length=0.001`, `length_unit='MILLIMETERS'`, grid and clip distances. Panels always show mm.

---

## 2. Data model

### 2.1 Scene: `Scene.slicewright`

| Property | Type | Notes |
|---|---|---|
| `printer_id`, `process_id` | String | `sys:<vendor>/<name>` or `user:<name>`, with `StringProperty(search=…)` [V]; enums over 10k presets are slow |
| `filaments` | Collection of `FilamentSlot{preset_id, color, ams_slot (-1 = auto), overrides: ConfigPG}` | Order gives filament 1..N |
| `printer_edits`, `process_edits` | Pointer → `ConfigPG` | Unsaved "modified" state |
| `mode` | Enum PREPARE/PREVIEW | Set to PREVIEW by a finished slice; switched by the user (§1.1); `update=` recomputes the Prepare overlays; reset to PREPARE on `load_post` when there is no preview to restore |
| `preview` | Pointer → `PreviewProps` | view_type, layer_lo/hi, move_pos, role_mask (32 bools), show_travel/retracts/seams/toolchanges, model_display (HIDE/GHOST/SHOW), quality (TUBES/LINES), range log/fixed |
| `plate_collection` | Pointer(Collection) | |
| `last_cache_key` | String | Reconnects the preview cache after reload (saved) |
| `job_state`, `job_progress`, `job_message` | Enum `idle`/`validating`/`slicing`/`done`/`failed`/`cancelled`, Float 0-1, String | Slice job status for the panel and header. Not meant to persist: Blender's `SKIP_SAVE` does not keep a property out of the .blend [V], so they are plain properties reset on `load_post` |
| `stale` | Bool | The inputs' slice key differs from `last_cache_key` (§6.3); reset on `load_post` |
| `embedded_presets` | String (JSON) | Written in `save_pre` |

Runtime objects (job, arrays, GPU textures) live in a module registry keyed by `scene.session_uid` [V], never in RNA, and are rebuilt or invalidated on `load_post`, `undo_post` and `redo_post`.

### 2.2 Object and mesh
`Object.slicewright`: `filament` (0 = inherit, 1..N, N ≤ 16 as for paint), `overrides` (→ `ConfigPG`), `role` (PART only in v1; others reserved for v1.1 volumes). Paint lives on the **mesh** (§5), so linked duplicates share it.

**Per-object overrides (implemented).** A key of `Object.slicewright.overrides` is overridden iff it is set (§2.3). The path to the engine is: `extract.object_overrides` reads the set keys (`config_pg.overridden`, FlatConfig text), `core/object_overrides.sanitize` keeps those whose schema `scope` is `object` or `region` (04 §2.5, §6.4) and turns every other key into a **warning Issue** instead of a failure (`override_unknown_key`: not in this engine's schema, e.g. an orphan from an older engine; `override_not_per_object`: a plate-wide setting such as `printable_height`; both carry the object and the key and say the override is ignored), and the kept dict becomes `ExtractedObject.config_overrides`, which the job runner passes as `add_object(config_overrides=...)` (04 §4.1) and which is part of the object's key (`hashing.object_key`, §4.4). Because every property of the generated group has the staleness `update=` callback and removal goes through `config_pg.clear`, adding, editing or removing an override marks the slice stale and gives the next slice another `last_cache_key`; an override equal to the plate's value is still a different input, so it too changes the key (the output is the same). The overrides of an instance are those of its plate owner (the object in the plate collection), like `filament`. A value the engine refuses (a hand-typed text value of a `floatOrPercent` or vector key) fails the slice before any job starts with "Settings error: <object>: <engine text>" and an error Issue that carries both `object_name` and `opt_key` (`jobs.JobRunner._fail_exc`). Checked against the real engine: a per-object `wall_loops` changes that object's filament use, a per-object `layer_height` adds layers, `zaa_enabled` on one of two objects contours only that object's toolpath, and every key the add-on offers is accepted by `add_object` (`tests/real/test_real_overrides.py`, `tests/contract/test_object_override_scope.py`).

**The Object panel** ("Slicewright object settings", Slicer tab, after Settings; `ui/object_panel.py`, `blender/object_settings.py`, `operators/object_settings.py`): for the active object it shows a note when the object is not on the Print plate, its filament slot, and the overrides: one row per set key (the generated property, so the right widget for its type) with an X button, orphan keys in red, an **Add** button and Remove all. Add opens a search popup (`slicewright.object_override_add`) over the keys `core/object_overrides.offerable` returns: scope `object` or `region`, allowed by the **Settings detail** preference (`settings_layout.mode_allows`, so Expert keys such as the `zaa_*` ones appear only at Expert), not already overridden, listed as "Label  (key)". A new override starts from the plate's current process value (`process_edits`), so it changes nothing until edited. Existing overrides are always listed, whatever the detail level, because they change the slice. The panel is drawn with the same property widgets as the settings pages but not through `settings.draw_page` (that is bound to the preset role and its Override/Reset buttons, which act on the active filament slot).

### 2.3 Generated typed config PropertyGroup
At register the add-on calls `sc.config_schema()` and builds `SLICEWRIGHT_PG_Config` with `type(...)` and `__annotations__`; ~800 annotated properties register in **2.8 ms** [V]. If the engine fails to import, only a diagnostic panel registers. No schema snapshot ships (labels are AGPL engine data).

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
- **Tooltip corrections (implemented, `core/descriptions.py`).** Labels and tooltips stay engine data read at runtime, but a small add-on-owned table (`CORRECTIONS`: key, a `marker` the engine's tooltip must still contain, the text, replace or append) carries the corrected wording for tooltips that are wrong or leave out what decides whether a value works. The engine is not patched and its schema is not copied; if an engine release fixes its text the marker no longer matches and the engine's own is shown. The corrected text is the property's description (`config_codec.spec_for`, so the settings pages and the Object panel's picker) and is what the filter searches. Two entries, both measured on the engine (Stage 0 of the Z contouring experiments): **`zaa_minimize_perimeter_height`** is replaced, because the engine lowers walls where the slope is *greater* than the value (`ContourZ.cpp`: `minimize_perimeter_height_angle < slope_degrees`) while Orca's tooltip says "less than"; **`zaa_min_z`** gets a note that it is a fixed height in mm that does not scale with the layer height (0.05 is near-optimal at 0.2 mm layers, it gave no gain at 0.08 mm) and that a value above the layer height, such as 0.25 at 0.2 mm, is refused with "Bad min Z value". **Modes are not changed locally:** the four `zaa_*` keys (`zaa_enabled`, `zaa_min_z`, `zaa_minimize_perimeter_height`, `zaa_dont_alternate_fill_direction`) are schema mode `expert`, scope `region`, and stay Expert-only (Quality > "Z contouring" at Expert detail, and the per-object picker at Expert). Orca classes them as expert, Stage 0's own recommendation is to keep Z contouring an optional expert feature (enabling it moves the slicing plane, so walls on slopes grow by about 0.19 mm), and a promotion table would be a second source of truth for modes next to the engine's; adding one later is a few lines in `settings_layout.mode_allows` callers if the owner decides otherwise.
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
| Portable copy of user and modified presets | `scene.slicewright.embedded_presets` (flattened, host keys stripped by allowlist) |
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

### 3.3 Module layout (`slicewright/core/profiles/`, pure Python, no `bpy`)
`source.py` (zip access), `index.py`, `resolve.py` (LRU), `compat.py`, `models.py` (vendor → model → nozzle → concrete printer), `user.py`, `importer.py`, `compose.py` (wrapper over `compose_config` + `normalize_config`).

### 3.4 Index build without threads
Parsing every header takes **0.51 s** in CPython [V, warm cache]. `index.py` is a generator that parses ~300 files per step; a timer runs steps for ~20 ms per tick, so the build finishes in 1–3 s of wall time with a responsive UI ("Loading printer library… 43%"). The index is cached per `orca_commit` and loads in ~50 ms later. Entries hold the fields needed for picking, resolution and compatibility. Resolution is lazy (2–5 ms per preset, LRU).

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
The printer picker lists variants from `machine_model.nozzle_diameter`; the concrete preset matches `printer_model` + `printer_variant`. Extruder variants (`extruder_variant_list`, per-variant filament vectors) are collapsed by `compose_config`; Python exposes only a "nozzle volume type" choice where offered. New printers take `default_print_profile` and the **first** of their `default_materials` (compatible with the process): one filament slot. A model can list a dozen (the Bambu A1 lists twelve), every slot lands in the G-code header and the flush matrix, and a plate of one material is the common case. The Filaments header has an "Add default materials" button (`slicewright.filament_add_defaults`, `picker.add_default_materials`) that appends the rest, skipping ones already in a slot and leaving existing slots, colours and overrides alone; choosing a different printer still replaces the slots, as before.

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
    face_support = attr_or_none(me, "slicewright_support")[poly].astype(np.uint8)   # per-face → per-triangle
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
- **Out of volume**: XY convex hull vs `printable_area` and the `bed_exclude_area` polygon, max Z vs `printable_height`, min Z vs −0.01 mm. In Prepare mode this runs on depsgraph updates, throttled to 200 ms, for live outlines.
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
| `slicewright_support` | 0 none, 1 enforce, 2 block |
| `slicewright_seam` | 0 none, 1 enforce, 2 block |
| `slicewright_filament` | 0 object default, 1..N slot (N ≤ 16: Orca's paint state limit, 04 §2.4) |

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
SLICEWRIGHT_OT_slice.execute  (main thread)
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
- **Runner (`engine/jobs.py`, no `bpy`).** `JobRunner.start(SliceRequest)` composes and normalises the config (a value in `errors` blocks the slice as a `ConfigError` status), computes the key (`hashing.scene_key` over the engine version, the normalised config and the object keys; threads and thumbnails are not part of it) and starts the `SliceJob`; `poll()` maps the engine's `running` to the UI state `slicing` (`cancelling` is a flag on it) and calls `result()` only after a terminal state. Errors never raise: the status carries the mapped `ErrorReport`, a one-line message and the merged issue list (§6.5). `Busy` is a failed status with `retry_later` and is not retried; a second `start` while a job lives is held until the first has stopped. The Blender side gathers the request (presets from the edit buffers, plate extraction) and drives `poll()`.
- **Timers** are registered with `persistent=True` so a file load doesn't silently drop them; a `load_pre` handler cancels any live job, and the timer finishes cleanly when the job ends.
- **Threads.** Slicing and everything else has **no `threading` usage** (a test greps for it; the sole exception is the network host-name lookup worker, §8.6). `set_threads` defaults to cores − 1.
- **Progress UI.** The panel uses `UILayout.progress(factor=…, type='BAR', text=…)` [V, an RNA function in 5.1] plus the stage message; the header shows `Slicing 42% · Generating supports`; `WindowManager.progress_begin/update/end` drives the cursor. Implemented so far: the primary Slice button / progress bar and Cancel sit in a header-less panel at the top of the Slicer tab (registered first, so it shows first), driven by `Scene.slicewright.job_*`; the live text goes to the status bar (`workspace.status_text_set`) and the cursor progress; there is no viewport-header button yet (no header pattern exists in the add-on). `blender/slicing.py` owns the poll timer: its own persistent callback at 0.1 s (the shared `TickRunner` timer re-arms at interval 0, too hot for polling an engine), armed by `start` and stopping itself when the job ends. A failed slice before any engine job starts (bad setting, empty plate, `Busy`) returns CANCELLED with a warning report, never an ERROR one (that raises for script callers).
- **Cancel.** Esc or a right click (a pass-through modal watcher that lives while the operator is running, started by `invoke`) or the Cancel button calls `job.cancel()`. "Cancelling…" shows in `cancelling`; G-code finalization isn't cancellable, so there can be a short tail.
- **Typed exceptions** (04 §7): `Cancelled` → info; `SliceError` → select the object; `ValidationError` → jump to `opt_key` or select `object_name`, list all issues; `ConfigError` → highlight the key; `ArrangeError`, `Busy`; `MemoryError` → suggest fewer threads; `EngineError` → "Copy diagnostics".
- **Recovery copy** (pref `recovery_copy`, default on). Before `start()`, if the file is dirty (or was never saved) and the last copy is older than 5 minutes, `bpy.ops.wm.save_as_mainfile(filepath=<extension_path_user>/recovery/<name>-<n>.blend, copy=True)`. It never touches the user's file, its path or its dirty flag [V: `copy=True` leaves both alone]; the last three copies are kept (`<n>` counts up across the directory, `<name>` is the .blend's base name or `untitled`). A native crash would take Blender down, so this is cheap insurance. Implemented in `blender/recovery.py` (the numbering and pruning are `core/recovery.py`, no `bpy`): background mode skips it, a failing write (read-only folder, full disk) is logged and the slice carries on, and a failed attempt does not start the 5-minute wait. Note that in `-b` a Python property write does not mark the file dirty (only an operator with an undo push does) [V].

### 6.2 Thumbnails
The add-on renders thumbnails at the sizes in the `thumbnails` key (e.g. `"48x48/PNG,300x300/PNG"`) with a `gpu.types.GPUOffScreen` from a fixed iso view, before `start()`, and passes RGBA arrays to `job.set_thumbnails` (04 §4.1). The engine encodes and embeds them; the add-on never edits G-code. Bambu printers get them only inside the `.gcode.3mf`. Background mode makes none.

Implemented as follows (`core/thumbnails.py` without `bpy`, `blender/thumbnails.py` for the GPU part). **This differs from the earlier draft, which named `draw_view3d`:** that call needs a 3D view region (a timer or script has none) and draws whatever the viewport shows, other scene objects included. The plate is already extracted as bed-frame mm triangles for the engine, so those are drawn directly with the builtin `SMOOTH_COLOR` shader: flat-shaded (one directional light plus ambient), each object in its filament slot's colour (0 inherits slot 1), orthographic from a fixed front-right-above direction, framed on the plate's bounding box with an 8 % margin, on a transparent background. One image per distinct `WxH` (the formats of a size share it), uint8 `(H, W, 4)` with row 0 at the top (the read-back is flipped). Sizes outside 1..4096 or malformed entries are dropped. The render is requested by the job runner (`SliceRequest.thumbnail_source`) only once the engine is really going to run: no entries in the key means no render and no `set_thumbnails` call, and a cache hit renders nothing. The key's *value* is part of the config and so of the slice key; the images are not. In background mode (or if the render fails) the slice goes ahead without them and the Issues list carries an `info` issue `thumbnails_skipped` (or a `warning` `thumbnail_render_failed`) next to the engine's own `thumbnail_missing` warnings. A cache hit serves the G-code as first sliced, thumbnails included or not.

### 6.3 Invalidation
While a slice result exists (`last_cache_key` is set and the job is `done` or a preview is loaded), `Scene.slicewright.stale` is `True` exactly when the slice key of the *current* inputs differs from `last_cache_key`; changing an input back clears it. The key is the runner's own (`jobs.slice_key` over `jobs.normalized_config` and the per-object keys, §4.4), so the two cannot drift. **This differs from the earlier draft ("no hashing in the handler"):** events still do no work, they only mark the scene dirty, but one timer, throttled to 0.3 s (first check no sooner than 0.1 s after the first event, so a drag is one check), recomputes the key and compares, instead of waiting for Slice or entering Preview.

`blender/staleness.py` marks a scene dirty from:
- a `depsgraph_update_post` handler (registered in `handlers.py` with `undo_post` and `redo_post`): an update to a plate object (transform, geometry, modifiers, per-object properties, also a boolean operand, which tags its target), to a mesh a plate object uses, to any collection, or to the scene when the plate signature (members, viewport visibility, unit scale) changed. Objects outside the plate are ignored;
- `update=` callbacks on every property that feeds the key: the generated config groups (printer and process edit buffers, slot and object overrides), the preset ids, the filament slots, the per-object properties and the plate collection. Writing a custom property does not tag the depsgraph [V], so the depsgraph alone would miss settings. `property_unset`, slot add/remove and `load_flat` call `staleness.mark()` explicitly (they call no update callback);
- undo and redo (they restore properties without callbacks).

The add-on's own writes (`job_*`, `stale`, `last_cache_key`, the preview and view settings, the settings-page navigation) have no such callback, so they never mark anything. A check re-extracts the plate only after an object-level event (the per-object keys are cached); a settings-only event recomposes the config and hashes it with the cached keys. Nothing is checked while a slice runs; its completion starts a full check, so an edit made meanwhile makes the result stale at once. An invalid setting counts as changed. `stale` resets on a completed slice, when the preview is cleared (`handler.clear`) and on load.

A stale preview stays visible, desaturated (`u_eye.w` of the path shader, 0.25; it rides in the eye vector because a separate push constant would exceed the 128 bytes every backend guarantees), and the Slice panel shows "Preview out of date", also after a failed or cancelled re-slice. Auto re-slice ships (plan §8; preference `auto_reslice`, off by default): every edit that marks the scene restarts a 1.5 s timer, which on firing slices again if the active scene's preview is stale and no slice is running (`staleness._auto_tick`). It is never started while a job runs; the end of that slice re-checks and re-arms it if the result is already stale. Not yet handled: a scene that is not the active one is rechecked only when it next changes.

### 6.4 Result cache
`cache/slices/<key>/` holds `out.gcode` (via `write_gcode`), one `.npy` per moves field (`moves.<field>.npy`) and per layers field (`layers.<field>.npy`, the engine's `layers` is a dict of three arrays, not one array), `gcode_line_ends.npy`, `stats.json`, `warnings.json` and a `manifest.json`, written one array per timer tick. The manifest also holds `filename_template`: the engine's `output_filename` evaluated once at store time with a token as the basename (so `filename_format`, the print time and the filament are the slice's own), which gives a cache hit or a reloaded file the same default export name as the live result (`CachedResult.output_filename(base)` swaps the token for the caller's basename; entries without a template give `<base>.gcode`). LRU capped by a pref (`cache_cap_mb`, default 2048, 0 turns the cache off). After reopening a file, `last_cache_key` restores the preview without slicing and export uses the cached G-code. (Plan §8: no cut line, so the cross-reload part ships.)

Implemented as follows. `engine/cache.py` (no `bpy`) is the store; `blender/paths.py` resolves the directory (`extension_path_user(<package>, path="cache/slices")` once installed as an extension, `user_resource('CONFIG')/slicewright/...` from a source checkout, where Blender refuses the extension call).
- **Writes** are atomic at two levels: every file goes to `<name>.part` and is renamed, and the whole entry is built in a `.tmp-<key>-…` staging directory that is renamed to `<key>/` only after `manifest.json` (which records each array's dtype, shape and size) is written. A cancelled or failed write removes its staging; a crash leaves staging litter that the next eviction sweeps. The task is a generator on the tick runner: one step per array (the G-code copy is one step, `write_gcode`). A 10M-move `position` array is therefore one step of roughly 100 ms.
- **Reads** validate the manifest (format, key equal to the directory name, every file present at its recorded size, equal move counts) and ignore anything else, so a partial, copied-under-another-name or truncated entry is a miss. A same-size but garbled array is only found while loading; the entry is then discarded and the slice runs. A key must be a hex digest, which also keeps it a safe path component.
- **Eviction** runs after each store: least recently used first (a hit touches `manifest.json`), the entry just stored or used is never removed (so one entry over the cap is kept), and key-named directories without a valid manifest and staging older than an hour go too.
- **Hit.** `JobRunner` (with a `ResultCache`) looks the key up after computing it; a hit returns `done` with the `Entry` and no `SliceJob` is created (`JobStatus.cached`, `from_cache`; the text reads "Done (from cache)"). The preview then loads from disk one array per tick and shows through the same `show_result`. A cached result (`CachedResult`) carries the moves, layers, stats, warnings, objects and `write_gcode`, but no `write_gcode_3mf` (that needs the live engine result). Thumbnails are not part of the key, so a hit serves the G-code as it was first sliced.
- **Reconnect.** The `load_post` hook (after `props` has reset the runtime fields) looks `last_cache_key` up and, on a hit, loads the entry on the ticks, shows the preview, sets `job_state` to `done` with "Restored from cache" and runs `staleness.after_slice`, so an edit saved after the slice shows as out of date. A missing, partial or unreadable entry leaves the preview empty and the Slice panel shows a one-line note (kept in the runtime registry, not in `job_message`); `last_cache_key` is kept. `load_pre` drops a restore still loading; a cache write carries on, it is keyed by content.

### 6.5 Issues
`result.warnings`, a `ValidationError`'s `issues` and our pre-checks merge into one list of `Issue` dicts `{level, code, message, opt_key, object_name}` (04 §2.6), shown in a UIList under the Slice panel; clicking a row selects the object or opens the setting.

Implemented as follows. `core/issues.py` (no `bpy`) holds an `IssueBook` with one list per source, `slice` (the last slice's status: engine warnings, validation errors and the pre-checks that travelled with the request) and `check` (the Check plate operator), and merges them: the slice's wording wins a duplicate (object-level codes match on `(code, object_name)`), errors come before warnings before notes, each in arrival order. A source is replaced as a whole, never appended to. The book is a runtime registry (`blender/issues.py`), not saved in the .blend; `registry.state.plate_issues` stays equal to the merged list. `template_list` needs an RNA collection, so the list is mirrored into `WindowManager.slicewright_issues` (a window manager's custom properties are not written to the .blend [V]) and rebuilt on every change. The list belongs to the scene last sliced or checked; other scenes show it empty.

- **Cleared**: at the start of a slice (the list is empty while it runs), replaced when the slice finishes in any state (done, failed or cancelled; the `check` source is dropped because the slice ran its own pre-checks), and emptied on `load_pre` and `load_post`.
- **Panel**: "Issues" is one panel of the Slicer tab (its place is in section 1.1). The header shows one icon and count per level (cross = error, triangle = warning, info mark = note); an empty list reads "No issues". Each issue is a small box (not a UIList: a UIList row cannot wrap): the sentence as wrapped labels with the severity icon (6.5a), then the buttons that fit: **Select object** (`issue_goto`: select, make active, frame; also opens the setting when the issue has an `opt_key` too), **Show setting** (an issue with a key and no object), **Open edges** (`mesh_open_edges`: the "Select non-manifold" helper) and, when the sentence was reworded, an info button (`issue_details`) whose tooltip is the engine's own text and whose click reports it to the status bar and Info log. At most 6 issues show; "Show all N" (`WindowManager.slicewright_issues_all`, not saved) reveals the rest. The `WindowManager.slicewright_issues` mirror is kept for the active index. **Quiet rows** (`core.issues.QUIET_CODES`, today only `preset_keys_unknown`) are not part of the list and not counted: the header counts, "No issues" and `issue_goto`'s row indexes (`issues.visible()`) leave them out.
- **Unknown keys from a preset** (`core.issues.collapse_preset_unknown_keys`, applied by the runner to `normalize_config`'s issues). A Bambu Studio preset carries dozens of keys Orca 2.4.2 does not know (the A1 brings 24); the engine drops them and reports each as an `unknown_key` warning (04 §6.2), which the user did not cause and cannot act on. The request records which unknown keys came unchanged from the selected presets (`SliceRequest.preset_keys`, from `slice_inputs.preset_keys`: the key is in the resolved preset with an identical value and is not a filament-slot override; user presets count as presets). Those warnings are replaced by ONE `info` row with the add-on's own code `preset_keys_unknown`, "N profile settings ignored", whose `detail` (an extra field on the Issue dict, never sent to the engine) holds the sorted key list. **Decision (2026-11-22, after a real-window review: the 24 keys pushed everything off the screen):** it is information, not a problem, so it keeps `level: info` in the data but is *quiet* in the panel: it is not a list row and not in the header counts (a first-time user on a clean first slice sees "No issues"), and it never counts as a warning. Under the list sits one line, "24 profile settings ignored", with a disclosure triangle (`WindowManager.slicewright_ignored_open`, closed by default, not saved); opened, it shows a one-line note and a 5-row UIList of the keys (`slicewright_ignored_keys`, scrolls on its own). An unknown key whose value was changed in the edit buffer, or that a slot overrides, stays an `unknown_key` warning. No engine API change.
- **Message text** (`core/messages.py`, 6.5a). `messages.plain(issue)` is what the panel, the Slice panel's error box and the job message show; the engine's own `message` stays in the Issue, in the row's tooltip and in `issue_details`.
- **Click** (`slicewright.issue_goto`): with an `object_name`, select that object alone, make it active and frame it in the 3D view (`Bolt#3` resolves to `Bolt`, or to the plate object that instances it); with an `opt_key`, point the Settings panel at it: the role from the schema's `preset`, the first page that lists the key, and the filter set to the key so only that row shows. If the settings level (Simple/Advanced/Expert) hides the key, the page opens with an empty filter and the status bar says so. A row with neither only reports its message. The Settings panel itself is not expanded: Blender has no API to open a collapsed panel.

### 6.5a Message text: wrapping and plain wording
Blender labels never wrap: a long message is cut in the middle ("...d_exclude_area)."). Two small pieces fix that for every message in the sidebar.

- **Wrapping** (`core/text.py`, `blender/labels.py`; tests `test_text_messages.py`). `text.wrap(text, width)` breaks at spaces, breaks a word longer than a line (a setting key) and never drops a character. The width comes from `text.char_budget(region_width, scale, icon=, indent=)`: the region's width in pixels over its pixels per UI unit (`system.dpi / 72 * view.ui_scale`, 2 on a Retina Mac), minus 54 units of margins, scroll bar and tab strip, over 5.1 units per character, times 0.95, minus 4 for an icon and any `indent`; between 16 and 90, and 38 where no region is known (background mode, tests). Measured on a real window: a sidebar 280 units wide (561 px) fits 42 characters, 38 with an icon. `labels.draw_wrapped(layout, text, icon, indent=)` draws one label per line (the icon on the first, `BLANK1` on the rest, so the lines align) and is used for every message label in the sidebar: the Slice panel's error, hint and note lines, Issues, Export, Bake, Send (gate, why-disabled hint, last result, test result, certificate), the printer box's problems and test result, Plate, Object settings, Preview status and the engine panel.
- **Plain wording** (`core/messages.py`). `plain(issue)` rewrites what we know: `object_outside_bed` becomes "<name> is outside the print area" (or "<name> overlaps a no-print zone of the bed" for our own exclude-area check), `object_too_tall` "<name> is too tall for this printer", `Object 'X' ...` loses its quotes, and the setting names in the engine's text ("printable_area minus bed_exclude_area") disappear. The name is the issue's `object_name`, else the one the text starts with. Anything else is unchanged, so nothing is ever lost. `engine/jobs.error_message` uses it for the Slice panel's "Validation failed: ..." line. The engine's own text stays in the Issue and is shown by the tooltip of `issue_goto` and by `issue_details`.

---

## 7. Preview renderer

Written from this spec. libvgcode (AGPL) is a behaviour reference for what a slicer preview shows, not a source to translate.

### 7.1 What the Python `gpu` API allows [V, background mode]
- **Shaders.** `GPUShaderCreateInfo` accepts `vertex_in`, `vertex_out` (smooth/flat/no_perspective), `push_constant` (`INT`, `FLOAT`, `VEC*`, `IVEC2`, arrays; **avoid `UINT`, it cannot be set from Python**), `uniform_buf` + `typedef_source`, `sampler` (`FLOAT_2D`, `UINT_2D`, `*_BUFFER`), `define`, `fragment_out`, `depth_write`. Compiling and drawing need a GUI context, confirmed on Metal, GL and Vulkan by the Phase 0 GPU spike ([results](../spikes/phase0-results.md)).
- **Vertex formats.** `attr_add` with `I8…U32, F32, I10` and fetch modes `FLOAT`, `INT`, `INT_TO_FLOAT_UNIT`.
- **Batches.** `draw`, `draw_range`, `draw_instanced(instance_start, instance_count)` (exposes `gl_InstanceID`). **`instance_count=0` draws ONE instance** (verified on Metal, GL and Vulkan), not "all instances"; either way the planner skips empty ranges, so an empty range costs zero draw calls. No per-instance attributes, no SSBOs.
- **Textures and buffers.** `GPUTexture(size, format, data=Buffer)` supports RGBA32F, RG32UI, RG16F, R32F and more, but is **filled whole at construction: there is no sub-region update**. `GPUVertBuf` has only whole-buffer `attr_fill`. `gpu.types.Buffer` built from a numpy array is **zero-copy** [V]: about 0.01 ms for 4 MB, and it keeps the array alive. Multi-dimensional buffers report reversed strides, so read them back with `np.asarray(memoryview(buf)).T.reshape(-1)`.
- **State.** Depth test and mask, blend, line width (> 1 px unreliable on Metal/Vulkan), point size, clip distances. No polygon offset.

**Conclusion: vertex pulling from per-chunk textures.** Moves are split into chunks of up to C = 2²⁰ (~1M) moves; each chunk owns its own small textures, built once when its data is ready. A 4-vertex `TRI_STRIP` ribbon template (about 2x faster than a 6-vertex `TRIS` template in the spike) is drawn with **one `draw_instanced` call per chunk**, and the vertex shader computes the segment from `gl_InstanceID` plus a per-chunk offset uniform (no reliance on base-instance semantics).

### 7.2 Chunk layout
Fields come from 04 §5.2. Chunk *c* covers moves `[s_c, e_c]`. Its textures hold `n_c + 1` texels at width W = 8192: **texel 0 duplicates move `s_c − 1`** (move 0 for the first chunk), and texel *j* ≥ 1 holds move `s_c − 1 + j`. Every segment's start point is therefore in the same chunk.

| Texture (per chunk) | Format | Contents | B/move |
|---|---|---|---|
| `t_pos` | RGBA32F | x, y, z, width | 16 |
| `t_meta` | RG32F | bit-packed, see below: `.r` = role (5 b) \| type (4 b) \| filament (8 b) \| nozzle (3 b) \| flags; `.g` = layer_id | 8 |
| `t_val` | RG16F | height, scalar for the active view; fetched only when the view mode needs it | 4 |

**`t_meta` packing.** Each 32-bit channel carries 23 payload bits in the mantissa under exponent `0x3F800000` (a float in [1, 2)), so it is a valid finite float on every backend, and the shader decodes it with `floatBitsToUint(...) & 0x7FFFFFu`. RG32UI created from a float view also worked in a small diagnostic but was not fully exercised, so RG32F is the specified format.

28 B/move: 10M moves ≈ 280 MB VRAM. A view change rebuilds only each chunk's `t_val`. A chunk is 128 rows, far below any texture limit. The palette is a std140 UBO (`role_colors[32]`, `option_colors[16]`, `range_colors[11]`, `range_minmax`, slot colours) with the values in Appendix A.

### 7.3 Draw passes
1. **Extrusions as tubes** (default): 4-vertex `TRI_STRIP` template with a `corner` attribute that must be **U32 (`UVEC4` in the shader)**: a 1-byte U8 attribute aborts Blender on Metal (vertex descriptor stride 1). The vertex shader fetches A (texel *j*−1), B (texel *j*) and the meta texel, rejects non-extrusions and masked roles by emitting a clipped position, and otherwise builds a view-facing ribbon of the move's width, extended by width/2 at the ends. The fragment shader reconstructs a cylinder normal and shades Lambert + specular. A "box" option uses an 8-vertex template for close-ups.
2. **Travel**: a 2-vertex `LINES` template, type == Travel, off by default.
3. **Markers** (seams, retracts, unretracts, tool/colour changes, pauses): per-chunk index lists (`np.flatnonzero(type == X)`) in R32F textures (the index is stored as float bits), drawn as screen-space quads in option colours under the same ranges.
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
if last_c < first_c: skip            # never issue an empty draw (instance_count=0 draws one instance)
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
- **CPU.** The engine SoA is ~70 B/move (5M ≈ 350 MB). Packing runs per chunk: the transient peak is the SoA plus one chunk's staging arrays (~60–90 MB at C = 1M; the `Buffer` itself is zero-copy), not a whole-print copy. After all chunks are built, only fields needed for view changes stay (float16 scalars, ~25 B/move); `position` is dropped from CPU memory.
- **Upload.** One chunk per timer tick: pack, build `Buffer`s, construct the three textures. Budget ≤ 40 ms per chunk; the GPU spike measured a median of 8.5 ms (Apple M1 Max) [V].
- **VRAM budget** (pref). Defaults are conservative on integrated GPUs: 384 MB when `gpu.platform.device_type_get()` reports Intel or when Apple Silicon has ≤ 8 GB unified memory; 1 GB otherwise. Over budget, the plan degrades in two steps: the `t_val` textures are dropped first (only the feature-type view stays), then the preview is truncated at the last chunk that fits, so the print can still be scrubbed up to that move. Travel and the lines LOD are not budget steps. The user is told which step was taken.

### 7.9 Shader sketch

```glsl
// samplers: 0 FLOAT_2D t_pos, 1 FLOAT_2D t_meta (RG32F, bit-packed), 2 FLOAT_2D t_val (all per chunk); UBO 0 Palette pal
// push: MAT4 ViewProjectionMatrix; VEC3 u_eye; INT u_first; INT u_grey_below;
//       INT u_role_mask; INT u_view_mode
ivec2 tc(int j) { return ivec2(j % 8192, j / 8192); }
void main() {
  int j = u_first + gl_InstanceID;                 // texel in this chunk; j >= 1
  vec4 B = texelFetch(t_pos, tc(j), 0), A = texelFetch(t_pos, tc(j - 1), 0);
  uvec2 m = floatBitsToUint(texelFetch(t_meta, tc(j), 0).rg) & 0x7FFFFFu;   // 23 payload bits
  uint role = m.r & 31u, type = (m.r >> 5) & 15u; int layer = int(m.g);
  if (type != EXTRUDE || ((uint(u_role_mask) >> role) & 1u) == 0u) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return;    // rejected: clipped
  }
  /* expand ribbon from corner id; v_side for the cylinder normal; grey if layer < u_grey_below */
}
```

The role mask is an **INT** push constant: UINT push constants cannot be set from Python, and a stale mask rejected every segment on GL. The shader casts it to `uint` before shifting.

### 7.9a Testing and capture notes (from the Phase 0 GPU spike)
- `GPUOffScreen.draw_view3d` does **not** run `POST_VIEW` handlers. For screenshots and tests, capture through a `POST_PIXEL` handler using `read_color`; `screen.screenshot` is black on GL under xvfb.
- `bpy.app.timers` never fire in `-b`, so headless tests drive `poll_job` directly.
- Workspaces can only be created with `ops.workspace.duplicate()` plus a rename; `bpy.data.workspaces` has no `new()`.
- `WorkSpaceTool` registration and activation work; the click-through raycast is untested.

### 7.10 Lifecycle
One `Renderer` per scene runtime, owning the chunk textures and template batches; shaders compile lazily on first draw. Draw handlers are added in `register()` and removed in `unregister()`, and return early when idle. `load_pre` releases GPU resources and cancels any job; `load_post` reconnects through `last_cache_key`. Exceptions in handlers are logged once per session.

### 7.11 Bake to Curves / Mesh (v1)
- **Curves (default)**: a `Curves` datablock [V]; one curve per maximal run with the same role and filament and no travel (breaks via `np.flatnonzero`); point radius = width/2; a `role` attribute; one material per role (base colour from Appendix A). Renders as tubes in Cycles and EEVEE.
- **Mesh**: a hexagonal prism per segment (12 vertices); warn above ~2M segments.
- Both bake only the **visible range and roles** into a "Slicewright bake" collection that slicing excludes, filled with `foreach_set` in chunks across ticks.

Implemented as follows (`core/bake.py` and `core/bake_plan.py` pure numpy, tests `test_bake_core.py`, `test_bake_plan.py`; `blender/bake_data.py` the datablocks, `blender/baking.py` the plan, job and runtime stage, `blender/bake_pg.py` the choices, `blender/operators/bake.py`, `blender/ui/bake_panel.py`; headless test `check_bake.py` on both engines):
- **Where.** A "Bake" panel under Export in the Slicer tab (collapsed by default), operators `slicewright.bake`, `bake_cancel` and `bake_keep`. The choices (kind, layer range, features, travel, sides, segments per object) are on the window manager (`slicewright_bake`), like the Send panel's: session-only, never in a shared .blend. The button is greyed with the reason under it: "Slice the plate to bake it", "Wait for the slice to finish", "Loading the cached slice…", "Out of date: slice again, then bake" (unlike export, an out-of-date slice is not offered with a confirmation: a bake of a plate that has since changed has no use), "Wait for the bake to finish".
- **What is baked.** The result the preview shows (`rt.result.moves` and `layers`). By default (*Use the preview's range and features*) it is exactly what the Preview shows: its layer range, in-layer scrub position and visible feature types; otherwise the panel's own layer range and a checkbox per feature type. **Travel moves** are off by default and have their own checkbox; they are the feature type 32 (`TRAVEL_KEY`), baked thin (0.1 mm) because the engine gives them no width or height. Move 0 has no segment. Wipe moves are not baked.
- **Paths and objects.** Move *i* ends segment *i-1 to i*. A **path** is a maximal run of consecutive selected moves with the same feature (role, or travel), filament, layer and `object_id`: one curve. A path of *n* segments has *n + 1* points (the positions of moves `first-1..last`); a point takes the values of the segment that ends there and the first point copies the first segment. The bake is split by **source object** (`object_id` through `result.objects`; the skirt, wipe tower and custom G-code, which have no object, become "Plate extras"), one baked object per source and kind, named `<source> Bake (curves)` / `<source> Bake (mesh)` (`... (mesh 02)` when a mesh is split in parts). Names are cut to 200 bytes.
- **Geometry.** In Blender units (`1 / scene_mm_per_bu`), centred at `z - height / 2` (a move's z is the nozzle, the top of the layer; the preview shader centres at z, so a baked bead sits half a layer lower than the preview's ribbon). Curves are POLY curves with `radius = width / 2` (a round tube: it is deliberately not area-equivalent, so a 0.45 x 0.2 bead is fatter than a real one; Geometry Nodes can reset the radius from the `width` and `height` attributes). **Mesh**: one prism per segment with `sides` corners (default 6, a flat-topped hexagon of exactly width x height; 3 to 16; 4 is a box), 2 x sides vertices, `sides` quads and a cap polygon at each end of every *path*. A prism is lengthened at a joint by `half width x tan(turn / 2)`, at most half a width, so the outside of a corner has no wedge-shaped gap; neighbouring prisms are not welded and the mesh is not watertight between segments (only each path end is capped). Sides read round (smooth shading) for 5 or more sides, flat for 4 or fewer; caps are flat. Edges and loops are built in numpy (Blender's own edge computation would double the finalise time).
- **Attributes** (slicer units, whatever the scene's unit scale): `feature_type` (role id, 32 = travel), `layer`, `width`, `height`, `speed` (mm/s), `flow` (mm3/s = `mm3_per_mm x feedrate`), `filament`, on the POINT domain of Curves and the FACE domain of meshes, plus `material_index` (CURVE / FACE) and `radius` (Curves). The object carries `slicewright_features`, a plain dictionary `{feature_type id: name}`. One material per feature type present (`Slicewright Outer wall`, ..., `Slicewright Travel`), base colour from Appendix A in linear space, found again (and so kept with the user's edits) by a tag on the next bake.
- **Plain data.** Baked objects live in a `Slicewright Bake` collection linked under the scene's master collection, never in the plate. The objects, their curves or meshes and the collection carry tags (custom properties `slicewright_bake` = kind, `slicewright_bake_source`, `slicewright_bake_scene`); nothing points back at the add-on (no modifier, driver or node tree), so the .blend opens without it (tested after unregistering). `plate_objects` ignores any tagged object even if the user moves the collection under the plate. Only a collection the add-on tagged is reused.
- **Size and asking.** Before anything starts the panel shows the estimate (`bake_plan.estimate`): Curves as points and curves, a mesh as vertices and faces, with the bytes Blender will hold (curves count the numpy arrays and Blender's copy together). Above **512 MB** the operator asks first ("This is a large bake (12,000,000 vertices, 600,000 faces, about 0.8 GB)..."), above **4 GB** it refuses and says to narrow the range, switch features off or use curves. With a hexagonal prism (696 bytes a segment) that is ~0.75M and ~6M segments; the doc's earlier "~2M segments" is replaced by these byte limits. A script's `execute` must say `allow_large=True`; `invoke` shows the popup (background Blender shows none: `invoke_confirm` cancels).
- **Replacing.** Baking again for the same kind, scene and source needs a yes: one popup lists what is replaced ("The 2 objects of the previous bake of the same sources will be replaced: ..."); a script passes `replace=True`. Only objects that carry the add-on's tag are ever removed (and their now unused tagged data); a user's object with the same name is untouched. A bake is not silently deleted by renaming, so `bake_keep` ("Keep Baked Objects") removes the tags from the scene's baked objects: they become the user's and the next bake adds new objects. Old objects go in the last step, after the new ones exist; a cancel or a failure removes everything the job created and nothing else.
- **Chunking.** One generator task on the shared tick runner (`bake:<scene>`, progress bar and Cancel in the panel). numpy steps are 250k curve points or 16k mesh segments (8 ms measured); the Blender copies are one `foreach_set` per array (an attribute copy is a memcpy; `Mesh.vertices.foreach_set("co")` and `Curves.points.foreach_set("position")` are 20 to 70 times slower and are not used). A mesh is split into **parts** of at most `part_segments` (default 50,000; 5,000 to 2,000,000) because `polygons.foreach_set("loop_start")` (~40 ns a face) and `Mesh.update` (~0.5 us a segment) cannot be done in pieces on one mesh: a part's finishing steps are about 15 to 25 ms. The cost is more objects (1M segments = 20 objects). Nothing is linked to the scene until the last step, which also pushes an undo step; an undo while baking stops the job ("the file changed while baking"). A file load (`load_pre`) cancels the job.
- **Measured** (Blender 5.1.2 on an Apple M-series laptop with 32 GB, `-b`, synthetic 1M-move and 10M-move tables with ~30-segment paths, the real job on the tick runner with the 20 ms budget; the numbers include nothing a window would add). Curves, 1M moves (0.97M segments, 34k curves): plan 22 ms, whole bake 0.1 s, worst tick 28 ms, ~85 MB. Mesh, 1M moves: plan 50 ms, 11.6M vertices and 5.9M faces in 20 objects, 1.7 s of work over 60 ticks, worst tick 42 ms (average 29 ms), peak process memory 1.15 GB (Blender itself ~0.3 GB), ~645 MB estimated. Curves, 10M moves (10.0M points, 341k curves): plan 0.87 s (a synchronous numpy pass at the click and when the panel's settings change), 1.5 s of work over 44 ticks, **worst tick 253 ms** (one whole-array copy into Blender, which cannot be split), peak 2.1 GB. So the 20 ms budget is held by the numpy steps (about 8 ms) but a Blender copy or a mesh part can run 25 to 45 ms, and a 10M-point curve bake has one visible hitch. None of this was run on a real engine result at this size; real slices were baked only at test sizes.
- **Not done / not verified.** No travel assignment to an object: travel has its own `object_id` from the engine. Ribbon geometry, welded prisms, vertex colours and per-point `time` are not baked. The panel and popups were walked with a stand-in layout in headless Blender, not in a window; the Cycles render (CPU, 64 x 64) checks that curves are visible, not how they look.

---

## 8. Export and send

### 8.1 Export
- **G-code**: copy the engine's `out.gcode`; default name from `result.output_filename(input_basename)`.
- **`.gcode.3mf`**: `result.write_gcode_3mf(path, plate_meta)` (04 §5.1). Firmware acceptance is tested on the hardware matrix.

Implemented as follows (`core/export.py` no `bpy`, `blender/exporting.py` the runtime, `blender/operators/export.py`, `blender/ui/export_panel.py`):
- **Panel and operators.** An "Export" panel sits under Slice and Issues; its "Export G-code" button (`slicewright.export_gcode`) is enabled only when there is a slice to export and no slice is running or loading. The line under it says why it is greyed ("Slice the plate to export it", "Wait for the slice to finish", "Loading the cached slice…"), or that the preview is out of date, or what the last export did (a check mark, or the error in red; dropped on file load).
- **Where the G-code comes from** is the result the preview shows (`last_cache_key`): the live engine result straight after a slice, the `CachedResult` after a cache hit or a reload (`write_gcode` copies the cached `out.gcode`), or, if the preview is not loaded, the cache entry itself. All three give the same bytes as the slice; a missing entry after a reload means there is nothing to export.
- **File dialog.** It opens in the .blend's folder, or, for a file that was never saved, in the last export folder (preference `last_export_dir`, updated after each export) and then the home folder. The default name is the engine's `output_filename(<.blend name without ".blend">)`, so a dot in the name is kept (`my.part.blend` gives `my.part_PLA_20m2s.gcode`); `untitled` stands in for an unsaved file. A name the format made is made portable (path separators, characters Windows forbids, reserved device names) and an extension is only ever added, never swapped (`bpy.path.ensure_ext` would turn `my.part_PLA` into `my.gcode`). If `filename_format` cannot be evaluated the name is `<basename>.gcode`. A cache hit gets the same name as the live result: the cache stores the engine's `output_filename` once with a token for the basename (§6.4).
- **Overwrite.** Blender's file dialog only reddens an existing name, so after the dialog the add-on asks itself ("Replace file?", `slicewright.export_confirm`); a script call needs `overwrite=True`, otherwise it is cancelled with a warning and the file is untouched.
- **Atomic write.** The file is written to a hidden sibling (`.<name>.<pid>.part`) and renamed over the destination, so a failure (full disk, an engine error) leaves neither a partial file nor damage to the file it would have replaced. A G-code copy is one `write_gcode` call with the GIL released, streamed by the OS; it is not chunked on the tick runner (the doc asks for chunking only for the cache and the preview).
- **Out-of-date preview.** If the plate or settings changed since the slice (the pending check is run first, so an edit made a moment ago counts), exporting asks "Preview out of date: the file would hold the older slice. Slice again first to export the current plate." with "Export anyway", before the file dialog. The simplest choice that does not surprise: it never silently writes the older slice, and slicing again is one click on the Slice button above. A script call needs `allow_stale=True`.
- **`.gcode.3mf`** (`slicewright.export_gcode_3mf`, same dialog, name and popups; the name is the engine's with `.gcode` swapped for `.gcode.3mf`, and `plate_name` is the .blend's name). `write_gcode_3mf` needs the live engine result, so with one (straight after a slice) the file is written at once; it is a single engine call that holds the engine lock and the main thread and cannot be chunked, and it raises `Busy` while a job runs (reported, nothing written). After a cache hit or a reload there is no live result, so the plate is **sliced again** with the same inputs (`exporting._start_reslice`): its own `JobRunner` and poll timer (`poll_export`, 0.1 s) rather than the preview's runner, thumbnails rendered again, no cache lookup. While it runs the Export panel shows a progress bar and Cancel, the status bar shows the text, and Slice and both export buttons wait (a Slice click is refused with a warning instead of failing with `Busy`). The displayed slice is untouched: no preview reload, no change to `stale`, `last_cache_key`, `job_*` or the Issues list while it runs. If it fails, the typed error goes into the Issues list (source `export`, cleared by the next export or slice) and the panel message. The re-slice must come out with the same key as the slice on screen, otherwise nothing is written. It yields the same G-code **except the header's generation time stamp** (`; generated by OrcaSlicer ... at HH:MM:SS`), so the 3MF's G-code is not byte-identical to the cached `out.gcode`. If the preview is out of date and the engine's result is gone, the `.gcode.3mf` is not offered (the inputs of the slice on screen cannot be reproduced): slice again first; the G-code can still be exported from the cache.
- **Thumbnails** are passed at slice time (`set_thumbnails`), never at export: the `thumbnails` key's sizes are rendered (the A1 gets `48x48/PNG,300x300/PNG` from Orca's default when its profile has none), the largest becomes `Metadata/plate_1.png` in the `.gcode.3mf` (plus the derived `plate_1_small.png`) and a Klipper printer's G-code embeds all of them; a Bambu printer's plain G-code has none (04 §4.1). Background Blender renders none, so a file exported from a slice made there has no plate image. Verified with the real engine on the A1 (3MF) and a Voron 2.4 (G-code).
- Background Blender has no window: `INVOKE_DEFAULT` runs `execute` straight away and every popup answers "yes", which is how the tests walk the popup chains. The dialogs themselves have not been exercised by hand in a window here.

### 8.2 OctoPrint
Behaviour reference [O `OctoPrint.cpp`]: test with `GET /api/version` (`X-Api-Key`); upload with `POST /api/files/local` multipart (`file`, `path`, `select`, `print`).

Implemented as follows (layer `octoprint`: `core/network/webapi.py`, `core/network/octoprint.py`; tests `test_net_octoprint.py`, `FakeOctoPrint` in `tests/fake_net.py`):
- **Shared plumbing** (`webapi.py`, used by the Moonraker client too): `Endpoint` (base URL including a reverse-proxy prefix, the key as a `SecretStr`, TLS policy, timeouts, resolver, cancel token), `call()` (one request; `X-Api-Key` is built there from `reveal()` into a local variable and nowhere else), `Transfer` (progress object a panel can read: `phase` connecting / uploading / waiting / done, `sent`, `total`), `check_filename` / `check_subfolder` (no folder part or control characters or hidden names in the file name; a plain relative folder without `..`) and `status_error` which maps an HTTP answer to a message built from the product, the origin and the status line only (**never** a header, a request body or the server's answer text): 401/403 -> `AuthRejected` ("rejected the API key"), 409 -> `Conflict` ("busy, not ready, or printing a file with the same name"), 413 -> `PayloadTooLarge` (mentions a reverse proxy), 3xx -> "turn HTTPS on or fix the port or base path", 404 -> "check the port, HTTPS setting and base path", 415, 5xx. A 2xx that is not JSON from the right product is `NotThisServer`. Uploads use their own, longer idle timeout (60 s: the printer may think after the last byte); a redirect is followed only for the GET calls (same host, never to plain HTTP), never for the POST.
- **`test_connection(ep)`**: `GET /api/version` (OctoPrint demands a valid key for it, so a 200 means the key is accepted; the server and API versions are read from it), then `GET /api/connection` for the printer state (`Operational`, `Printing`, `Closed`...), which is a nicety: a key without that permission, a 404 or a 500 leave the state `None` and are not errors. A missing key sends nothing (`InvalidRequest`). Result `ConnectionInfo.summary()`: "OctoPrint 1.10.2 (API 0.1), API key accepted, printer: Operational".
- **`upload(ep, file_path, name, subfolder="", select=False, start_print=False, transfer=None)`**: `POST /api/files/local`, multipart with the text fields first (`path` for a folder, `select=true`, `print=true`) and the file in `file`, streamed from disk in 256 KB chunks (never read whole); `Content-Length` is exact. **A print is started only for `start_print=True`** (the identity test is strict: `1` is not `True`), which also sends `select=true` because OctoPrint prints what it selects; `select` is never implied otherwise. The result (`UploadResult`) takes the stored name and path from OctoPrint's `files.local` and **`selected`/`printing` only from what OctoPrint confirms** (`effectiveSelect` / `effectivePrint`): an answer that is not the expected JSON is not an upload failure, and claims nothing. `foldername` (creating a folder) is not used: it is a separate request without a file, and the add-on sends into an existing or implicit `path`.
- **Failure behaviour** (all tested against the fake): 401/403 before or after the body, 409 (busy, only for a request to select or print), 413 before the body (the upload stops after the first chunks: `Transfer.sent < total`), 415, 400, 500/503, a connection dropped mid-upload (`ConnectionLost`), a server that stops reading (`Timeout` idle), cancel mid-upload (the generator raises `Cancelled`; closing it closes the socket, which the server sees), a wrong base path (404 message), something else on the port. No step of an upload takes the 20 ms budget (measured with the tick runner).
- **The key never leaves the header**: a test runs every path (success, 401, 403, 409, 500, bad key) against a server that *echoes the key it was sent* in its error bodies and asserts that the key appears in no URL, message, exception repr, result or captured log record; over HTTPS with pinning the request (and so the key) is sent only after the pinned fingerprint matched, and an unknown or different certificate gets nothing (the fake records no request).

### 8.3 Moonraker / Klipper
Behaviour reference [O `Moonraker.cpp:178-300`]: `GET /server/info`; `POST /server/files/upload` (multipart `file`, `root=gcodes`); read `result.item.path`; `POST /printer/print/start {"filename": path}`. `X-Api-Key` if configured.

Implemented as follows (layer `moonraker`: `core/network/moonraker.py` on the shared `webapi.py`; tests `test_net_moonraker.py`, `FakeMoonraker` in `tests/fake_net.py`):
- **`test_connection(ep)`**: `GET /server/info` (Moonraker and API versions, `klippy_state`), then `GET /printer/info` for Klipper's state message, which is a nicety (Moonraker answers 503 while Klipper is not connected: that only loses the message). Klipper not being `ready` (`startup`, `shutdown`, `error`, `disconnected`) is **reported, not an error**: files can still be sent, `ConnectionInfo.ready` is False and the summary says so ("Moonraker v0.9.3 (API 1.0.5), API key accepted, Klipper: shutdown"). The API key is optional: with none configured nothing is sent (no header) and the summary says "access allowed (no API key set)"; a 401/403 then reads "asks for authorization, but this printer has no API key set... or add this computer to Moonraker's trusted clients". JWT/OAuth login is out of scope.
- **`upload(ep, file_path, name, subfolder="", start_print=False, transfer=None)`**: `POST /server/files/upload`, multipart with `root=gcodes`, `path` (a folder, only when given), `print=true` (**only** for `start_print=True`, strict identity) and the file in `file`, streamed in 256 KB chunks. `UploadResult.printing` is true only when Moonraker answers `print_started: true`; a print it declined (Klipper not ready, one already running) is reported as not started. **Observation about the design text above:** Moonraker's public API documentation shows the upload answer as `{"item": {"path": ..., "root": ..., "size": ...}, "print_queued": ..., "print_started": ..., "action": "create_file"}`, with `item` at the top level, not under `result` as "read `result.item.path`" suggests; the client reads the top-level shape and also accepts a `result` wrapper, and falls back to the name it sent when the body is neither (the file is stored; claiming nothing). The separate `POST /printer/print/start` is not used: `print=true` on the upload does it in one request. Not implemented: the optional `checksum` field (it would mean hashing the file before the upload).
- **403 is ambiguous** in Moonraker (a wrong or unauthorised key, and also "file is loaded, upload not permitted" for the file being printed), so its message says both; 409 is "busy, not ready, or printing a file with the same name"; everything else maps as for OctoPrint (`webapi.status_error`).
- **Not verified against a real Moonraker**: the fake follows the public API documentation (the answers above, `/printer/info` 503 while Klipper is down, 403 for the file being printed); the shapes and statuses are assumptions until the hardware matrix (plan, M9).

### 8.4 Bambu LAN (Developer Mode only, unsupported by Bambu)
The UI labels this mode **"Bambu LAN mode (unsupported by Bambu)"**. It uses only the printer's **access code** over open protocols on the local network.

**Do not**, in code, docs or support answers:
- use Bambu's cloud API or any cloud endpoint;
- bundle, load, download or emulate Bambu's network plugin;
- extract or use Bambu Connect keys or certificates;
- identify as, or imitate the client fingerprint of, Bambu Studio, Bambu Connect or Bambu Handy;
- work around, retry around or otherwise bypass the printer's authorization mechanism.

Protocols:
- **FTPS, implicit TLS, port 990**, user `bblp`, password = access code. `ftplib.FTP_TLS` is explicit-TLS only [V] and blocks, so the add-on has its own non-blocking client (below), which **offers the control connection's TLS session on the data channel**. Uploads `<name>.gcode.3mf` to the storage root.
- **MQTT 3.1.1 over TLS, port 8883**, same credentials; publish to `device/<serial>/request`:
  ```
  {"print":{"command":"project_file","param":"Metadata/plate_1.gcode","url":"ftp:///<name>.gcode.3mf","use_ams":…,"ams_mapping":[…],"bed_leveling":…,"timelapse":…,…}}
  ```
  and subscribe to `device/<serial>/report`. Our own minimal client (CONNECT, SUBSCRIBE, PUBLISH QoS 0 and 1, PINGREQ, DISCONNECT; `core/network/mqtt.py`); no paho.
- **TLS trust**: trust on first use; the leaf SHA-256 is pinned per printer after the user confirms it.
- **Authorization.** Since early 2025, firmware blocks third-party control outside LAN-only mode with Developer Mode enabled (Bambu wiki: "Enable Developer Mode"; affected firmware versions unverified). On any authorization-denied reply or refused login, the client **stops** (no retry, no fallback) and shows a message pointing to Developer Mode.
- **AMS mapping** from each slot's `ams_slot` (auto = index). Multi-nozzle (H2D) profiles slice in v1, and **H2D send is in v1**: it maps AMS slots per nozzle from the engine's `nozzle` field and the filament maps, and is checked on real hardware before release.

Implemented as follows, layer 1 (FTPS upload; `core/network/ftps.py`, `core/network/bambu.py`, `nbsocket.start_tls`, the Bambu paths of `core/send.py` and `blender/connections.py`; tests `test_net_ftps.py`, `test_net_bambu.py`, the Bambu tests of `test_send.py`, `test_printers.py`, `check_send.py`; `FakeFtpsServer` in `tests/fake_net.py`):
- **Sources.** RFC 959 (FTP) and RFC 4217 (FTP over TLS) for the protocol; the OpenBambuAPI community documentation for the facts about the printer (`ftp.md`: `ftps://<ip>:990`, implicit TLS, user `bblp`, password the LAN access code; `mqtt.md`: the `project_file` URL forms `ftp:///<name>`, `ftp:///cache/<name>`); a community note that a project started from `cache/` can fail on some firmware while one in the FTPS root works. No code of any other client was read or translated.
- **Where the file goes: the storage root `/`**, under the file name sent (a `.gcode.3mf`). The root is what `ftp:///<name>` (layer 2's start URL) addresses; `/cache` is not used. Not verified on a printer: some models may want another place, which the hardware matrix decides. Bambu takes **only** a `.gcode.3mf` here (the panel refuses plain G-code) and **no folder** (the Folder field is hidden and refused).
- **Non-blocking client** (`ftps.py`, generators like every other client): implicit TLS handshake and pin check first, `220` greeting, `USER`, `PASS`, `PBSZ 0`, `PROT P`, `TYPE I`, `PASV`, `STOR`, the file in 64 KB slices, `226`, `QUIT`. Replies are parsed with limits (4 KB lines, 200 lines); the error text holds the target and the reply **code** only, never the server's text. `FtpsEndpoint` keeps the access code as a `SecretStr`; `reveal()` is called once, to build the `PASS` line.
- **Data channel TLS and session reuse.** `PROT P` is demanded: a server that refuses it gets **nothing in the clear** (`DataChannelError`). The data connection is opened (plain TCP) *before* `STOR` and its TLS handshake runs *after* the server's `125`/`150`, as RFC 4217 servers expect. It **offers the control connection's TLS session** (`nbsocket.start_tls(..., reuse=control)`, which has to use the very `SSLContext` the session came from: Python raises "Session refers to a different SSLContext" otherwise), so servers that require reuse (vsftpd's `require_ssl_reuse`) accept it and servers that do not simply ignore the offer. Under TLS 1.3 the session is a ticket the server sends after the handshake; the client has read it by the time it has read the greeting (the fake server checks `session_reused` on the data connection; against a real Bambu server this is **unverified**). The data connection is pinned like the control one: the pin is compared before the first file byte.
- **Passive mode only, and the address in the `227` reply is ignored**: the data connection goes to the address the control connection uses, so a device cannot send the client to a third host. No `EPSV`, no active mode.
- **End of file.** FTP has no length: the TLS `close_notify` on the data connection is the end-of-file mark. After the last byte the add-on sends it (best effort, never waiting for the peer's: some embedded servers never answer it) and waits for `226`. On cancel or any failure it sends **no** `close_notify` and shuts the TCP connection down, so the printer sees a truncated stream rather than a file that looks complete. (Observed on macOS: a plain `close()` of the data socket did not reach the peer in the test, `shutdown(SHUT_RDWR)` did.) Cancel closes the generator, which does exactly that, then sends a best-effort `QUIT`.
- **Errors** (`errors.py`): `LoginRejected` (530/532; **stops, one attempt, no retry**, the message says to check the access code and that the printer must be in LAN Only mode with Developer Mode on, "Bambu does not support third-party tools"), `PermissionDenied` (550/553 to `STOR`), `StorageFull` (452/552, also read from the control channel after a data connection that dropped), `DataChannelError` (no `PROT P`, no passive mode, data port unreachable, data TLS refused, 425/426) and `FtpError` for the rest. TLS pin errors pass through untouched (below).
- **TLS: always pinned.** `core.printers.tls_policy` returns a pinning policy for every Bambu record (the HTTP kinds still pin only with `tls = pin`); a Bambu record has no `tls` option, so "verify" cannot be chosen and nothing turns checking off; `BambuEndpoint` refuses a non-pinning policy. A first connection ends with `TlsUnknownCertificate` before the user name is sent (the fake server records no byte), the Printers box and the Send panel offer "Review and trust certificate..." (§8.5), and a different certificate is `TlsPinMismatch` with only "Forget trusted certificate..." as the way out: the imposter test shows it receives neither the access code nor any command.
- **Connection test** (`bambu.test_connection`): login over FTPS, then `LIST` of the root over a TLS data channel, so a broken passive range or a refused TLS session shows up before a send. A refused login or a TLS refusal raises; a data channel that fails is *reported* in the summary ("..., but the data channel failed: ...") because login worked. The summary starts "Bambu LAN mode (unsupported by Bambu): access code accepted over FTPS, certificate trusted, ...". It stores nothing. (Layer 2 adds the MQTT state.)
- **UI.** Everything that names the mode says "Bambu LAN mode (unsupported by Bambu)" (kind label, Printers box, Send panel). The Send panel offers Bambu printers, defaults the file to `.gcode.3mf` and hides Folder. `core.send.SUPPORTED` has all three kinds; `connections.build_endpoint` returns a `BambuEndpoint` for Bambu (no base URL), reading the access code from the keychain just before the call. (Starting a print is layer 2, below.)
- **Not verified against a real printer**: the fake follows RFC 959/4217 and the OpenBambuAPI text. Open questions for the hardware matrix: the root versus another directory on each model, session reuse behaviour, whether `LIST` works the same, whether a printer insists on something the fake does not (a `SITE`, a `TYPE` order, a delay after the upload before a print starts).

Implemented as follows, layer 2 (MQTT status and start print; `core/network/mqtt.py`, the MQTT parts of `core/network/bambu.py`, `core.send.ams_mapping`, the print path of `blender/sending.py`; tests `test_net_mqtt.py`, `test_net_bambu.py`, `check_send.py`; `FakeMqttBroker` in `tests/fake_net.py`):
- **Sources.** The OASIS MQTT 3.1.1 specification for the protocol. For the messages: the OpenBambuAPI `mqtt.md` (port 8883 with TLS, user `bblp`, password the LAN access code, topics `device/<serial>/report` and `.../request`, `{"pushing":{"sequence_id","command":"pushall","version":1,"push_target":1}}`, `{"info":{"sequence_id","command":"get_version"}}` with the `module` list and `sw_ver`, the `print.project_file` fields and the `ftp:///<name>` URL forms) and a community protocol reference (the `bambu-printer-manager` MQTT protocol reference: the `project_file` field list as a working client sends it, `ams_mapping` as an array of absolute tray ids with `-1` for an unused filament, `bed_leveling` spelling, the printer answering on the report topic with the command name and `result`/`reason`). Where they disagree the choice is recorded: the key is `bed_leveling` (OpenBambuAPI's prose spells it `bed_levelling`; the reference and 03's own example use `bed_leveling`); `ams_mapping` is an array (OpenBambuAPI's example shows an empty string and prose describing an array). No code of any other client was read or translated.
- **Minimal MQTT client** (`mqtt.py`, generators on `nbsocket`, no third-party code): `CONNECT` (our own client id `slicewright-<8 hex>`, user, password, clean session, keep alive 30 s), `CONNACK`, `SUBSCRIBE`/`SUBACK`, `PUBLISH` QoS 0 and 1 (`PUBACK`), incoming QoS 0 and 1 (a QoS 1 message is acknowledged; QoS 2 is a protocol error), `PINGREQ` when the line has been quiet for half the keep alive, `DISCONNECT` (best effort, on close). Packets are parsed from a byte stream with limits (4 MB; a malformed length is an error), the inbox keeps the newest 256 messages. The TLS handshake and the pin check come first, so the password in `CONNECT` is never sent to a device that failed its check. A `CONNACK` of 4 or 5 is `LoginRejected` (**stops, one attempt**, message: the access code, LAN Only mode, Developer Mode, unsupported by Bambu); other codes are protocol errors.
- **Connection test** (`bambu.test_connection`): the FTPS check of layer 1, then MQTT login, a subscription to the report topic, `pushall` and `get_version` (QoS 0) and up to 8 s of reports. The summary reads "Bambu LAN mode (unsupported by Bambu): access code accepted (FTPS and MQTT), certificate trusted, printer state IDLE, firmware 01.08.00.00, storage reachable (2 entries in its root)". A printer that logs in but reports nothing (almost always a wrong serial number: the broker accepts a subscription to any topic) is *reported* ("sent no status for serial number ..."), not an error. The serial is checked (4 to 32 letters and digits) before any connection, so no wildcard or separator can get into a topic.
- **Start print** (`bambu.upload(..., start_print=True, options=PrintOptions)`; the panel's checkbox plus the popup "Start printing?" naming the printer and the file, a script's `confirm_print=True`, exactly as for OctoPrint; only the identity `True` reaches here). After the FTPS upload and a 1 s pause (some firmware needs one; `PrintOptions.settle`), the client connects over MQTT, asks for the status, **refuses to send the command to a printer that reports `PREPARE`, `RUNNING` or `PAUSE`** (`PrinterBusy`, no command sent) or one that reports nothing, then publishes (QoS 1) `{"print":{"sequence_id":"3","command":"project_file","param":"Metadata/plate_1.gcode","project_id":"0","profile_id":"0","task_id":"0","subtask_id":"0","subtask_name":"<name without .gcode.3mf>","file":"","url":"ftp:///<name>","md5":"","timelapse":false,"bed_type":"auto","bed_leveling":true,"flow_cali":false,"vibration_cali":false,"layer_inspect":false,"use_ams":<bool>,"ams_mapping":[...]}}`. Calibration, inspection and timelapse stay off; the panel offers **Use AMS** and **Level the bed** when a print is asked for.
- **What is reported as selected or printing comes only from the printer's own reports.** `selected` = a report with `print.command == "project_file"` and `result` `success` (any case); `printing` = a `gcode_state` of `PREPARE` or `RUNNING` in a report whose `gcode_file`/`subtask_name` is empty or names our file (another file printing is not taken for ours). The broker's `PUBACK` is not evidence. Waiting is at most 20 s; then the result says "the printer did not confirm the print command" (nothing heard), "refused the print command: <its reason>" (`result` not success) or "accepted the command but has not reported that it is printing". The panel shows a warning "The printer did not start the print" with that note in all but the confirmed case; the file stays on the printer. A refused MQTT login after the upload, a busy printer or silence never turns into "started"; a cancel, the online-access gate and certificate decisions (unknown or changed certificate) raise instead of becoming a note.
- **AMS mapping** (`core.send.ams_mapping`, read in `sending._bambu_options`): one entry per filament slot of the scene, the slot's `ams_slot` or its own index when that is automatic, and `-1` for a filament the slice's `filament_per_extruder` says the plate does not use. Only sent with **Use AMS**; otherwise `use_ams` is false and the array empty. **Not done: H2D (dual-nozzle) mapping** (`ams_mapping2`, per-nozzle slots, the integer-flag variant some H2 firmware wants): single-nozzle printers only until the hardware check, so 03's earlier "H2D send is in v1" is still open work.
- **Same security as FTPS**: pinned certificate for MQTT as for FTPS under the same per-printer pin (a printer whose two services present different certificates would need a second pin: **unverified**), the imposter test (another certificate on the broker: no `CONNECT`, no password, no byte), the access code in no message, repr, note or log (`test_the_access_code_never_appears_in_...`), cancel closes the connection (`DISCONNECT` or a plain close), no retry of a refused login.
- **Not verified against a real printer**: the fake follows the sources above. Open for the hardware matrix: that the real broker replies to `project_file` the way the fake does (and what `gcode_file`/`subtask_name` hold), whether `ftp:///<name>` (root) starts the file on every model or some want `file:///sdcard/<name>` or `/cache`, file names with spaces or non-ASCII in the URL, whether one pin serves both ports, the `pushall` rate limits some firmware has, and H2D.

### 8.5 Credentials
Printer records in AddonPreferences, without secrets. Secrets in the OS keychain via the stdlib: macOS `/usr/bin/security` (`-s slicewright -a <printer-uuid>`), Windows `advapi32.CredWriteW/CredReadW` via `ctypes`, Linux `secret-tool` when present. Fallback: a `0600` file with an explicit "stored unencrypted" notice. No `keyring` wheel. **Secrets never enter the .blend.**

Implemented as follows (layer `secrets`: `core/redact.py`, `core/network/secrets.py`, `blender/secrets_rt.py`; tests `test_redact.py`, `test_secrets.py`, `check_secrets.py`):
- **Interface** `SecretStore` (`available/get/set/delete`, keyed by an opaque `secret_ref` of 1-64 letters, digits, `_`, `-`; `new_ref()` gives a UUID) and `SecretManager` (one active backend chosen at construction: the OS keychain if `available()`, else the file; a runtime failure of an available keychain is reported to the user and **never** falls back to the file silently; a secret saved in the file before a keychain was used is still found and deleted with the rest). Secret values are 1-4096 characters without control characters.
- **macOS** (`MacKeychainStore`, service `slicewright`, account = the ref): `/usr/bin/security`. The secret is **never an argument** (it would be visible to `ps`): `set` runs `security -i` and writes `add-generic-password -U -s <svc> -a <ref> -X <hex>` to its standard input; `get` runs `find-generic-password -s <svc> -a <ref> -w` (the password comes back on standard output); only service and ref are ever arguments. Verified on macOS 26: `-i` returns the command's exit status (44 for a missing item, 128 for a denied or cancelled prompt). The stored text is `sw1:<hex of the UTF-8 secret>`, because `security -w` prints a password containing any non-ASCII byte as bare hex, which a secret that happens to be hex could not be told apart from. Errors are fixed text plus the exit status, never the tool's output. The call is a subprocess the add-on waits for (timeout 10 s): it covers a locked keychain asking to be unlocked, and runs only when the user saves or uses a secret.
- **Linux and Windows** (`LibsecretStore`, `WindowsCredentialStore`): stubs that report themselves unavailable ("not implemented"), so those platforms use the file. macOS is the only keychain backend.
- **File fallback** (`FileSecretStore`, `<user config>/slicewright/secrets/secrets.json`, via `blender/paths.py`): `{"format", "warned", "secrets"}`, written through a `0600` temp file in the same directory, `fsync` and `os.replace` (a failed write leaves the old file), directory `0700`. On every read it refuses a symlink, a non-regular file or a file owned by someone else, and repairs a file readable by group or others (`repaired` is shown in the status). A damaged file is an error, never silently replaced. It is not encrypted: `take_warning()` returns the loud warning once per user (the flag is saved in the file; only once a secret is in it), and the preferences' Secrets box always says which backend is in use, with the unencrypted file in red.
- **Redaction** (`core/redact.py`): `SecretStr` (`str`, `repr`, `format` give `***`; not picklable), a session registry of every secret a store has read or written (masked wherever they appear) and patterns for credential-shaped text (`X-Api-Key: ...`, `access_code=...`, `password`, `Authorization: Bearer ...`, `user:pass@host`). `RedactingFilter` is attached to every handler `core/logs.py` installs and scrubs the message, arguments, traceback and stack. `unregister` forgets the manager and the registry.

Printers and the online-access gate (layer `printers-prefs`: `core/printers.py`, `core/network/access.py`, `blender/printers.py`, `printers_pg.py`, `online.py`, `ui/printers_box.py`; tests `test_printers.py`, `test_net_access.py`, `check_printers.py`):
- **Records** (`PrinterRecord`, stored as a `CollectionProperty` of `SLICEWRIGHT_PG_Printer` in the add-on preferences): `id`, `name` (unique, case-insensitive), `kind` (`octoprint` | `moonraker` | `bambu_lan`), `host` (a host name or IP literal: no scheme, port, path or user name, no brackets), `port` (0 = the kind's default: 80, 7125, 990; 443 with HTTPS; Bambu's MQTT port 8883 is fixed), `options` (whitelisted per kind: `https`, `tls` = `verify` | `pin`, `base_path`, `pin` for OctoPrint and Moonraker; `serial` and `pin` for Bambu; a key that looks like a credential is rejected by name), `preset` (the Slicewright printer preset it pairs with, a `printer_id` with search), `secret_ref` and `secret_saved`. **There is no secret field**: the record only knows whether a secret was saved, so drawing the UI never asks the keychain. `validate(record, others, complete=...)` returns the problems; `complete=True` adds what sending needs (a host, the Bambu serial, a saved secret where one is required; the Moonraker key is optional). The preferences draw the problems in red under each printer.
- **Secret entry.** A `StringProperty(subtype='PASSWORD')` on a saved Blender data block would be written to the file, so the field is a property of the *dialog operator* `slicewright.printer_secret` only: `SKIP_SAVE` (Blender does not remember it as the last-used value), no `REGISTER` flag (not logged in the Info editor, not offered for redo). `execute` copies it into a local, blanks the property, hands the local to the store and drops it. Tests save the .blend and (in a child Blender with a temporary config directory) the user preferences, and search the raw bytes for the secret; the preferences file does contain the record and its `secret_ref`, which is what makes that search meaningful. `printer_remove` deletes the secret first and keeps both if the store refuses; `printer_secret_clear` forgets it.
- **Gate.** `network_allowed()` (in `core/network/access.py`, installed by `blender/online.py`) returns `Access(allowed, reason)` read afresh from `bpy.app.online_access`; the reason distinguishes the preference ("Preferences > System > Network > Allow Online Access") from `--offline-mode` (`bpy.app.online_access_override`). Observed on Blender 5.1.2: background mode with `--factory-startup` starts with `online_access` False; `--online-mode` gives True with the override flag True, `--offline-mode` False with the override True. With no provider installed (plain Python, or after `unregister`) the answer is no. `nbsocket.connect()` calls `require_network()` as its first line, so no socket is opened by any code path that forgot to ask; the unit tests install an "allowed" provider for their loopback servers. UI: `online.draw_gate(layout)` writes the red reason line (the Printers box uses it), `online.poll_network(cls)` is for an operator's `poll` (greys it, tooltip is the reason), and a panel greys a block with `layout.enabled = network_allowed().allowed`. **Wording in the panel** (2026-11-22, after a real-window review: the two-sentence reason was cut mid-sentence): `Access` carries a `short` text next to the full `reason`. The Send panel and the printer box draw a red headline "Network features are off", ONE short wrapped instruction ("Turn on Allow Online Access in Preferences > System > Network."; for `--offline-mode`: "Blender was started with --offline-mode. Restart it without that option.") and, for the preference case, an "Open Network Preferences" button (`slicewright.show_online_prefs`: `screen.userpref_show(section='SYSTEM')`, which changes nothing) whose tooltip is the full `reason`; disabled buttons keep the full reason as their tooltip. The path is the one Blender 5.1.2 has (`bl_ui/space_userpref.py`: panel "Network" in the System tab, property `use_online_access` labelled "Allow Online Access"); `check_send.py` reads that script and fails if the words ever stop matching.
- **Certificate pins** (`blender/printers.py`, `PrefsPinStore`): the `PinStore` of the add-on keeps a printer's trusted leaf SHA-256 in that printer's preferences entry (`pin`, which is `options["pin"]` of its record, validated as 64 hex digits). A fingerprint is public data, not a secret, so it lives with the record in the preferences (it never goes in the keychain) and goes wherever the preferences go; it is keyed by the printer `id`, read from the live preferences on every call (a removed printer has no pin) and a damaged value counts as no pin. `core.printers.tls_policy(record, store)` gives `TlsPolicy(pin)` when `tls = pin` and the normal verification otherwise; `blender.printers.tls_policy_for(record)` is what every client uses. The flow for `TlsUnknownCertificate`: the failing operation calls `printers.note_tls_problem(printer_id, exc)`, which remembers the fingerprint (with the host and port that were contacted); the Printers box then shows "Review and trust certificate..." (`slicewright.printer_trust_cert`), a dialog that names the printer, host, port and the SHA-256 in `AB:CD:...` form; **only the user's confirmation stores the pin**, and only a fingerprint a connection actually saw can be confirmed (the operator takes just the printer id), only for the address that was contacted (an edit of host or port in between refuses it), and never over an existing pin. Afterwards the operation is run again. A `TlsPinMismatch` is shown as a red "different certificate than the one you trusted" line with no trust button; the only way out is the separate `slicewright.printer_forget_pin` operator (confirm popup, explains it is for a replaced or reset device), after which the new certificate is offered again as unknown. Nothing is ever repaired automatically, and the failed handshake check happens before the first byte is sent, so no API key reaches an imposter (tested end to end with two fake HTTPS servers). The pending problems are runtime state, dropped on unregister.
- **Manifest.** `[permissions] network = "Send G-code to OctoPrint, Klipper or Bambu printers on LAN"` (03 section 10.2); `blender --command extension validate` accepts it.

### 8.6 Async I/O, permissions, online access
- Network operations are **tick-driven state machines** on non-blocking sockets (`ssl` with `SSLWantRead/Write`, `select(…, 0)`), advanced by a timer with ~15 ms per tick, streaming multipart in 256 KB chunks. The same code runs blocking in unit tests against fake servers.
- Every network operator is user-initiated and its `poll()` requires `bpy.app.online_access` [V]; LAN printers count as network access.
- No telemetry, update checks, downloads or remote code.

Implemented as follows (layer `net-core`, `core/network/`, no `bpy`, no threads except the lookup worker below; tests `test_net_parser.py`, `test_net_http.py`, fake servers in `tests/fake_net.py`):
- **Generators, not state-machine classes.** Every operation (`nbsocket.connect`, `httpclient.request`, `fetch_fingerprint`) is a generator: one `yield` is one bounded non-blocking step (a `connect_ex` poll, a handshake attempt, one `send` of at most 256 KB, one `recv` of at most 64 KB). It yields `ticking.IDLE` when the socket would block, a float for upload progress, and returns its result. The `TickRunner` runs it from the timer (cancelling the task closes the generator and so the socket); `nbsocket.run_blocking` runs the same generator blocking, for tests. When a whole round of tasks yields `IDLE` the runner stops the tick early and asks to be called again after `idle_interval_s` (20 ms) instead of spinning for the budget. No `select` on the hot path: each step just tries the operation (`SSLWantRead/Write` and `BlockingIOError` mean "not yet").
- **Host-name lookup** (`resolve.py`): `getaddrinfo` has no timeout and no non-blocking form, and a `.local` name that mDNS cannot answer can keep it waiting for many seconds, so a host *name* is resolved in a short-lived daemon **worker thread** and `resolve()` is a generator that yields `IDLE` until the thread answers, `Timeouts.resolve` (4 s) passes or the task is cancelled. This is the add-on's **one use of a thread** and the only module `test_code_rules.py` allows to import `threading` (an explicit allowlist of that one file; the rule "no threads, processes or event loops" holds everywhere else, including slicing, §6): the thread runs only the injected resolver, never touches `bpy` or add-on state, and is a daemon. A lookup the OS never finishes is abandoned, not killed (a thread cannot be): its answer is dropped, and after 8 such stuck lookups new ones are refused with "use the IP address" instead of piling up threads. IP literals skip the thread. The `connect` timeout starts after the lookup. The timeout message tells the user to use the printer's IP address.
- **Timeouts** (`Timeouts`): `resolve` bounds the host-name lookup, `connect` bounds TCP plus the TLS handshake (it starts when the lookup is done), `idle` is "nothing moved either way", `total` is optional. **Cancel**: a `CancelToken` checked every step, or closing the task.
- **HTTP/1.1** (`Connection: close`, one request per connection): the response is read while the body is sent, so an early `401`/`413` is heard and the upload stops; the request is sent only after the TLS policy accepted the peer; limits on head size, header count, line length and body (`Limits`, 32 KB / 100 / 8 KB / 1 MB, enforced as bytes arrive, chunked or not); redirects are **not** followed by default, with `same-host` only the same host, never HTTPS to HTTP, a changed port only for an http to https upgrade, at most 3, and a `POST` is repeated only after `307`/`308` (a `302` of a `POST` raises `RedirectRefused` rather than silently becoming a `GET`). Credentials in a URL are refused (use a header); header values with control characters are refused. `Response.raise_for_status()` raises `HttpStatusError` whose message is the status line only.
- **Multipart** (`multipart.MultipartBody`): exact `Content-Length` up front (no chunked request encoding), the file read in 256 KB chunks, replayable, a file that shrinks mid-send is an `UploadError`.
- **TLS** (`tls.TlsPolicy`): `verify` (default; optional `cafile` for a private CA) or `pin` (trust on first use: the leaf SHA-256 is compared with `PinStore.get(pin_key)`; no stored pin raises `TlsUnknownCertificate` carrying the fingerprint, a different one raises `TlsPinMismatch`, which is never repaired automatically). `fetch_fingerprint(host, port)` returns what a device presents so the UI can ask the user before `PinStore.set`. `PinStore` is an interface (`get/set/remove`) with `MemoryPinStore`; the add-on's persistent one is `PrefsPinStore` (§8.5).
- **Errors** (`errors.py`): `NetError` with `InvalidRequest`, `ResolveError`, `ConnectionRefused`, `Unreachable`, `ConnectionLost`, `Timeout(phase)`, `TlsError` (`TlsVerificationError`, `TlsUnknownCertificate`, `TlsPinMismatch`), `ProtocolError`, `LimitExceeded`, `RedirectRefused`, `HttpStatusError`, `UploadError`, `Cancelled`. Messages hold the target and a kind or status line, never a header, a query string, a body or a credential (tested).
- **Test certificates** are made at test time with the `openssl` command line (a throw-away EC key in a temp directory); nothing key-like is checked in, and the TLS tests skip without `openssl`.

### 8.7 Sending to OctoPrint and Moonraker (panel, operators, connection test)
Implemented as follows (layers `send`: `core/send.py` no `bpy`; `blender/connections.py`, `blender/send_pg.py`, `blender/sending.py`, `blender/operators/send.py`, `blender/ui/send_panel.py`, the Printers box; tests `test_send.py`, `check_send.py` on both engines, `check_printers.py`, `check_registration.py`). Bambu printers are sent to over FTPS and can be asked to start the print over MQTT (§8.4); "printing" is reported only from the printer's own report.
- **Send panel** (under Export; `slicewright.send`, `send_cancel`, `send_confirm`). The choices live on the **window manager** (`WindowManager.slicewright_send`, `SLICEWRIGHT_PG_Send`), not the scene: a window manager's custom properties are not written to the .blend, so "Start print after upload" can never come back switched on from a file and a printer name from another machine's preferences never ends up in a shared scene; they last for the session. Fields: **Printer** (a search field over the printer names; with exactly one eligible printer and none named, that one is used), a filter button for **all printers** (by default only printers whose paired preset equals the scene's printer preset, plus printers with no pairing, are offered), **File** (`Printer default` = plain G-code for OctoPrint and Moonraker, `G-code`, `.gcode.3mf`), **Name** (empty = the engine's `output_filename`, with the extension of the chosen format; a typed name is made portable: `/` and Windows-forbidden characters become `_`, an extension is only ever added), **Folder** (optional, a plain relative path; `..`, empty or hidden parts are refused before anything is made) and **Start print after upload** (off). Buttons: **Send to <printer>** and **Test connection** (an icon button). While a file goes out a progress bar with Cancel replaces them.
- **Greyed out, with the reason as the line under the buttons and the button's tooltip:** online access off (Blender's own reason, or the `--offline-mode` one), a send already running, no slice ("Slice the plate to send it"), a slice or export running, the cached preview still loading, no printer chosen, an incomplete printer (the first problem of `validate(complete=True)`: host, API key), Bambu not yet supported, a `.gcode.3mf` that cannot be made any more (out of date and the engine's result is gone), a print requested for a `.gcode.3mf` (neither OctoPrint nor Moonraker can print one). The gate is also checked again by `nbsocket.connect`, so turning online access off after the start still stops the upload (tested: nothing reaches the server, the message is the gate's).
- **What is sent** is what the Export panel would write: the result the preview shows. The file goes to a private `0700` temp directory first (`tempfile.mkdtemp`): G-code through the export's atomic write (a copy of the engine's `out.gcode`, byte for byte), a `.gcode.3mf` through `exporting.export_3mf(quiet=True, issues_source="send", then=...)`, which writes at once from a live result and otherwise **slices again first** (the same re-slice as the export, shown as "Slicing again..." with the export's progress; Cancel cancels it). `quiet` keeps the Export panel's message and the remembered export folder out of it, and `exporting.availability(verb="send")` words the shared reasons. The upload reads **from that file** in 256 KB chunks as a task on the tick runner (the generator of `core/send.upload_gen`); the temp directory is removed however the send ends (done, failed, cancelled, a file load, unregister), checked by every test.
- **Out-of-date preview:** like the export, a confirm popup ("Preview out of date ... Send anyway") before anything happens; a script call needs `allow_stale=True`.
- **Start print:** only the checkbox plus the popup **"Start printing?"** (naming the printer and the file, "Upload and start print") start one, and only `start_print is True` reaches the client (strict identity). A script call needs `confirm_print=True`, otherwise it is cancelled with a warning and nothing is sent. If a print was asked for but the printer did not confirm it (OctoPrint's `effectivePrint`, Moonraker's `print_started`), the message is a warning: "The printer did not start the print". In `-b` `INVOKE_DEFAULT` runs `execute` directly and the popups answer "yes" at once, so the tests call the operator's `invoke` and the popup operator's `execute` themselves; the popup text is checked through a stand-in window manager (nothing has been seen in a real window).
- **Result:** the Send panel line and the status bar (kept 8 s after the end through a tracked timer), and for a failure an `error` in the Issues list, source `send` (code `send_<kind>`: `send_auth_rejected`, `send_conflict`, `send_too_large`, `send_http_status`, `send_lost`, `send_refused`...), cleared by the next send or file load. Messages are the errors' own text (origin and status line only), passed through `redact.redact`; the secret is read from the keychain just before the upload (`connections.build_endpoint`), wrapped in a `SecretStr` held only by that upload's endpoint, never stored, logged or shown (tests: the key never appears in a status line, an Issues entry or the keychain's items besides itself).
- **Cancel** closes the upload task's generator, which closes the socket, so the printer sees an aborted request. If every byte had already left the computer (a small file fits in the socket buffers) the printer may still finish storing the file: Cancel cannot take back what was sent. The mid-upload test pads the file to 6 MB so the transfer is really in flight.
- **Connection test** (`slicewright.printer_test`, `connections.py`): a button in each printer's box in the Preferences and next to Send in the panel. One generator per printer on the tick runner with short timeouts (connect 5 s, idle 8 s), started only by the click and only while `network_allowed()`; the result (versions and state, or the error text) is runtime state drawn under the printer. Refused or failed tests are expected outcomes, returned by `_guarded` instead of raised, so the tick runner does not log a traceback. A TLS refusal that needs a decision is recorded (`printers.note_tls_problem`) and the box and the panel then offer "Review and trust certificate..." or, for a mismatch, only "Forget trusted certificate..." (§8.5); after trusting, the operation is run again by the user.
- **Registration:** stages `blender.connections` (no engine needed) and `blender.sending` (engine) follow `blender.printers`; the operators and the panel are in the engine groups. `unregister` removes the window-manager pointer, the PG, the pending problems, the tests and the send state, and cancels the send task; `check_registration.py` asserts all of it.

---

## 9. Code structure, testing, logging

### 9.1 Package layout
```
addon/
  LICENSE                               # GPL-3.0 text
  slicewright/                            # extension root
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
      operators/  ui/  paint/  preview/ (renderer shaders glsl/ thumbnails)  bake_data.py baking.py bake_pg.py (bake: stage, datablocks, choices)
    wheels/        # slicewright_engine-<ver>-cp312-abi3-<platform>.whl (from PyPI, pinned + hashed)
  tools/ (fetch_wheels build_all make_repo)
  tests/ (unit blender gui contract fixtures fake_engine)
```
Registration order (the `STAGES` tuple): prefs → library → config_pg (after engine import) → props → issues → operators → ui → preview → plate → paint → slicing → exporting → baking → online → secrets → printers → connections → sending → staleness → timers → handlers; unregister reverses it. The preview settings group (`SLICEWRIGHT_PG_preview`) is registered by the props stage, just before `Scene.slicewright`, which holds it as `Scene.slicewright.preview`; there is no separate `Scene.slicewright_preview`. A test asserts no handlers, timers or draw handlers remain.

### 9.2 Fake engine (`tests/fake_engine`)
Implements all of 04, including the state machine (with `validating`), typed exceptions and `Busy`, and passes the shared contract suite (04 §11). It is the single dev backend.

**Backend selection (M8).** The add-on imports `slicewright_engine` by default; the fake is an explicit dev switch, `SLICEWRIGHT_ENGINE_MODULE=fake_engine` (only these two names are accepted, `engine/adapter.py`). There is no wheel in the personal build: Blender does not read `PYTHONPATH`, so `SLICEWRIGHT_ENGINE_PATH=<dir holding the slicewright_engine package>` (e.g. `engine/build/engine/python`; `os.pathsep`-separated) is put on `sys.path` before the import, or symlink the package into the user `scripts/modules` directory (`mkdir -p ~/Library/Application\ Support/Blender/5.1/scripts/modules && ln -s "$PWD/engine/build/engine/python/slicewright_engine" ~/Library/Application\ Support/Blender/5.1/scripts/modules/`; verified; `--factory-startup` ignores user scripts, so the tests use the variable) for launches without a shell. Without the module the add-on registers only its diagnostics panel. The pytest suite (`addon/tests/conftest.py`) finds the build directory itself and exports the variable to the Blender subprocesses; `--backend=fake|real` selects the contract backends, and the checks that need the real engine (`tests/real`, `tests/blender/check_real_engine.py`) skip when it is not built.
- **Schema and layout** from fixtures exported by the real engine. They are AGPL data, so they live in `engine/tests/fixtures/exported/` and are never packaged in the add-on.
- **`eval_condition`**: a small evaluator (`==`, `!=`, `=~`, `and`, `or`, `not`, indexing, numeric comparisons). **`compose_config`**: naive concatenation.
- **Synthetic slicing**: bounding-box layers with walls, zigzag infill, travel, retracts and plausible times; advances on `poll()`.
- **`from_gcode(path)`**: a parser for Orca G-code in both tag dialects (plain `;TYPE:`, `;WIDTH:`, `;HEIGHT:`, `;LAYER_CHANGE`; Bambu `; FEATURE:`, `; LINE_WIDTH:`, `; LAYER_HEIGHT:`, `; CHANGE_LAYER`), relative/absolute E, G2/G3. Written from the dialect description, so real 1M–20M-move prints can drive the preview before the native module exists.

### 9.3 Testing
- **Unit** (stock Python, three OSes): resolve, compat, user presets, importer, geometry, hashing, preview packing (incl. chunk boundaries and empty ranges), stats, export naming, multipart, MQTT packets, FTPS/HTTP against fake servers, ticking.
- **Golden profiles** (engine-enabled job): §3.5.
- **Headless Blender** (`-b --factory-startup`): register hygiene, ConfigPG generation, extraction (transforms, negative scale, modifiers, instances, unit scale), paint attributes via bmesh and `foreach_set`, orchestration with the fake (timers never fire in `-b` [V], so drive `poll_job` directly), cache reconnect, recovery copies, bake counts.
- **GPU** (scripted, per platform): slice a fixture, scrub, switch views, save screenshots, compared to references with a perceptual-diff threshold.
- **CI**: `extension build --split-platforms` and `extension validate` on every build.

### 9.4 Logging
`logging.getLogger("slicewright")` to the console and a rotating file under `extension_path_user(...)/logs/`; engine logs via `sc.set_log`. "Copy diagnostics" collects versions, OS, GPU backend and log tails.

---

## 10. Packaging the extension

Licences, store policy, naming, the manifest's `license`/`copyright` rationale and the Bambu legal context are in [compliance.md](../publishing/compliance.md).

### 10.1 Build pipeline
1. `tools/fetch_wheels.py` downloads the pinned `slicewright_engine` wheels **unmodified from PyPI**, verifying hashes.
2. `blender --command extension build --split-platforms` produces `slicewright-<ver>-{macos_arm64,windows_x64,linux_x64}.zip`.
3. `blender --command extension validate` on each zip, plus our checks: size, no `__pycache__`, no binary assets, SPDX headers, manifest `copyright` format (each entry starts with a year).
4. The zips are attached to a GitHub Release (`addon-vX.Y.Z`). The `gh-pages` branch holds only `index.json` and HTML from `extension server-generate`, with `archive_url`s rewritten to the Release asset URLs (zips over 100 MB can't live in git).
5. If the moderators agree, the same zips go to extensions.blender.org with the store manifest variant.

### 10.2 `blender_manifest.toml` (self-hosted variant)

```toml
schema_version = "1.0.0"
id = "slicewright"
version = "0.1.0"
name = "Slicewright"
tagline = "An FDM slicer for Blender: slice, preview and send"   # ≤64 chars, no end punctuation
maintainer = "<maintainer> <contact@…>"
type = "add-on"
blender_version_min = "5.1.0"
license = ["SPDX:GPL-3.0-or-later", "SPDX:AGPL-3.0-only"]
copyright = [                       # illustrative; generated from THIRD_PARTY_LICENSES; every entry starts with a year
  "2026 Slicewright contributors",
  "2016-2026 OrcaSlicer contributors",
  "2011-2026 BambuStudio, PrusaSlicer and Slic3r contributors",
  "2013-2026 Ultimaker CuraEngine contributors",
  "1995-2026 CGAL, Boost, oneTBB, Eigen, GMP, MPFR and other contributors (see THIRD_PARTY_LICENSES)",
]
website = "https://<owner>.github.io/<repo>/"
tags = ["Import-Export", "Mesh", "Object"]
platforms = ["macos-arm64", "windows-x64", "linux-x64"]
wheels = [
  "./wheels/slicewright_engine-<ver>-cp312-abi3-macosx_11_0_arm64.whl",
  "./wheels/slicewright_engine-<ver>-cp312-abi3-win_amd64.whl",
  "./wheels/slicewright_engine-<ver>-cp312-abi3-manylinux_2_28_x86_64.whl",
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
