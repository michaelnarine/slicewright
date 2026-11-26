# UI screenshot tool

One command launches a real (non-background) Blender on a scratch profile, drives the add-on through its main UI
states and writes PNGs of the actual window, so the UI can be reviewed by looking at images. Local use only; not
part of pytest (no `test_*.py` here) and not part of CI.

    python3 addon/tests/tools/run_ui_shots.py [--out DIR] [--engine real|fake] [--only a,b] [--scale 1.0]
                                              [--size WxH] [--blender PATH] [--timeout 480]

* `--out` defaults to `ui-shots-<timestamp>` under the system temp dir. Refuses a path inside the repo.
* `--engine real` (default) uses `engine/build/engine/python`; `fake` uses `tests/fake_engine`.
* `--only` takes step names; a prefix matches (`send_panel` = both Send shots, `settings` = all five).
* `--size` passes `--window-geometry` (Blender may ignore it when it opens a saved file; the default is the full screen).
* A run takes about 30 s on this Mac. Each step writes `<step>.png` (whole window) and `<step>_sidebar.png`
  (the Slicer sidebar region, cropped); the mode steps also write `<step>_header.png`. `manifest.json` lists every
  step with status (`ok` or `skipped` + reason), description, Blender version, engine, git SHA and timestamp.
  `blender.log` is Blender's output.

Steps: `prepare_empty`, `prepare_cube`, `prepare_multi`, `plate_mesh`, `plate_mesh_off`, `plate_mesh_preview`, `settings`, `settings_simple`, `settings_expert`, `settings_segmented`, `settings_segmented_three`, `slicer_workspace` (adds the Slicer workspace with the operator, shoots the window with and without a World, removes it again and asserts the removal),
`sliced_preview`, `preview_model_hidden`, `preview_model_ghost`, `preview_model_shown`, `preview_layers_features`, `preview_sliders` (also writes `preview_sliders_layers/_move/_dragged` and drives real window events with `--enable-event-simulate`, which the launcher passes: drag the top thumb and the bar, click the track, wheel and Shift-wheel, drag the move thumb, press away from the sliders, leave and re-enter Preview; results in the manifest `notes`), `preview_sliders_hidden`, `preview_sliders_unregister` (last: unregisters the add-on with the modal running), `mode_switch_preview`, `mode_switch_prepare`, `export_panel`,
`send_panel_gate_off`, `send_panel_gate_on`, `bake_panel`, `baked_result`, `issues`, `issues_ignored_open`, `preferences_printers`.

## How it works (and what it fakes)

* Launcher: creates a scratch dir (profile via `BLENDER_USER_RESOURCES`, `TMPDIR`, cwd; outside the repo), saves a
  base `.blend` with a headless Blender and opens it in the GUI one (no splash screen), runs `ui_shots.py`, and
  kills the process group if it outlives `--timeout`. The driver also quits Blender by itself after a watchdog.
* Driver: one `bpy.app.timers` generator; each step is try/except and a failure is recorded as skipped. The 3D
  viewport is maximised (no timeline/outliner), then `bpy.ops.screen.screenshot` inside a window override.
* Panels: a Blender panel's open state is not scriptable, so the driver re-registers the Slicer panels in their real
  order (sorted by `bl_order`, the same key Blender draws by: `names.PANEL_ORDER`) under a fresh `bl_idname` with `DEFAULT_CLOSED` set as wanted. Each step opens only the panels it is about.
* Preview shots rely on the add-on's own Preview option Model (default Hidden); the `preview_model_*` steps show Hidden, Ghost and Shown.
* Send: a fake OctoPrint printer (`192.0.2.1`, secret `EXAMPLE`) in an in-memory secret store and in-memory
  preferences (`use_preferences_save` is off); nothing touches the keychain or the network. "Gate on" swaps the
  network-access provider for one that answers yes; no connection is made.
* `preferences_printers` cannot show Preferences > Add-ons (the add-on is not installed as an extension there), so
  it draws the add-on's own `printers_box.draw` in a mirror panel in the Slicer sidebar. Real preferences are wider.
* The sidebar is narrow (about 560 px at this scale), so some labels truncate here exactly as they do for a user.

Safety: never writes outside `--out` and the scratch dir; never saves user preferences.
