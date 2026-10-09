# Licensing, store policy and compliance

Status: draft, 2026-10-09. **Not legal advice.** This is the single home for licensing, store-policy, naming and printer-access rules; the design docs and the plan only point here. Release-time checks are in [the plan's release checklist](../implementation/plan.md#9-v1-release-checklist).

## 1. Summary

- **Public distribution is lawful, with conditions.** GPLv3 §13 and AGPLv3 §13 expressly allow a GPL-3.0-or-later add-on to be combined with an AGPL-3.0-only module. Every linked component is compatible once OpenSSL 1.1.1 is removed (§4).
- **The self-hosted extension repository is the real channel.** extensions.blender.org acceptance is estimated at roughly 15–25 %: its licence allow-list has no AGPL entry, and there is no AGPL precedent (§8).
- **Conditions we must meet:** corresponding source with clear directions next to every binary (§5); modification notices and complete licence texts (§4–§5); in-app legal notices (§6); a provenance rule that keeps translated Orca code out of `addon/` (§3); new names without "Blender" (§7); Bambu access limited to the documented LAN Developer Mode (§10).

## 2. Licences of the parts

| Part | Licence | Contains | Ships in |
|---|---|---|---|
| Add-on (`addon/`) | **GPL-3.0-or-later** (`addon/LICENSE`) | Python only; no images, fonts, `.blend` or other binary assets | extension zips |
| Engine (`engine/`) | **AGPL-3.0-only** (`engine/LICENSE`). OrcaSlicer is AGPL "version 3" with no "or later" | binding, build, stubs, ported glue, patch series, tools; statically linked libraries (§4) | PyPI wheels, bundled unmodified in the zips |
| Profile JSON | AGPL-3.0 (inherited from Orca; the files carry no licence text, and their content is largely factual, so AGPL is the safe default) | `resources/profiles/**/*.json` only, as `profiles.zip` | the engine wheel |
| Docs, repo tooling | GPL-3.0 (repo-root `LICENSE`) | `docs/`, top-level files | repo only |

The extension as distributed is a combined work under **GPL-3.0-or-later AND AGPL-3.0-only**. AGPL §13 (network interaction) applies only to someone who runs a **modified** engine for remote users; desktop use and static hosting of downloads don't trigger it. README and `NOTICE` say so. Installation-Information rules (User Products) and export control (public open source, stdlib TLS) are not concerns.

## 3. Provenance and contributions

- **Provenance rule.** Add-on code is implemented from the design spec and tests and is **never translated** from OrcaSlicer, BambuStudio, PrusaSlicer or libvgcode (AGPL-3.0+) C++. A translation is a derivative work, and labelling it GPL would be inaccurate. Allowed in `addon/`: behaviour described in our own words and pinned by tests, colour values, protocol facts (endpoints, ports, message fields) and file-format facts. Anything that *is* a translation (e.g. the CLI/GUI glue in 02 §5.9) goes in `engine/` under AGPL.
  - Applies in particular to preset resolution and compatibility (03 §3.5–§3.6), the settings rule table (03 §2.4), the preview renderer (03 §7) and the G-code parser in the fake engine (03 §9.2).
  - Reviewers check that no translated Orca code is under `addon/` (PR template checkbox and a release-checklist item).
- **Per-directory licences.** `engine/` is AGPL-3.0-only, `addon/` GPL-3.0-or-later; every file we write carries the matching SPDX header. The CI boundary check excludes the Orca submodule, profile JSON and patch files.
- **No moving code from `engine/` to `addon/`** unless the person moving it wrote all of it.
- **Developer Certificate of Origin.** Every commit carries `Signed-off-by:` (DCO 1.1); a CI check enforces it. Rules are in `CONTRIBUTING.md`.

## 4. Engine link set and notices

`THIRD_PARTY_LICENSES` and `licenses/` are generated in CI from the actual linker map (`-Wl,-map`, `-Wl,-Map`, `/MAP`) through a component table (archive glob → name, version, SPDX, licence file). The build fails on an unknown archive or an SPDX ID outside the allow-list. A second check greps the binary for OpenSSL, OCCT and OpenCV symbols.

| Component | Licence | Notes |
|---|---|---|
| OrcaSlicer libslic3r (+ Slic3r, PrusaSlicer, BambuStudio heritage) | AGPL-3.0-only | the base |
| `calib.cpp` (pressure-advance patterns) | GPL-3.0 | compiled into libslic3r (`CMakeLists.txt:102-103`) |
| SmallAreaInfillFlowCompensator | GPLv3-based | |
| ArcWelder | AGPL-3.0-or-later | |
| Arachne, TreeSupport3D, Interlocking, Lightning (CuraEngine-derived) | AGPL-3.0-or-later | |
| FilamentMixerModel, `clonable_ptr` | MIT | |
| `Int128`, `MutablePriorityQueue` | BSL-1.0 | |
| glu-libtess | SGI-B-2.0 | |
| earcut | ISC | |
| admesh | GPL-2.0-or-later | |
| agg | AGG-PL / BSD dual | if linked |
| CGAL 6.2.1 | GPL-3.0+ / LGPL-3.0+ | |
| GMP 6.2.1, MPFR 4.2.2 | LGPL-3.0+ | static, or vendored DLLs on Windows (see below) |
| libnest2d | LGPL-3.0 | |
| libnoise (Orca's fork) | LGPL-2.1+ | the archive has no COPYING; **we supply the LGPL-2.1 text** |
| NLopt 2.5 | MIT + LGPL-2.1+ parts | |
| qhull | Qhull licence | ship `COPYING.txt` plus a modification notice and origin notice for any patches |
| oneTBB 2021.5 | Apache-2.0 | ship its NOTICE |
| Boost 1.84, Clipper2 | BSL-1.0 | |
| Eigen, libigl | MPL-2.0 | |
| cereal, libjpeg-turbo, nanobind | BSD-3 (+IJG) | |
| zlib, libpng, expat, miniz, nlohmann, fast_float, nanosvg, qoi, semver, imgui (one stb header) | permissive | keep notices |
| `msvcp140.dll` (Windows, via delvewheel) | Microsoft redistributable terms | |

- **Not linked:** minilzo; mcut (removed, 02 §3.2); OpenSSL 1.1.1 (its advertising clause is GPL-incompatible; it was used only for MD5); OCCT, OpenCV, Assimp, Draco, OpenVDB, FreeType, CURL, SLVS, GLFW/OpenGL, wxWidgets, FFMPEG.
- **LGPL obligations.** Ship the LGPL-3.0, LGPL-2.1 and GPL-3.0 texts, a "uses X under the LGPL" notice per LGPL library, and show their copyright notices in the About panel (LGPL-3.0 §4(c)). Full corresponding source satisfies the relinking requirement.
- **Windows GMP/MPFR.** If Phase 0 chooses Orca's prebuilt DLLs (02 §3.4), record their download URL, version and hash, ship them under their LGPL texts, and include the matching source archives in the source tarball.
- **Profiles.** Ship JSON only. Exclude the strays `check_unused_setting_id.py` and `FlyingBear/error_hull_show`. Importing a user's own presets is fine.
- **Never shipped or downloaded:** Bambu's network plugin (non-free), vendor logos, cover images, bed STLs, textures, Orca's fonts, `handy_models` (3DBenchy is CC-BY-ND).

## 5. Corresponding source and modification notices (AGPL §5a, §6)

- **Source tarball per engine release.** Each `engine-vX.Y.Z` GitHub Release attaches `<engine>-X.Y.Z-src.tar.zst`: our `engine/` tree, the Orca tree at the pinned commit, and the source archive of every statically linked or vendored dependency. CI rebuilds from it once on a clean runner. Tarballs are **kept indefinitely** and **mirrored** (Software Heritage save request and a Zenodo deposit per release).
- **No PyPI sdist.** It would be incomplete (submodule, dep sources) and over PyPI's 100 MB limit; the tarball is the source.
- **Clear directions next to the object code (§6(d))**, all pointing at the release and tarball: the PyPI long description; `SOURCE.txt` in the wheel and `version()["source_url"]`; the add-on `NOTICE`; the store or repository listing; the About panel.
- **Every binary publication carries them from the first one**, including the M1-B deps release assets and the M7 TestPyPI wheels: licence files, `NOTICE`, a source pointer and a source tarball.
- **Modification notices (§5a).** The numbered patch series is the record. `NOTICE` reads "This program is a modified version of OrcaSlicer <tag/commit>…", lists each patch's one-line purpose and date (generated from patch headers) and names the ported glue files in `engine/src/glue/`.
- **Credits** (README, `NOTICE`, manifest `copyright`): OrcaSlicer, BambuStudio, PrusaSlicer, Slic3r, CuraEngine, CGAL, Boost, oneTBB and the rest of §4.

## 6. In-app legal notices (GPL/AGPL §5(d))

An interactive program must show "Appropriate Legal Notices". The About & licences panel (03 §10.4) shows: copyright notices (ours, the main upstreams, and the LGPL libraries); that there is **no warranty**; that the add-on is GPL-3.0-or-later and the engine AGPL-3.0-only; how to view the licences (in-app viewer of `sc.licenses()`); the source link; the AGPL §13 note from §2; and non-affiliation with OrcaSlicer, Bambu Lab, Prusa Research and the Blender Foundation.

## 7. Naming and trademarks

- Blender's trademark policy and store ToS 2.1 forbid "Blender" in a product or extension name, so the **product and the repository are renamed before release**. "<Product>, an FDM slicer for Blender" is acceptable as a tagline.
- Avoid "Orca"/"OrcaSlicer" and vendor marks (Bambu, Prusa, Creality, …) in names. Describe compatibility nominatively ("uses printer profiles from the OrcaSlicer project", "sends to Bambu Lab printers in LAN Developer Mode"). No Orca, vendor or Blender logos; no implied endorsement.
- **Engine name.** PyPI `slicer-core` is taken (Kitware's 3D Slicer, Apache-2.0) and so is `stratum`. The store hash-checks bundled wheels against PyPI by name and version, so the engine needs its own PyPI name, trademark-searched and registered before the first TestPyPI upload.

## 8. extensions.blender.org

- **ToS 1.1** requires add-ons to be "wholly compliant with the GNU General Public License, version 3 or later". The licence allow-list (extensions-website `fixtures/licenses.json`, `validators.py:299-318`) contains GPL-2.0+/3.0+, BSD-1/2/3, BSL-1.0, CC0, LGPL-2.1+/3.0+, MIT, MIT-0, MPL-2.0, Pixar and Zlib, and every add-on must include GPL-3.0-or-later. **There is no AGPL slug**, so the manifest cannot express AGPL there. Local `blender --command extension validate` does **not** check the allow-list, so passing it proves nothing about the store. A devtalk question on AGPL (t/43443) has no staff answer.
- **ToS 1.2**: bundled assets must be CC0; the add-on ships none. **ToS 1.3**: credit copyright holders in `copyright`. **ToS 5.1/5.2**: self-contained, no downloaded functional components (so no "requires OrcaSlicer installed" path, and the store can never be served by a subprocess-to-Orca design). Wheels must be unmodified PyPI wheels.
- **Manifest variants.**
  - *Self-hosted* (default): `license = ["SPDX:GPL-3.0-or-later", "SPDX:AGPL-3.0-only"]`. The local validator accepts it.
  - *Store*, **only with explicit moderator consent**: `license = ["SPDX:GPL-3.0-or-later"]` plus, in the description and listing: "The add-on's Python code is GPL-3.0-or-later. It bundles <engine>, AGPL-3.0-only (derived from OrcaSlicer); the extension as distributed is GPL-3.0-or-later AND AGPL-3.0-only. Source: <link>." Never declare GPL-only silently; under ToS 1.1 that under-declares.
  - **`copyright` entries each start with a year**, e.g. "2016-2026 OrcaSlicer contributors"; the store validator rejects entries without one. Our zip check enforces it.
- **Moderator query (v2)**, sent in Phase 0 from the maintainer's account (Matrix #extension-moderators or an extensions-website issue), drafted outside the repo: (1) is a bundled AGPL-3.0-only PyPI wheel acceptable; (2) since the allow-list has no AGPL slug, is the GPL-only manifest plus the disclosure sentence acceptable; (3) does profile JSON inside the wheel count as an asset under ToS 1.2.
- **If the answer is no**, nothing in the engineering changes: the same zips ship through the self-hosted repository. There is no store-compatible alternative engine (no maintained GPL-3.0-or-later slicer core of comparable quality exists), and a thin "installer" listing would violate ToS 5.2.

## 9. Hosting

Extension zips (up to 150 MB) are **GitHub Release assets**; they don't go into the `gh-pages` git history. GitHub Pages serves only `index.json` and the HTML listing, generated by `extension server-generate` with `archive_url`s pointing at the Release assets. Engine source tarballs, deps tarballs and debug symbols are Release assets too.

## 10. Bambu LAN access

- Bambu's documented route for third-party tools is LAN-only mode with **Developer Mode**, which leaves MQTT and FTP open but which Bambu does not support (Bambu blog). Bambu's legal threat of May 2026 targeted software that impersonated Bambu Studio to the cloud. Bambu's ToS page returned HTTP 403 during review and is unread.
- Our design therefore uses only the user's access code in LAN Developer Mode, labels the feature "unsupported by Bambu", stops on any authorization denial and points the user to Developer Mode, and follows the **do-not list in 03 §8.4** (no cloud API, no network plugin, no Bambu Connect keys or certificates, no client impersonation, no authorization bypass). The release checklist verifies the list.

## 11. Patents and when to ask a lawyer

- Stratasys v. Bambu Lab (verdict September 2026) included US 9,421,713 (purge towers, method claims). No suits have targeted slicer software. Acceptable for free open-source distribution; get a lawyer if the project goes commercial or a demand arrives.
- **Lawyer consultation points:** accuracy of a GPL-only store manifest with the disclosure sentence; exposure under Bambu's terms of service; patents if commercial; whether any ported preset logic could be a derivative work (mitigated by §3).
