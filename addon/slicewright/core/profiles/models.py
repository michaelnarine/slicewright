# SPDX-License-Identifier: GPL-3.0-or-later
"""Vendor -> model -> nozzle -> concrete printer preset, and the defaults a new printer brings (03 section 3.7).

A *model* entry (``machine_model``) lists its nozzle diameters (``0.4;0.6``) and ``default_materials``.
The concrete printer preset is the selectable machine preset of the same vendor whose
``printer_model`` is the model's name and whose ``printer_variant`` is the nozzle. A printer preset names
``default_print_profile``; the model names the default materials. No ``bpy`` here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .index import LIBRARY_VENDOR, Entry, ProfileIndex, as_list


@dataclass
class PrinterChoice:
    """Everything a printer pick sets."""
    printer: Entry
    process: Entry | None
    filaments: list[Entry] = field(default_factory=list)


def norm_nozzle(text: str) -> str:
    """``0.40`` and ``0.4`` name the same nozzle."""
    try:
        return f"{float(text):g}"
    except ValueError:
        return text.strip()


def vendors(index: ProfileIndex) -> list[str]:
    """Vendors with at least one printer model, sorted case-insensitively (the filament library has none)."""
    names = {e.vendor for e in index.of_kind("model") if e.vendor != LIBRARY_VENDOR}
    return sorted(names, key=str.lower)


def models(index: ProfileIndex, vendor: str) -> list[Entry]:
    return sorted((e for e in index.of_kind("model") if e.vendor == vendor),
                  key=lambda e: e.name.lower())


def nozzle_options(model: Entry) -> list[str]:
    """The nozzle diameters a model offers, in file order, without duplicates."""
    out: list[str] = []
    for part in as_list(model.info.get("nozzle_diameter", "")):
        norm = norm_nozzle(part)
        if norm not in out:
            out.append(norm)
    return out


def printer_for(index: ProfileIndex, model: Entry, nozzle: str) -> Entry | None:
    """The concrete printer preset for ``model`` with ``nozzle`` (``printer_model`` + ``printer_variant``)."""
    wanted = norm_nozzle(nozzle)
    for e in index.of_kind("machine", selectable_only=True):
        if (e.vendor == model.vendor and e.printer_model == model.name
                and norm_nozzle(e.printer_variant) == wanted):
            return e
    return None


def model_of(index: ProfileIndex, printer: Entry) -> Entry | None:
    """The model entry a printer preset belongs to."""
    for e in index.of_kind("model"):
        if e.vendor == printer.vendor and e.name == printer.printer_model:
            return e
    return None


def default_process(index: ProfileIndex, printer: Entry) -> Entry | None:
    """The printer's ``default_print_profile``, if it exists and can be selected."""
    name = printer.info.get("default_print_profile", "")
    found = index.lookup("process", name, printer.vendor) if name else None
    return found if found is not None and found.instantiation else None


def default_filaments(index: ProfileIndex, model: Entry | None, printer: Entry) -> list[Entry]:
    """The model's ``default_materials`` that exist as selectable presets, in the listed order."""
    out: list[Entry] = []
    for name in as_list(model.info.get("default_materials", "")) if model else []:
        found = index.lookup("filament", name, printer.vendor)
        if found is not None and found.instantiation and found not in out:
            out.append(found)
    return out


def choose(index: ProfileIndex, model: Entry, nozzle: str) -> PrinterChoice | None:
    """What picking ``model`` with ``nozzle`` selects, or ``None`` if no such printer preset exists."""
    printer = printer_for(index, model, nozzle)
    if printer is None:
        return None
    return PrinterChoice(printer, default_process(index, printer),
                         default_filaments(index, model, printer))


def search_ids(entries: list[Entry], text: str, limit: int = 200) -> list[str]:
    """Ids of ``entries`` whose name or vendor contains every word of ``text`` (case-insensitive)."""
    words = text.lower().split()
    out = []
    for e in entries:
        haystack = f"{e.vendor} {e.name}".lower()
        if all(w in haystack for w in words):
            out.append(e.id)
            if len(out) >= limit:
                break
    return out
