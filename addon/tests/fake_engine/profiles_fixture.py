# SPDX-License-Identifier: GPL-3.0-or-later
"""A small hand-made profile library for the fake engine's ``profiles_archive()`` (04 section 3).

Everything here was written for this repository from the *format* description (vendor header,
machine model, machine / process / filament preset, ``inherits``, ``include``, ``instantiation``,
compatibility lists and conditions). The vendor and printer names are invented, and **no profile
JSON from Orca or any other slicer is copied**. The data in this module is dedicated to the
public domain under CC0-1.0.

Layout inside the zip (``profiles/`` root, like the real archive):
``<Vendor>.json`` (header) and ``<Vendor>/{machine,process,filament}/<name>.json``.

What the fixture is built to exercise (see ``addon/tests/unit/test_profiles_*.py``):

* ``OrcaFilamentLibrary``: the shared library vendor. Base presets without ``instantiation``,
  generic filaments with empty ``compatible_printers``, one with a condition.
* ``Acme``: two models, nozzle variants, a process with a list, with conditions and with neither,
  a printer-specific filament that shadows the library alias ``Generic PLA``, a parent in the
  library, and per-filament process conditions.
* ``Bolt3D``: one model, an ``include`` of a G-code template, and a vendor-local parent that shadows
  a library parent of the same name.
"""
from __future__ import annotations

import json
import zipfile

LIBRARY = "OrcaFilamentLibrary"


def _header(name, version, models=(), machines=(), processes=(), filaments=()):
    def listing(kind, names):
        return [{"name": n, "sub_path": f"{kind}/{n}.json"} for n in names]
    return {"name": name, "version": version, "description": f"{name} (fixture)",
            "machine_model_list": listing("machine", models),
            "machine_list": listing("machine", machines),
            "process_list": listing("process", processes),
            "filament_list": listing("filament", filaments)}


def _model(name, nozzles, materials, family):
    return {"type": "machine_model", "name": name, "model_id": name.replace(" ", "-"),
            "nozzle_diameter": nozzles, "machine_tech": "FFF", "family": family,
            "default_materials": materials}


def _preset(kind, name, parent, **extra):
    data = {"type": kind, "name": name, "from": "system", "instantiation": "true", **extra}
    if parent:                       # presets with no parent simply omit the key, as real files do
        data["inherits"] = parent
    return data


def _printer(name, model, variant, parent, print_profile, **extra):
    return _preset("machine", name, parent, printer_model=model, printer_variant=variant,
                   default_print_profile=print_profile, nozzle_diameter=[variant], **extra)


def _base(kind, name, parent="", **extra):
    """A preset that exists only to be inherited from (no ``instantiation``)."""
    data = _preset(kind, name, parent, **extra)
    data["instantiation"] = "false"
    return data


