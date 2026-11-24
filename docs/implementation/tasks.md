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

### Non-planar slicing, Stage 1 inputs

Owner's printers (2026-11-24): **Bambu P2S** and **Bambu A1 mini**. Research and Stage 0 results are in `BlenderSlicer-local/research/non-planar/`. Still needed before Stage 1: toolhead/nozzle clearance measurements for each printer, and whether multi-millimetre relief (warp prototype) is wanted or Z contouring (ZAA) is enough.

### Other open items

- H2D dual-nozzle send (per-nozzle mapping; single-nozzle only today).
- Check the real engine's travel-move `object_id` for bake.
- Project overrides (prime tower position) and AMS slot mapping in the UI.
- Cache browse/clear UI; move inspector and G-code window; Custom printer and bed editor.
- Remaining UI rough spots from the screenshot review: Preview legend truncation, panel title "Slicewright object settings".
- Stress-aware infill solver: research is in `BlenderSlicer-local/research/stress-infill/`; start with the printed test.
- Real-printer and real-window smoke tests (OctoPrint, Klipper, Bambu); deferred by the owner.
