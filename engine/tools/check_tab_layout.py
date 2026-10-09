# SPDX-License-Identifier: AGPL-3.0-only
"""Schema-coverage check for the generated tab layout (04 section 6.5).

Usage:
    check_tab_layout.py --layout tab_layout.json --orca-tree <orca checkout>
    check_tab_layout.py --layout tab_layout.json --print-config PrintConfig.cpp [--preset-cpp Preset.cpp]

The key universe is every `def = this->add("key", ...)` in PrintConfig.cpp (parsed statically,
including the computed machine_max_* and filament_* override families; see schema_keys).
Violations (exit 1): malformed shape, a duplicate key within one tab, `custom` not str/null, `wiki`
keys not a subset of `keys`, or a layout key outside the universe.  Non-fatal: schema keys placed
on no page, grouped by preset type (taken from the s_Preset_*_options lists of Preset.cpp), and
counted in the summary line.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TABS = ("process", "filament", "printer")
PRESET_LISTS = {"process": ("print_options",), "filament": ("filament_options",),
                "printer": ("printer_options", "machine_limits_options")}


def schema_keys(print_config_text):
    """Literal add("key") calls plus the two computed families PrintConfig.cpp declares in loops:
    add("machine_max_<what>_" + axis.name) over the AxisDefault {"x", {...}} rows, and the
    filament_extruder_override_keys list passed to add_nullable(opt_key)."""
    keys = set(re.findall(r'\bdef\s*=\s*this->add(?:_nullable)?\(\s*"(\w+)"\s*,', print_config_text))
    axes = re.findall(r'\{\s*"(\w)"\s*,\s*\{', print_config_text)
    for prefix in re.findall(r'\bdef\s*=\s*this->add\(\s*"(\w+)"\s*\+\s*axis\.name', print_config_text):
        keys |= {prefix + a for a in axes}
    m = re.search(r"filament_extruder_override_keys\s*=\s*\{(.*?)\};", print_config_text, re.S)
    if m:
        keys |= set(re.findall(r'"(\w+)"', re.sub(r"//[^\n]*", "", m.group(1))))
    return keys


def preset_keys(preset_text):
    """{tab: set(keys)} from the static std::vector<std::string> s_Preset_*_options lists."""
    res = {}
    for tab, names in PRESET_LISTS.items():
        keys = set()
        for n in names:
            m = re.search(rf"s_Preset_{n}\s*\{{(.*?)\}};", preset_text, re.S)
            body = re.sub(r"//[^\n]*|/\*.*?\*/", "", m.group(1), flags=re.S) if m else ""
            keys |= set(re.findall(r'"(\w+)"', body))
        res[tab] = keys
    return res


def validate_shape(layout):
    errs = []
    if not isinstance(layout, dict) or set(layout) != set(TABS):
        return [f"top level must be an object with exactly the tabs {TABS}"]
    for tab in TABS:
        seen = set()
        if not isinstance(layout[tab], list):
            errs.append(f"{tab}: must be a list of pages")
            continue
        for p in layout[tab]:
            if not (isinstance(p, dict) and set(p) == {"page", "icon", "groups"} and isinstance(p["page"], str)
                    and isinstance(p["icon"], str) and isinstance(p["groups"], list)):
                errs.append(f"{tab}: bad page object {str(p)[:60]}")
                continue
            for g in p["groups"]:
                where = f"{tab}/{p['page']}/{g.get('title') if isinstance(g, dict) else g}"
                if not (isinstance(g, dict) and set(g) == {"title", "keys", "wiki", "custom"}
                        and isinstance(g["title"], str) and isinstance(g["keys"], list)
                        and all(isinstance(k, str) for k in g["keys"]) and isinstance(g["wiki"], dict)
                        and all(isinstance(v, str) for v in g["wiki"].values())):
                    errs.append(f"{where}: bad group object")
                    continue
                if g["custom"] is not None and not isinstance(g["custom"], str):
                    errs.append(f"{where}: custom must be str or null")
                for k in set(g["wiki"]) - set(g["keys"]):
                    errs.append(f"{where}: wiki key {k} not in keys")
                for k in g["keys"]:
                    if k in seen:
                        errs.append(f"{tab}: duplicate key {k} ({where})")
                    seen.add(k)
    return errs


def check(layout, universe, by_preset=None):
    """Return (violations, coverage {tab: sorted unplaced keys}, placed {tab: set})."""
    errs = validate_shape(layout)
    placed = {t: set() for t in TABS}
    if not errs:
        for tab in TABS:
            for p in layout[tab]:
                for g in p["groups"]:
                    placed[tab].update(g["keys"])
            errs += [f"{tab}: key {k} is not in the config schema" for k in sorted(placed[tab] - universe)]
    cov = {}
    for tab in TABS:
        pool = (by_preset or {}).get(tab, universe)
        cov[tab] = sorted((pool & universe) - placed[tab])
    return errs, cov, placed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--layout", required=True)
    ap.add_argument("--orca-tree")
    ap.add_argument("--print-config")
    ap.add_argument("--preset-cpp")
    a = ap.parse_args(argv)
    pc = a.print_config or (a.orca_tree and str(Path(a.orca_tree) / "src/libslic3r/PrintConfig.cpp"))
    pr = a.preset_cpp or (a.orca_tree and str(Path(a.orca_tree) / "src/libslic3r/Preset.cpp"))
    if not pc:
        ap.error("need --orca-tree or --print-config")
    universe = schema_keys(Path(pc).read_text("utf-8", "replace"))
    by_preset = preset_keys(Path(pr).read_text("utf-8", "replace")) if pr and Path(pr).exists() else None
    errs, cov, placed = check(json.loads(Path(a.layout).read_text("utf-8")), universe, by_preset)
    for e in errs:
        print(f"VIOLATION: {e}", file=sys.stderr)
    for tab in TABS:
        print(f"unplaced {tab}: {len(cov[tab])} keys: {', '.join(cov[tab])}")
    print(f"summary: schema_keys={len(universe)} placed={ {t: len(placed[t]) for t in TABS} } "
          f"unplaced={ {t: len(cov[t]) for t in TABS} } violations={len(errs)}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
