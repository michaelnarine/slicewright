# SPDX-License-Identifier: GPL-3.0-or-later
"""Printer, process and filament selection on ``Scene.slicewright`` (03 sections 2.1 and 3.7).

The vendor / model / nozzle fields are *views* of ``printer_id``: picking any of them selects a concrete
printer preset, and setting ``printer_id`` updates the three. A new printer takes the model's default
materials and the printer's default process. Property update callbacks call into here; ``_guard`` stops
those callbacks from recursing when this module sets the properties itself.
"""
from __future__ import annotations

from contextlib import contextmanager

from ..core import logs
from ..core.profiles import models
from ..core.profiles.index import Entry
from . import config_pg, library, user_presets

_busy = [0]


@contextmanager
def _guard():
    _busy[0] += 1
    try:
        yield
    finally:
        _busy[0] -= 1


def busy() -> bool:
    """True while this module is setting properties itself; update callbacks must then do nothing."""
    return _busy[0] > 0


# -- search and enum callbacks (read-only) ----------------------------------------------------------

def search_printers(text: str) -> list[str]:
    lib = library.get()
    return [] if lib is None else models.search_ids(lib.printers() + lib.user_entries("machine"), text)


def search_processes(pg, text: str) -> list[str]:
    lib = library.get()
    if lib is None:
        return []
    subject = lib.subject("machine", pg.printer_id)
    entries = (lib.compat.compatible_processes(subject) if subject is not None
               else lib.index.of_kind("process", selectable_only=True))
    return models.search_ids(entries + lib.user_entries("process"), text)


def search_filaments(pg, text: str) -> list[str]:
    lib = library.get()
    if lib is None:
        return []
    printer = lib.subject("machine", pg.printer_id)
    process = lib.subject("process", pg.process_id)
    entries = (lib.compat.compatible_filaments(printer, process) if printer is not None
               else lib.index.of_kind("filament", selectable_only=True))
    return models.search_ids(entries + lib.user_entries("filament"), text)


def search_vendors(text: str) -> list[str]:
    lib = library.get()
    words = text.lower().split()
    return [] if lib is None else [v for v in models.vendors(lib.index)
                                   if all(w in v.lower() for w in words)]


def search_models(pg, text: str) -> list[str]:
    lib = library.get()
    words = text.lower().split()
    if lib is None:
        return []
    return [e.name for e in models.models(lib.index, pg.pick_vendor)
            if all(w in e.name.lower() for w in words)]


_nozzle_items: list[tuple[str, str, str]] = []     # kept alive: Blender holds raw pointers to the strings


def nozzle_items(pg) -> list[tuple[str, str, str]]:
    lib = library.get()
    found = []
    if lib is not None:
        model = lib.index.get("model", f"sys:{pg.pick_vendor}/{pg.pick_model}")
        found = [(n, f"{n} mm", "") for n in models.nozzle_options(model)] if model else []
    _nozzle_items[:] = found or [("", "-", "")]
    return _nozzle_items


# -- selection ---------------------------------------------------------------------------------------

def _model(pg) -> Entry | None:
    lib = library.get()
    return lib.index.get("model", f"sys:{pg.pick_vendor}/{pg.pick_model}") if lib else None


def _preferred_nozzle(options: list[str]) -> str:
    return "0.4" if "0.4" in options else (options[0] if options else "")


def select_printer(pg, entry: Entry, *, defaults: bool = True) -> None:
    """Make ``entry`` the printer: sync the pickers and, with ``defaults``, set process and filaments."""
    lib = library.get()
    with _guard():
        pg.printer_id = entry.id
        pg.pick_vendor = entry.vendor
        model = lib.model_of(entry) if lib else None
        pg.pick_model = model.name if model else ""
        pg.pick_nozzle = models.norm_nozzle(entry.printer_variant) if model else ""
    if defaults and lib is not None:
        apply_defaults(pg, entry, model)
    else:
        load_edit_buffers(pg)


def load_edit_buffers(pg, *, printer: bool = True, process: bool = True) -> None:
    """Fill the printer and/or process edit buffers from the selected presets (every key set, 03 section 2.3).
    A preset that cannot be found falls back to the copy embedded in the .blend (03 section 2.5).
    Loading discards unsaved edits in the buffers it refills."""
    lib = library.get()
    if lib is None:
        return
    for wanted, kind, buffer, preset_id in ((printer, "machine", pg.printer_edits, pg.printer_id),
                                            (process, "process", pg.process_edits, pg.process_id)):
        if not wanted or not preset_id:
            continue
        resolved = lib.resolve(kind, preset_id)
        config = resolved.config if resolved is not None else user_presets.embedded_config(pg, kind, preset_id)
        if config is None:
            logs.get_logger("picker").warning("preset %s not found and not embedded", preset_id)
            continue
        for message in config_pg.load_flat(buffer, config):
            logs.get_logger("picker").warning("%s: %s", preset_id, message)


