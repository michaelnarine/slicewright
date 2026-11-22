# SPDX-License-Identifier: GPL-3.0-or-later
"""Names of presets as people read them (03 section 3.7). No ``bpy``.

A preset's id is ``sys:<vendor>/<name>`` for a system preset and ``user:<name>`` for one the user saved. The ids are what
the scene stores and what everything compares; the panel shows :func:`display_name` instead, which drops the
internal prefix and keeps the rest of the name whole, including the part that tells presets apart (``@BBL A1``,
``0.20mm Standard``). The full id goes in the tooltip (:func:`tooltip`).
"""
from __future__ import annotations

SYS, USER = "sys:", "user:"
USER_SUFFIX = " (user)"


def display_name(preset_id: str) -> str:
    """``sys:BBL/Bambu PLA Basic @BBL A1`` -> ``Bambu PLA Basic @BBL A1``; ``user:My PLA`` -> ``My PLA (user)``;
    anything else is returned as it is. The name is never shortened."""
    if preset_id.startswith(SYS):
        vendor, sep, name = preset_id[len(SYS):].partition("/")
        return name if sep and name else preset_id[len(SYS):]
    if preset_id.startswith(USER):
        return preset_id[len(USER):] + USER_SUFFIX
    return preset_id


def vendor_of(preset_id: str) -> str:
    """The vendor of a system preset id, ``""`` for anything else."""
    if preset_id.startswith(SYS):
        vendor, sep, _name = preset_id[len(SYS):].partition("/")
        return vendor if sep else ""
    return ""


def tooltip(kind_label: str, preset_id: str) -> str:
    """Tooltip of a preset button: what it is, the whole name, the vendor and the id the file stores."""
    if not preset_id:
        return f"No {kind_label.lower()} chosen. Click to search and choose one"
    vendor = vendor_of(preset_id)
    lines = [f"{kind_label}: {display_name(preset_id)}"]
    if vendor:
        lines.append(f"Vendor: {vendor}")
    lines.append(f"Preset id: {preset_id}")
    lines.append("Click to search and choose another")
    return "\n".join(lines)
