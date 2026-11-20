# slicewright_engine

The native slicing engine (AGPL-3.0-only), derived from OrcaSlicer, built as an in-process nanobind abi3 module.

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

## Dependencies

`deps/CMakeLists.txt` drives Orca's own deps superbuild (patch 0007) so that it builds only what libslic3r needs:

    cmake -S engine/deps -B engine/build/deps -G Ninja
    cmake --build engine/build/deps          # real build: cold 25-45 min per platform (02 section 3.4)

`cmake --build engine/build/deps --target orca_deps-configure` followed by `python engine/deps/check_dep_list.py engine/build/deps`
configures only and checks the planned set without downloading anything. Options: `SLICEWRIGHT_DEPS_LIST`,
`SLICEWRIGHT_GMP_SOURCE` (Windows GMP/MPFR: from source or Orca's prebuilt DLLs; pending spike (c)),
`SLICEWRIGHT_DEPS_PREFIX`, `SLICEWRIGHT_DEPS_DOWNLOAD_DIR`, `SLICEWRIGHT_OSX_DEPLOYMENT_TARGET` (default 11.2).

## Building locally (macOS arm64)

`tools/build_local_macos.sh` wraps the commands of the `deps` and `link` jobs in `.github/workflows/engine-ci.yml`
(everything lands in the gitignored `engine/build`; every step is incremental, so it is safe to re-run):

    brew install ninja autoconf automake libtool texinfo ccache      # once; uv or python3.13 for the venv
    engine/tools/build_local_macos.sh

It creates `engine/build/venv` (Python 3.13, pytest 9.1.1, numpy 2.5.3, nanobind 3.1.0, CMake 4.4.5 as pinned in CI), checks out
the Orca submodule shallow, applies the patch series, builds the deps into `engine/build/deps/prefix` and then the
engine, the Orca test subset and the module into `engine/build/engine`. The module is
`engine/build/engine/python/slicewright_engine/_native.abi3.so` (abi3: any CPython 3.12+, so the venv's 3.13 and
Blender 5.1's 3.13 both load it). Measured on a 10-core M-series Mac, Xcode 26.6: deps about 3.5 min, engine about 4 min cold.

Run the suites the way CI does (from the repo root, with `engine/build/venv/bin` first on `PATH`):

    E=$PWD/engine/build/engine
    ctest --test-dir $E --output-on-failure
    PYTHONPATH=$E/python pytest engine/tests/binding -q --ignore=engine/tests/binding/test_acceptance_m5.py
    PYTHONPATH=$PWD/engine/tests:$E/python pytest -p contract_expected_skips --backend=real addon/tests/contract -q
    PYTHONPATH=$E/python python engine/tools/export_fixtures.py --check
    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
        --python engine/tests/blender/run_cube.py -- $E/python 10          # also 1 thread

To use the module from the add-on in Blender, set `SLICEWRIGHT_ENGINE_PATH=$E/python` (see `docs/design/03-blender-addon.md`).
