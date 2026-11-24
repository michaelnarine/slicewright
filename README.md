# Slicewright: an FDM slicer for Blender

An FDM 3D-printing slicer that runs inside Blender. Model in Blender, choose a printer, filament and process profile (vendor presets or your own), slice with the OrcaSlicer engine, and preview the toolpaths in the viewport the way a dedicated slicer does. Then export the G-code or send it to your printer.

**Status:** a working personal project, macOS (Apple silicon) only for now. The add-on slices with the OrcaSlicer v2.4.2 engine in-process, previews the toolpaths, exports G-code and `.gcode.3mf`, sends to OctoPrint, Moonraker and Bambu printers over the LAN (tested against fake servers only; no real printer yet), and can bake a slice into Curves or a mesh. Development happens directly on `main` with tests run locally; there is no CI, no pull-request flow and no public release planned. To try it, run `addon/tools/run_blender.sh` (needs the engine built with `engine/tools/build_local_macos.sh`; see [engine/README.md](engine/README.md)). What is open: [docs/implementation/tasks.md](docs/implementation/tasks.md).

- [Architecture overview](docs/design/01-architecture-overview.md): start here.
- [Implementation plan](docs/implementation/plan.md): the original milestone plan, with a status note at the top.
- [Future tasks](docs/implementation/tasks.md): open ideas and follow-ups.
- [Licensing and compliance](docs/publishing/compliance.md).
- [Contributing](CONTRIBUTING.md): DCO sign-off, per-directory licences, provenance rule.

The project was renamed from `BlenderSlicer` because Blender's trademark policy doesn't allow "Blender" in a product name.

## Licensing

This repository has two licences, split by directory:

| Part | Licence | Licence file |
|---|---|---|
| Blender add-on (`addon/`) | GPL-3.0-or-later | [`addon/LICENSE`](addon/LICENSE) |
| Slicing engine `slicewright_engine` (`engine/`), derived from [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer) | AGPL-3.0-only | [`engine/LICENSE`](engine/LICENSE) |
| Docs and everything else | GPL-3.0 | [`LICENSE`](LICENSE) |

The extension as distributed combines both and is GPL-3.0-or-later AND AGPL-3.0-only. Each engine release has a corresponding-source tarball on its GitHub Release. The AGPL's network clause applies only if you run a modified engine for remote users, not to desktop use. Details: [compliance.md](docs/publishing/compliance.md).

This project is not affiliated with or endorsed by OrcaSlicer, Bambu Lab, Prusa Research or the Blender Foundation.
