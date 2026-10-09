# SPDX-License-Identifier: AGPL-3.0-only
"""Flatten one Orca system machine/process/filament preset triple into standalone JSON files.

Resolves `inherits` chains by name across the vendor directories under resources/profiles.
Usage: flatten_profile.py <orca-src> <outdir>
Generic printer: "MyMarlin 0.4 nozzle" (Custom), process "0.20mm Standard @MyMarlin", filament "Generic PLA @System".
"""
import json
import sys
from pathlib import Path

MACHINE = "MyMarlin 0.4 nozzle"
PROCESS = "0.20mm Standard @MyMarlin"
FILAMENT = "Generic PLA @System"


def index_profiles(root: Path):
    idx = {}
    for sub in ("Custom", "OrcaFilamentLibrary"):
        for p in (root / sub).rglob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(d, dict) and "name" in d and "type" in d:
                idx[(d["type"], d["name"])] = d
    return idx


def resolve(idx, typ, name):
    d = idx[(typ, name)]
    parent = d.get("inherits", "")
    base = resolve(idx, typ, parent) if parent else {}
    out = dict(base)
    out.update(d)
    out["inherits"] = ""
    return out


def main(orca, outdir):
    idx = index_profiles(Path(orca) / "resources" / "profiles")
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    for typ, name, fname in (("machine", MACHINE, "machine.json"), ("process", PROCESS, "process.json"), ("filament", FILAMENT, "filament.json")):
        d = resolve(idx, typ, name)
        for k in ("setting_id", "renamed_from", "instantiation", "compatible_printers_condition"):
            d.pop(k, None)
        d["from"] = "User"
        d["name"] = "spike_" + typ
        if typ == "machine":
            d["printer_settings_id"] = "spike_machine"
        if typ == "process":
            d["print_settings_id"] = "spike_process"
            d["compatible_printers"] = []
        if typ == "filament":
            d["filament_settings_id"] = ["spike_filament"]
            d["compatible_printers"] = []
        (out / fname).write_text(json.dumps(d, indent=1), encoding="utf-8")
        print(fname, len(d), "keys")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
