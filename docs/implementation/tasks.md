<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Future tasks

Ideas and follow-ups that are not part of the milestone plan in `plan.md`. Add new items at the top of "Open". Development is on `main` only; nothing here is scheduled.

## Open

### Owner's UI and viewing list (2026-11-24)

The owner is keeping a list of issues and updates for when work resumes; this is the first batch, in their words. More will follow, so ask for the rest before starting. Do the plate-centre origin (below) with or before these.

- When slicing, the original mesh is still visible and blocks viewing the sliced output (hide or ghost the source meshes while Preview is showing).
- The layer viewer should also be a scrubbable slider, like in Bambu Studio (for planar slicing). A better scrubbing option for non-planar is to be worked out later.
- The colours for the path types are very washed out and look almost white (check the role palette and the shader's tone and saturation).
- Settings dropdowns with 3 or fewer options should render as segmented buttons, the way "tubes" vs "lines" does. More than 3 options stay a dropdown, unless the option labels are long (then a dropdown regardless).
- The slicer settings are a long list of settings. They need a better layout: less cluttered, organised into sections, and more web-like, as close as Blender's Python UI allows.
- Add a non-interactable mesh for the build plate (drawn, not selectable, not in the way of picking or Arrange).

### World origin at the plate centre

Requested 2026-11-24; owner chose "shift the whole frame". Today Blender world (0,0) is the bed corner (G-code origin, `printable_area` 0..256 on Bambu). Change: world (0,0) = centre of `printable_area`; the add-on adds the bed-centre offset when it hands objects to the engine and removes it for everything that comes back, so G-code coordinates stay correct. Touch points: `blender/extract.py` (instance matrices), `blender/arrange.py` (placements), preview and bake (move positions, travel), `blender/bed_draw.py` and `core/bed.py` (drawing, origin/axes), `core/checks.py` (printable-area test), the "Prime tower" empty (`wipe_tower_x/y`), `operators/plate.py` (plate add), `use_mm_scene`, paint/ZAA world normals (unaffected). Use one helper for the offset so it lives in one place. Update `docs/design/03-blender-addon.md` row "Coordinates" and `04-engine-api.md` bed frame note; add tests for corner-origin and centre-origin beds (e.g. delta printers already centred on 0, so offset is zero there). Deferred until the weekly limit resets.

### LAN printer library and discovery

Added 2026-11-24 at the owner's request.

Keep a library of registered printers and let the user find printers on the current Wi-Fi/LAN, then send a G-code or `.gcode.3mf` print job to any of them over the LAN.

What exists today (M9): manual printer records in the add-on preferences (host, kind, secret in the macOS Keychain, TLS pin), Send panel with OctoPrint, Moonraker and Bambu LAN (FTPS and MQTT), connection test, start-print confirmation.

What this task adds:
- **Discovery:** an explicit "Find printers on this network" action (never automatic, never a background scan), gated by the same online-access permission as sending. Candidates to evaluate, each to be verified before building:
  - Bambu LAN: SSDP-like `NOTIFY` announcements on UDP 2021 with HTTP-style headers: `Location` (IP), `USN` (serial), `DevModel.bambu.com`, `DevName.bambu.com`, `DevConnect.bambu.com` (community notes; some sources mention UDP 1990; not a Bambu-published spec). Home Assistant's `ha-bambulab` auto-discovers via SSDP and also accepts serial, IP and access code manually, so keep a manual path. Needs a real printer to verify.
  - OctoPrint: its discovery plugin announces `_octoprint._tcp` and `_http._tcp` over mDNS (default web port 5000). Moonraker: optional Zeroconf (default HTTP port 7125); its exact service type is unverified. mDNS is UDP 5353; a user-supplied subnet probe limited to known ports is the fallback.
  - macOS: browsing Bonjour or using local UDP broadcast can trigger the Local Network permission prompt (System Settings > Privacy & Security > Local Network), attached to the app that runs it (Blender); discovery must handle the permission being denied, and broadcasts do not cross VLANs. Research: `BlenderSlicer-local/research/lan-and-brick/lan-discovery.md`.
  - Discovery only proposes a record; the user confirms it, enters the access code/API key, and trusts the certificate (existing TOFU pin flow). No secret is ever auto-filled or sent during discovery.
- **Library:** named printers with a model/preset pairing, last-seen address (IPs change; re-find by serial or hostname), status (online, idle, printing), and a per-printer default format and folder. Optional "send to several printers" with an explicit per-printer confirmation for start-print.
- **Status view:** online/offline and basic state in the Send panel and the Printers box, polled only on user action or while the panel is open, within the tick budget.
- **Constraints to keep:** Bambu LAN Developer Mode only (no cloud API, no key extraction, no client spoofing, never the Bambu networking plugin); no outbound traffic while the online gate is off; secrets stay in the keychain; nothing scans networks the user did not select.
- **Needs hardware to verify:** the owner's Bambu P2S and Bambu A1 mini (see below). Broker research (`research/lan-and-brick/bambu-lan-mode-p2s-a1mini.md`, community sources, unverified against firmware): enable Settings > LAN Only, then Developer Mode, on the printer screen; MQTT 8883 as `bblp` with the access code, topics `device/<serial>/report` and `/request`; FTPS 990; certificates are self-signed (keep TOFU pinning); Bambu's January 2025 authorization firmware may block third-party control unless Developer Mode is on; the P2S manual documents Developer Mode; a `.gcode.3mf` can upload but fail to start depending on model and firmware, so the hardware test must cover start-print on both printers.

### Non-planar slicing: order of work and purpose

Decided 2026-11-24. Printers: **Bambu P2S** and **Bambu A1 mini**. Research and Stage 0 results are in `BlenderSlicer-local/research/non-planar/`.

**Purpose (the owner's main use case):** avoid delamination and easy break planes that planar printing creates. Layers that intersect along Z, instead of every joint lying in one flat plane, make a break along a single plane less likely.

**Order:**
1. **Z contouring (ZAA) first.** The engine already has it (`zaa_*` keys, Expert-only). Work: make it usable and trustworthy (corrected tooltips are done), decide whether to promote the keys to Advanced, plumb per-object use (done via per-object overrides), print tests on the P2S and A1 mini, and check what it does for layer-joint strength rather than only surface quality. Stage 0 found it helps slopes of about 2 to 20 degrees and does nothing above that.
2. **Then multi-millimetre relief** (the Stage 1 warp, slice planar, un-warp prototype in Python). Needs toolhead and nozzle clearance measurements for each printer, to set the slope and collision limits.

**Brick layers (to evaluate alongside; they target the same delamination problem and work with planar slicing):**
- Idea: alternate perimeter paths at Z offsets of half a layer, so bead joints in neighbouring walls interlock instead of lining up. Source: <https://www.cnckitchen.com/blog/brick-layers-make-3d-prints-stronger> (full notes: `BlenderSlicer-local/research/lan-and-brick/brick-layers-article.md`).
- Article details (broker read of the article, dated 2024-02-18; checked): done in Simplify3D with four processes (one per perimeter, one for infill): two use a 100% initial layer height and two 150%, giving the half-layer offset. Four samples per condition, PLA and PETG, 0.25 mm layers, 4 perimeters, no infill, DIY test machine: regular PLA 944 N and PETG 950 N; brick PLA 1072 N (+14%); brick PETG +10% (the article gives only the percentage; about 1045 N is a calculation). Tests at 0.2 and 0.3 mm were inconsistent and probably not significant (the author blames old, wet filament and thinks it works best with taller layers). Brick PETG samples showed secondary cracks. Fracture surfaces were zig-zag or conical instead of flat. A 2025 study (secondary) found +4 to 5% for PLA, and flow of 105 to 110% helped both patterns. In the 2025 Orca retest, 100% flow staggered samples could be weaker (voids); raising flow helped.
- Drawbacks reported: sloped top surfaces under-extrude, first and last layers need special handling, overhangs and adaptive layer height are problematic, extra moves; it mainly affects walls, not infill.
- Implementations: GeekDetour BrickLayers (G-code post-processor for PrusaSlicer, Orca and Bambu Studio); an OrcaSlicer "Stagger Perimeters" feature branch and fork, not in mainline (official issue 7282 open). Needs G-code with varied Z, not special firmware. Not in Orca v2.4.2 as far as known (unverified).
- Open questions: could the engine produce it (it already emits per-move Z for ZAA, so a half-layer perimeter offset may fit the same path machinery); interaction with ZAA; whether it is worth building before the printed comparison. Plan a small printed test (same specimen set as the stress-infill test) before building.

### Other open items

- H2D dual-nozzle send (per-nozzle mapping; single-nozzle only today).
- Check the real engine's travel-move `object_id` for bake.
- Project overrides (prime tower position) and AMS slot mapping in the UI.
- Cache browse/clear UI; move inspector and G-code window; Custom printer and bed editor.
- Remaining UI rough spots from the screenshot review: Preview legend truncation, panel title "Slicewright object settings".
- Stress-aware infill solver: research is in `BlenderSlicer-local/research/stress-infill/`; start with the printed test.
- Real-printer and real-window smoke tests (OctoPrint, Klipper, Bambu); deferred by the owner.