def files() -> dict[str, dict]:
    """``{path inside the zip: JSON object}``."""
    f: dict[str, dict] = {}

    # ---- the shared filament library ----
    lib = LIBRARY
    f[f"profiles/{lib}.json"] = _header(
        lib, "1.0.0.0", filaments=["fdm_filament_common", "fdm_filament_pla", "fdm_filament_petg",
                                   "Generic PLA", "Generic PETG", "Generic ABS"])
    f[f"profiles/{lib}/filament/fdm_filament_common.json"] = _base(
        "filament", "fdm_filament_common", filament_type=["PLA"], filament_diameter=["1.75"],
        filament_density=["1.24"], filament_cost=["20"], filament_colour=["#DDDDDD"],
        nozzle_temperature=["200"])
    f[f"profiles/{lib}/filament/fdm_filament_pla.json"] = _base(
        "filament", "fdm_filament_pla", "fdm_filament_common", nozzle_temperature=["210"])
    f[f"profiles/{lib}/filament/fdm_filament_petg.json"] = _base(
        "filament", "fdm_filament_petg", "fdm_filament_common", filament_type=["PETG"],
        filament_density=["1.27"], nozzle_temperature=["240"])
    f[f"profiles/{lib}/filament/Generic PLA.json"] = _preset(
        "filament", "Generic PLA", "fdm_filament_pla")
    f[f"profiles/{lib}/filament/Generic PETG.json"] = _preset(
        "filament", "Generic PETG", "fdm_filament_petg")
    f[f"profiles/{lib}/filament/Generic ABS.json"] = _preset(
        "filament", "Generic ABS", "fdm_filament_common", filament_type=["ABS"],
        nozzle_temperature=["250"], compatible_printers=[],
        compatible_printers_condition="nozzle_diameter[0] != 0.2")

    # ---- Acme ----
    a = "Acme"
    f[f"profiles/{a}.json"] = _header(
        a, "2.0.1.0", models=["Acme Maker 1", "Acme Maker 2"],
        machines=["fdm_acme_common", "Acme Maker 1 0.4 nozzle", "Acme Maker 1 0.6 nozzle",
                  "Acme Maker 2 0.4 nozzle"],
        processes=["fdm_process_common", "0.20mm Standard @Acme", "0.30mm Draft @Acme",
                   "0.12mm Fine @Acme", "0.20mm Strong @Acme"],
        filaments=["Generic PLA @Acme", "Acme PLA Pro"])
    f[f"profiles/{a}/machine/Acme Maker 1.json"] = _model(
        "Acme Maker 1", "0.4;0.6", "Generic PLA;Generic PETG;Acme PLA Pro", "Acme")
    f[f"profiles/{a}/machine/Acme Maker 2.json"] = _model(
        "Acme Maker 2", "0.4", "Generic PLA;Generic PETG", "Acme")
    f[f"profiles/{a}/machine/fdm_acme_common.json"] = _base(
        "machine", "fdm_acme_common", gcode_flavor="marlin2", printable_height="250",
        printable_area=["0x0", "220x0", "220x220", "0x220"], nozzle_diameter=["0.4"])
    f[f"profiles/{a}/machine/Acme Maker 1 0.4 nozzle.json"] = _printer(
        "Acme Maker 1 0.4 nozzle", "Acme Maker 1", "0.4", "fdm_acme_common", "0.20mm Standard @Acme")
    f[f"profiles/{a}/machine/Acme Maker 1 0.6 nozzle.json"] = _printer(
        "Acme Maker 1 0.6 nozzle", "Acme Maker 1", "0.6", "fdm_acme_common", "0.30mm Draft @Acme")
    f[f"profiles/{a}/machine/Acme Maker 2 0.4 nozzle.json"] = _printer(
        "Acme Maker 2 0.4 nozzle", "Acme Maker 2", "0.4", "fdm_acme_common", "0.20mm Standard @Acme",
        gcode_flavor="klipper", printable_height="300",
        printable_area=["0x0", "300x0", "300x300", "0x300"])
    f[f"profiles/{a}/process/fdm_process_common.json"] = _base(
        "process", "fdm_process_common", layer_height="0.2", initial_layer_print_height="0.2",
        wall_loops="2", top_shell_layers="5", bottom_shell_layers="3",
        sparse_infill_density="15%", sparse_infill_pattern="grid", outer_wall_speed="60",
        inner_wall_speed="100", sparse_infill_speed="120")
    f[f"profiles/{a}/process/0.20mm Standard @Acme.json"] = _preset(
        "process", "0.20mm Standard @Acme", "fdm_process_common",
        compatible_printers=["Acme Maker 1 0.4 nozzle", "Acme Maker 2 0.4 nozzle"])
    f[f"profiles/{a}/process/0.30mm Draft @Acme.json"] = _preset(
        "process", "0.30mm Draft @Acme", "fdm_process_common", layer_height="0.3",
        initial_layer_print_height="0.3", sparse_infill_density="10%",
        compatible_printers=[], compatible_printers_condition="nozzle_diameter[0] == 0.6")
    f[f"profiles/{a}/process/0.12mm Fine @Acme.json"] = _preset(
        "process", "0.12mm Fine @Acme", "fdm_process_common", layer_height="0.12", wall_loops="3",
        compatible_printers_condition='nozzle_diameter[0] == 0.4 and printer_model =~ /Acme.*/')
    f[f"profiles/{a}/process/0.20mm Strong @Acme.json"] = _preset(
        "process", "0.20mm Strong @Acme", "fdm_process_common", wall_loops="4",
        sparse_infill_density="40%")
    # shadows the library's "Generic PLA" for the 0.4 mm Maker 1 only
    f[f"profiles/{a}/filament/Generic PLA @Acme.json"] = _preset(
        "filament", "Generic PLA @Acme", "Generic PLA", nozzle_temperature=["205"],
        compatible_printers=["Acme Maker 1 0.4 nozzle"])
    f[f"profiles/{a}/filament/Acme PLA Pro.json"] = _preset(
        "filament", "Acme PLA Pro", "fdm_filament_pla", filament_colour=["#C0392B"],
        filament_cost=["32"],
        compatible_printers=["Acme Maker 1 0.4 nozzle", "Acme Maker 1 0.6 nozzle"],
        compatible_prints_condition="layer_height < 0.3")

    # ---- Bolt3D ----
    b = "Bolt3D"
    f[f"profiles/{b}.json"] = _header(
        b, "1.0.0.0", models=["Bolt One"], machines=["gcode_bolt_macros", "Bolt One 0.4 nozzle"],
        processes=["0.20mm Bolt"], filaments=["fdm_filament_pla", "Bolt PLA", "Bolt PETG"])
    f[f"profiles/{b}/machine/Bolt One.json"] = _model("Bolt One", "0.4", "Bolt PLA;Bolt PETG", "Bolt")
    # an include target: no instantiation, and "gcode" in the name
    f[f"profiles/{b}/machine/gcode_bolt_macros.json"] = _base(
        "machine", "gcode_bolt_macros", gcode_flavor="klipper", thumbnails="32x32,300x300")
    f[f"profiles/{b}/machine/Bolt One 0.4 nozzle.json"] = _printer(
        "Bolt One 0.4 nozzle", "Bolt One", "0.4", "", "0.20mm Bolt",
        include=["gcode_bolt_macros"], gcode_flavor="marlin2", printable_height="180",
        printable_area=["0x0", "180x0", "180x180", "0x180"])
    f[f"profiles/{b}/process/0.20mm Bolt.json"] = _preset(
        "process", "0.20mm Bolt", "", layer_height="0.2", wall_loops="3", top_shell_layers="4")
    # same name as a library base, so Bolt filaments find this one first
    f[f"profiles/{b}/filament/fdm_filament_pla.json"] = _base(
        "filament", "fdm_filament_pla", "fdm_filament_common", nozzle_temperature=["195"])
    f[f"profiles/{b}/filament/Bolt PLA.json"] = _preset("filament", "Bolt PLA", "fdm_filament_pla")
    f[f"profiles/{b}/filament/Bolt PETG.json"] = _preset("filament", "Bolt PETG", "fdm_filament_petg")
    return f


def write_zip(path: str) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files().items()):
            z.writestr(name, json.dumps(data, indent=1))
