# slicewright_engine

The native slicing engine (AGPL-3.0-only), derived from OrcaSlicer. Not built yet.

- Interface: [docs/design/04-engine-api.md](../docs/design/04-engine-api.md) (authoritative).
- Design: [docs/design/02-native-engine.md](../docs/design/02-native-engine.md).
- Layout: [docs/design/01-architecture-overview.md](../docs/design/01-architecture-overview.md) section 7.

## Orca source and patches

- `third_party/OrcaSlicer` is a shallow git submodule pinned to the release in `ORCA_PIN` (v2.4.2). Clone with
  `git submodule update --init --depth 1`. It is never edited in place.
- `patches/orca/NNNN-short-name.patch` is the numbered series applied on top (02 section 2). Every patch carries a header
  (purpose, date, author, SPDX, upstream status); see `tools/patchhdr.py`.
- `python tools/apply_patches.py` exports the pinned commit into `build/orca-src` and applies the series there
  (`--check` does a throwaway dry run). `python tools/lint_patch_headers.py --check-pin` is what CI runs.

Every file written under this directory carries `SPDX-License-Identifier: AGPL-3.0-only`.
The Orca submodule (`third_party/`), profile JSON and `patches/` keep their upstream licensing
and are excluded from the header check.
