# SPDX-License-Identifier: GPL-3.0-or-later
"""Gathers a :class:`~slicewright.engine.jobs.SliceRequest` from the scene (03 sections 3.2, 4.1 and 6.1).

The printer and process come from the preset *edit buffers* (the selected preset plus unsaved edits,
03 section 2.3), narrowed to the keys of their role; each filament slot is its resolved preset with the slot's
overrides and colour applied (04 section 6.1: the caller applies them before ``compose_config``). The plate is
extracted with the full pre-slice check (03 section 4.3), whose issues travel with the request.
"""
from __future__ import annotations

from typing import Any

from ..core.profiles import edits
from ..engine.jobs import SliceRequest
from . import config_pg, extract, library, user_presets

DEFAULT_FILAMENT = "Default filament"


class InputError(Exception):
    """The scene cannot be turned into a slice request (a preset is missing, ...)."""


def preset_dict(schema: dict[str, dict], kind: str, name: str, flat: dict[str, Any]) -> dict:
    """A PresetDict for ``compose_config``: ``flat`` (FlatConfig text, or resolved ``str`` / ``list[str]`` values)
    narrowed to the keys of ``kind``, vectors in preset-file form (04 section 2.5)."""
    out: dict[str, Any] = {"name": name}
    for key, value in edits.role_flat(schema, kind, flat).items():
        out[key] = value if isinstance(value, list) else edits.file_value(schema, key, value)
    return out


def slot_colour(slot) -> str:
    """``#RRGGBB`` of the slot's display colour (it is a gamma colour, so the channels are used as they are)."""
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02X}" for c in slot.color[:3])


def filament_dicts(pg, schema: dict[str, dict]) -> list[dict]:
    """One PresetDict per filament slot, in slot order. No slots: one default filament."""
    lib = library.get()
    out = []
    for index, slot in enumerate(pg.filaments):
        config = None
        if slot.preset_id:
            resolved = lib.resolve("filament", slot.preset_id) if lib is not None else None
            config = resolved.config if resolved is not None else user_presets.embedded_config(
                pg, "filament", slot.preset_id)
            if config is None:
                raise InputError(f"Filament {index + 1}: preset {slot.preset_id!r} was not found")
        preset = preset_dict(schema, "filament", slot.preset_id or f"Filament {index + 1}", config or {})
        for key, value in config_pg.overridden(slot.overrides).items():
            preset[key] = edits.file_value(schema, key, value)
        preset["filament_colour"] = [slot_colour(slot)]
        out.append(preset)
    return out or [{"name": DEFAULT_FILAMENT}]


def gather_presets(pg) -> tuple[dict, dict, list[dict]]:
    """``(printer, process, filaments)`` PresetDicts of the scene group ``pg``: what a slice composes its config from.
    Raises :class:`InputError` for a missing preset."""
    schema = config_pg.schema()
    printer = preset_dict(schema, "machine", pg.printer_id or "Printer", config_pg.to_flat(pg.printer_edits))
    process = preset_dict(schema, "process", pg.process_id or "Process", config_pg.to_flat(pg.process_edits))
    return printer, process, filament_dicts(pg, schema)


def build_request(context, threads: int = 0) -> SliceRequest:
    """The request for the plate of ``context.scene``. Raises :class:`InputError` for missing presets."""
    scene = context.scene
    printer, process, filaments = gather_presets(scene.slicewright)
    extraction = extract.check_plate(context)
    return SliceRequest(printer, process, filaments, extraction.objects, project=None, threads=threads,
                        issues=extraction.issues, tag=scene.name)