def apply_defaults(pg, printer: Entry, model: Entry | None) -> None:
    """The printer's default process and the model's default materials (those compatible with it)."""
    lib = library.get()
    subject = lib.subject("machine", printer.id)
    process = models.default_process(lib.index, printer)
    if subject is not None and (process is None or process not in lib.compat.compatible_processes(subject)):
        compatible = lib.compat.compatible_processes(subject)
        process = compatible[0] if compatible else None
    with _guard():
        pg.process_id = process.id if process else ""
        pg.filaments.clear()
        chosen = models.default_filaments(lib.index, model, printer)
        if subject is not None:
            allowed = lib.compat.compatible_filaments(subject, lib.subject("process", pg.process_id))
            chosen = [e for e in chosen if e in allowed] or allowed[:1]
        for entry in chosen:
            add_filament(pg, entry)
    load_edit_buffers(pg)


def add_filament(pg, entry: Entry | None = None) -> None:
    """Append a filament slot, with the preset's colour. ``entry=None`` takes the first compatible one."""
    lib = library.get()
    if entry is None and lib is not None:
        subject = lib.subject("machine", pg.printer_id)
        found = (lib.compat.compatible_filaments(subject, lib.subject("process", pg.process_id))
                 if subject is not None else lib.index.of_kind("filament", selectable_only=True))
        entry = found[0] if found else None
    slot = pg.filaments.add()
    with _guard():
        slot.preset_id = entry.id if entry else ""
    if entry is not None:
        set_slot_colour(slot, entry)


def set_slot_colour(slot, entry) -> None:
    """Take ``filament_colour`` of the preset (``#RRGGBB``) as the slot colour; keep it if unparsable."""
    lib = library.get()
    resolved = lib.resolve("filament", entry.id)
    if resolved is None:
        return
    value = resolved.config.get("filament_colour", "")
    text = (value[0] if isinstance(value, list) and value else str(value)).strip().lstrip("#")
    if len(text) >= 6:
        try:
            slot.color = tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
        except ValueError:
            pass


def on_printer_id(pg) -> None:
    lib = library.get()
    if lib is not None and pg.printer_id.startswith("user:"):
        if lib.store.exists("machine", user_presets.name_of(pg.printer_id)):
            load_edit_buffers(pg, process=False)         # a user printer keeps the process and filaments
        return
    entry = lib.index.get_or_renamed("machine", pg.printer_id) if lib else None
    if entry is not None:
        select_printer(pg, entry)


def on_vendor(pg) -> None:
    lib = library.get()
    if lib is None:
        return
    found = models.models(lib.index, pg.pick_vendor)
    if found:
        with _guard():
            pg.pick_model = found[0].name
        _after_model(pg)


def on_model(pg) -> None:
    if _model(pg) is not None:
        _after_model(pg)


def _after_model(pg) -> None:
    model = _model(pg)
    options = models.nozzle_options(model)
    with _guard():
        pg.pick_nozzle = _preferred_nozzle(options)
    on_nozzle(pg)


def on_nozzle(pg) -> None:
    lib = library.get()
    model = _model(pg)
    if lib is None or model is None:
        return
    choice = models.choose(lib.index, model, pg.pick_nozzle)
    if choice is not None:
        select_printer(pg, choice.printer)


def on_process_id(pg) -> None:
    """Map a renamed process id to its current name. Filaments the new process rejects are kept: the
    user may know better, and the slot list simply stops offering them."""
    lib = library.get()
    entry = None if lib is None or pg.process_id.startswith("user:") else lib.index.get_or_renamed(
        "process", pg.process_id)
    if entry is not None and entry.id != pg.process_id:
        with _guard():
            pg.process_id = entry.id
    load_edit_buffers(pg, printer=False)


def on_slot_preset(slot) -> None:
    lib = library.get()
    if lib is not None and slot.preset_id.startswith("user:"):
        if lib.store.exists("filament", user_presets.name_of(slot.preset_id)):
            set_slot_colour(slot, user_presets.UserEntry("filament", user_presets.name_of(slot.preset_id)))
        return
    entry = lib.index.get_or_renamed("filament", slot.preset_id) if lib else None
    if entry is not None:
        with _guard():
            slot.preset_id = entry.id
        set_slot_colour(slot, entry)
