#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Export the real engine's data as JSON fixtures for the add-on's fake engine (04 section 11, plan M2 layer 14).

    PYTHONPATH=<build>/python export_fixtures.py [--out engine/tests/fixtures/exported] [--check]

Writes, with sorted keys and a stable layout, so that a diff shows exactly what an Orca bump changed:

    config_schema.json   sc.config_schema()
    tab_layout.json      sc.tab_layout()
    enums.json           sc.enums()
    version.json         sc.version() minus the build date and compiler
    config_samples.json  inputs and outputs of compose_config / normalize_config / eval_condition on fixed samples

``--check`` writes nothing and exits 1 when the files on disk differ from what the module produces (CI uses it,
so the fixtures cannot go stale). The files carry Orca-derived labels and tooltips (AGPL-3.0-only) and live under
engine/tests/fixtures/, which the SPDX check skips. The add-on's fake loads them instead of its hand-written
schema_data.py; that switch is an add-on change (see the M2 layer 14 PR description).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "exported"

PRINTER = {"name": "Sample Printer", "printable_area": ["0x0", "256x0", "256x256", "0x256"], "printable_height": "250",
           "nozzle_diameter": ["0.4"], "gcode_flavor": "marlin2"}
PROCESS = {"name": "0.20mm Sample", "layer_height": "0.2", "initial_layer_print_height": "0.2", "wall_loops": "2",
           "sparse_infill_density": "15%", "inherits": "ignored"}
FILAMENTS = [
    {"name": "Sample PLA", "filament_type": ["PLA"], "filament_colour": ["#FFFFFF"], "nozzle_temperature": ["220"]},
    {"name": "Sample PETG", "filament_type": ["PETG"], "filament_colour": ["#112233"], "nozzle_temperature": ["240"]},
]
CONDITION_CONFIG = {"printer_model": "A1", "nozzle_diameter": ["0.4", "0.6"], "printer_notes": "PRINTER_VENDOR_TEST and more",
                    "num_extruders": "2"}
CONDITIONS = ['printer_model == "A1"', "nozzle_diameter[1] > 0.5", "num_extruders < 2", "printer_notes =~ /.*VENDOR_TEST.*/",
              '(printer_model == "X1" or printer_model == "A1") and num_extruders == 2']


def build(sc) -> dict:
    version = sc.version()
    version = {k: v for k, v in version.items() if k != "build"} | {"build": {"platform": version["build"]["platform"]}}
    composed_one = sc.compose_config(PRINTER, PROCESS, FILAMENTS[:1])
    composed_two = sc.compose_config(PRINTER, PROCESS, FILAMENTS)
    samples = {
        "inputs": {"printer": PRINTER, "process": PROCESS, "filaments": FILAMENTS, "condition_config": CONDITION_CONFIG},
        "compose_one_filament": composed_one,
        "compose_two_filaments": composed_two,
        "normalize_one_filament": sc.normalize_config(composed_one),
        "normalize_invalid": sc.normalize_config({**composed_one, "sparse_infill_density": "150%", "no_such_key": "1"}),
        "conditions": {c: sc.eval_condition(c, CONDITION_CONFIG) for c in CONDITIONS},
    }
    return {
        "config_schema.json": sc.config_schema(),
        "tab_layout.json": sc.tab_layout(),
        "enums.json": sc.enums(),
        "version.json": version,
        "config_samples.json": samples,
    }


def render(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    import slicewright_engine as sc

    files = {name: render(obj) for name, obj in build(sc).items()}
    stale = []
    for name, text in files.items():
        path = args.out / name
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path} ({len(text) / 1024:.0f} KiB)")
    if stale:
        print(f"::error::exported fixtures are out of date: {', '.join(stale)}; run engine/tools/export_fixtures.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
