# BlenderSlicer (working title)

An FDM 3D-printing slicer that runs inside Blender. Model in Blender, choose a printer, filament and process profile (vendor presets or your own), slice with the OrcaSlicer engine, and preview the toolpaths in the viewport the way a dedicated slicer does. Then export the G-code or send it to your printer.

**Status:** design phase. There is no usable code yet.

- `docs/design/`: architecture and design docs (in progress)

The public name will probably change before release, to follow the extensions.blender.org naming rules.

## Licensing

Licensing is being finalised as part of the design. The add-on code is intended to be GPL-3.0-or-later. The slicing engine is derived from [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer), which is AGPL-3.0. This project is not affiliated with or endorsed by OrcaSlicer, Bambu Lab, Prusa Research, or the Blender Foundation.
