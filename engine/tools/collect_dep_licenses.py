#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Collect dependency licences and write NOTICE / SOURCE.txt for a deps artifact.

    collect_dep_licenses.py --downloads DIR --prefix DIR --out DIR --commit SHA
                            [--source-tarball FILE] [--deps A;B;...] [--repo URL]

Orca's deps superbuild downloads every source archive to ``DIR/<Recipe>/`` (see
``SLICEWRIGHT_DEPS_DOWNLOAD_DIR``).  For each built dependency the licence files are
read straight from those archives into ``OUT/licenses/<Recipe>/``.  The run FAILS when
a dependency has no archive, or no licence file and no entry in ``SUPPLIED``.  It also
writes ``OUT/NOTICE``, ``OUT/SOURCE.txt`` and ``OUT/MANIFEST.txt`` (compliance.md
sections 4 and 5: every binary publication carries licences, NOTICE and a source pointer).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import patchhdr  # noqa: E402

ENGINE = Path(__file__).resolve().parents[1]
DEFAULT_DEPS = "Boost;TBB;Cereal;NLopt;Eigen;CGAL;PNG;ZLIB;EXPAT;JPEG;libnoise"
IMPLIED = ("GMP", "MPFR")  # pulled in by CGAL

# A licence file sits at the archive root or in a LICENSE(S)/COPYING directory one level down.
LICENCE_RE = re.compile(r"^(copying|copyright|licen[cs]e|notice|unlicense|third-party)", re.I)
LICENCE_DIRS = {"license", "licenses", "licence", "licences", "copying"}
# Recipes whose licence text lives in a differently named file.
EXTRA = {"ZLIB": re.compile(r"^readme$", re.I), "JPEG": re.compile(r"^readme\.ijg$", re.I)}
# Archives that ship no licence file: we supply the text (compliance.md section 4).
SUPPLIED = {"libnoise": ("LGPL-2.1.txt", "Orca's libnoise fork has no COPYING; LGPL-2.1+ text supplied by Slicewright")}


def is_licence(dep: str, parts: list[str]) -> bool:
    """parts = path components below the archive's top-level directory."""
    name = parts[-1]
    if len(parts) == 1:
        return bool(LICENCE_RE.match(name) or (dep in EXTRA and EXTRA[dep].match(name)))
    return len(parts) == 2 and parts[0].lower() in LICENCE_DIRS


def read_licences(archive: Path, dep: str) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                parts = info.filename.split("/")[1:]
                if not info.is_dir() and parts and is_licence(dep, parts):
                    found["/".join(parts)] = z.read(info)
    else:
        with tarfile.open(archive, "r|*") as t:  # streamed: Boost is a 140 MB archive
            for m in t:
                parts = m.name.split("/")[1:]
                if m.isfile() and parts and is_licence(dep, parts):
                    f = t.extractfile(m)
                    found["/".join(parts)] = f.read() if f else b""
    return found


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(downloads: Path, deps: list[str], out: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    manifest: list[str] = []
    for dep in deps:
        folder = next((d for d in downloads.iterdir() if d.name.lower() == dep.lower()), None) if downloads.is_dir() else None
        archives = sorted(p for p in folder.iterdir() if p.is_file()) if folder else []
        if not archives:
            errors.append(f"{dep}: no source archive in {downloads / dep}")
            continue
        names: list[str] = []
        for archive in archives:
            for rel, data in read_licences(archive, dep).items():
                dest = out / "licenses" / dep / rel.replace("/", "__")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                names.append(dest.name)
        note = ""
        if not names and dep in SUPPLIED:
            fname, note = SUPPLIED[dep]
            dest = out / "licenses" / dep / fname
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ENGINE / "licenses" / fname, dest)
            names, note = [fname], f"  [{note}]"
        if not names:
            errors.append(f"{dep}: no licence file found in {', '.join(a.name for a in archives)}")
            continue
        for archive in archives:
            manifest.append(f"{dep}\t{archive.name}\tsha256={sha256(archive)}")
        manifest.append(f"{dep}\tlicences: {', '.join(names)}{note}")
    return manifest, errors


def notice(pin: dict[str, str], patches: list[Path], deps: list[str], today: str) -> str:
    lines = [
        "Slicewright engine dependencies (static libraries and headers)",
        "",
        f"Built on {today} from the dependency recipes of OrcaSlicer {pin.get('tag')} (commit {pin.get('commit')}),",
        "trimmed to what a headless libslic3r needs, and modified by the patch series below.",
        "This is a modified version of the OrcaSlicer deps/ superbuild (AGPL-3.0-only); the build files",
        "added by Slicewright (engine/deps/, engine/tools/) are AGPL-3.0-only too. Each library keeps its",
        "own licence; the texts are in licenses/<name>/ (see MANIFEST.txt). Libraries: " + ", ".join(deps) + ".",
        "GMP and MPFR (LGPL-3.0+) are built from source and linked statically; the matching source",
        "archives are in the source tarball named in SOURCE.txt.",
        "",
        "Patches applied to the OrcaSlicer tree:",
    ]
    for p in patches:
        h = patchhdr.parse(p)
        if h.noop:
            continue
        date = patchhdr.parse_date(h.fields.get("Date", ""))
        lines.append(f"  {p.name} ({date.date() if date else '?'}): {h.fields.get('Purpose', '')}")
    return "\n".join(lines) + "\n"


def source_txt(pin: dict[str, str], commit: str, repo: str, tarball: Path | None) -> str:
    lines = [
        "Corresponding source for this archive (AGPL-3.0-only section 6)",
        "",
        f"Slicewright repository: {repo}",
        f"Commit:                 {commit}",
        f"OrcaSlicer submodule:   engine/third_party/OrcaSlicer at {pin.get('tag')} ({pin.get('commit')}), {pin.get('url')}",
        "",
        "Rebuild:  git clone --recurse-submodules --depth 1 --shallow-submodules, check out the commit, then",
        "  cmake -S engine/deps -B engine/build/deps -G Ninja && cmake --build engine/build/deps",
    ]
    if tarball:
        lines += [
            "",
            "Dependency source archives, as downloaded by the build (pass the extracted directory as",
            "-DSLICEWRIGHT_DEPS_DOWNLOAD_DIR to build offline):",
            f"  {tarball.name}  sha256={sha256(tarball)}",
            "  published together with this archive.",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--downloads", type=Path, required=True)
    ap.add_argument("--prefix", type=Path, required=True, help="deps install root (contains usr/local)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--commit", required=True, help="Slicewright commit the deps were built from")
    ap.add_argument("--repo", default="https://github.com/michaelnarine/slicewright")
    ap.add_argument("--deps", default=DEFAULT_DEPS)
    ap.add_argument("--source-tarball", type=Path)
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)

    deps = [d for d in args.deps.split(";") if d] + [d for d in IMPLIED if d not in args.deps.split(";")]
    errors: list[str] = []
    if not any((args.prefix / "usr" / "local").rglob("*.a")):
        errors.append(f"{args.prefix}/usr/local has no static libraries: nothing was built")
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    manifest, errs = collect(args.downloads, deps, args.out)
    errors += errs
    if errors:
        print("error: dependency licence collection failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    pin = patchhdr.read_pin(ENGINE / "ORCA_PIN")
    (args.out / "NOTICE").write_text(notice(pin, patchhdr.series(ENGINE / "patches" / "orca"), deps, args.date))
    (args.out / "SOURCE.txt").write_text(source_txt(pin, args.commit, args.repo, args.source_tarball))
    (args.out / "MANIFEST.txt").write_text("\n".join(manifest) + "\n")
    print("\n".join(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
