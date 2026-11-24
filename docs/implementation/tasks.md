<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Future tasks

Ideas and follow-ups that are not part of the milestone plan in `plan.md`. Add new items at the top of "Open". Development is on `main` only; nothing here is scheduled.

## Open

### LAN printer library and discovery

Added 2026-11-24 at the owner's request.

Keep a library of registered printers and let the user find printers on the current Wi-Fi/LAN, then send a G-code or `.gcode.3mf` print job to any of them over the LAN.

What exists today (M9): manual printer records in the add-on preferences (host, kind, secret in the macOS Keychain, TLS pin), Send panel with OctoPrint, Moonraker and Bambu LAN (FTPS and MQTT), connection test, start-print confirmation.

What this task adds:
- **Discovery:** an explicit "Find printers on this network" action (never automatic, never a background scan), gated by the same online-access permission as sending. Candidates to evaluate, each to be verified before building:
  - Bambu LAN: the SSDP-style UDP broadcast announcements that printers in LAN mode send (serial, model, IP); the community protocol notes (OpenBambuAPI) describe this. Needs a real printer to verify.
  - Moonraker and OctoPrint: mDNS/Bonjour service advertisements, or a user-supplied subnet probe limited to known ports.
  - Discovery only proposes a record; the user confirms it, enters the access code/API key, and trusts the certificate (existing TOFU pin flow). No secret is ever auto-filled or sent during discovery.
- **Library:** named printers with a model/preset pairing, last-seen address (IPs change; re-find by serial or hostname), status (online, idle, printing), and a per-printer default format and folder. Optional "send to several printers" with an explicit per-printer confirmation for start-print.
- **Status view:** online/offline and basic state in the Send panel and the Printers box, polled only on user action or while the panel is open, within the tick budget.
- **Constraints to keep:** Bambu LAN Developer Mode only (no cloud API, no key extraction, no client spoofing, never the Bambu networking plugin); no outbound traffic while the online gate is off; secrets stay in the keychain; nothing scans networks the user did not select.
- **Needs hardware to verify:** the owner's Bambu P2S and Bambu A1 mini (see below).

### Non-planar slicing: order of work and purpose

Decided 2026-11-24. Printers: **Bambu P2S** and **Bambu A1 mini**. Research and Stage 0 results are in `BlenderSlicer-local/research/non-planar/`.

**Purpose (the owner's main use case):** avoid delamination and easy break planes that planar printing creates. Layers that intersect along Z, instead of every joint lying in one flat plane, make a break along a single plane less likely.

**Order:**
1. **Z contouring (ZAA) first.** The engine already has it (`zaa_*` keys, Expert-only). Work: make it usable and trustworthy (corrected tooltips are done), decide whether to promote the keys to Advanced, plumb per-object use (done via per-object overrides), print tests on the P2S and A1 mini, and check what it does for layer-joint strength rather than only surface quality. Stage 0 found it helps slopes of about 2 to 20 degrees and does nothing above that.
2. **Then multi-millimetre relief** (the Stage 1 warp, slice planar, un-warp prototype in Python). Needs toolhead and nozzle clearance measurements for each printer, to set the slope and collision limits.

**Brick layers (to evaluate alongside; they target the same delamination problem and work with planar slicing):**
- Idea: alternate perimeter paths at Z offsets of half a layer, so bead joints in neighbouring walls interlock instead of lining up. Source: <https://www.cnckitchen.com/blog/brick-layers-make-3d-prints-stronger>.
- Reported results (broker summary, secondary; verify against the article): CNC Kitchen, 4 samples per condition, 0.25 mm layers, 4 perimeters, no infill: PLA 944 N to 1072 N (+14%), PETG 950 N to about 1045 N (+10%); follow-up samples at 0.2 and 0.3 mm were inconsistent. A 2025 study found +4 to 5% for PLA, and flow of 105 to 110% helped both patterns. In the 2025 Orca retest, 100% flow staggered samples could be weaker (voids); raising flow helped.
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
