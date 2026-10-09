# SPDX-License-Identifier: GPL-3.0-or-later
"""User presets in Blender: where they live, saving the edit buffers, revert, embedding in the .blend
(03 sections 2.5 and 3.8). The file format, diffing and secret stripping are in ``core/profiles``.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any

import bpy

from ..core import logs
from ..core.profiles import edits
from ..core.profiles import user as user_core
from ..core.profiles.source import ProfileError
from . import config_pg, library

KIND_OF_ROLE = {"printer": "machine", "process": "process", "filament": "filament"}
BUFFER_OF_ROLE = {"printer": "printer_edits", "process": "process_edits"}
_dir_override: list[str | None] = [None]          # tests and source checkouts point this somewhere else


@dataclass
class UserEntry:
    """A user preset shaped like an index entry, so the picker can list it with the system ones."""
    kind: str
    name: str
    vendor: str = "user"

    @property
    def id(self) -> str:
        return f"user:{self.name}"


def presets_dir() -> str:
    """``extension_path_user(..., 'presets')``; a temp folder when not installed as an extension."""
    if _dir_override[0]:
        return _dir_override[0]
    try:
        return bpy.utils.extension_path_user(__package__.split(".")[0], path="presets", create=True)
    except Exception:  # noqa: BLE001 - source checkout or tests
        return os.path.join(tempfile.gettempdir(), "slicewright-presets")


def set_presets_dir(path: str | None) -> None:
    _dir_override[0] = path


def role_buffer(pg, role: str):
    """``(config PG, slot or None)`` that holds the unsaved edits of ``role``."""
    if role == "filament":
        slots = pg.filaments
        slot = slots[min(max(pg.settings_slot, 0), len(slots) - 1)] if len(slots) else None
        return (slot.overrides if slot else None), slot
    return getattr(pg, BUFFER_OF_ROLE[role]), None


def preset_id_of(pg, role: str) -> str:
    if role == "filament":
        _, slot = role_buffer(pg, role)
        return slot.preset_id if slot else ""
    return pg.printer_id if role == "printer" else pg.process_id


def set_preset_id(pg, role: str, preset_id: str) -> None:
    from . import picker
    with picker._guard():
        if role == "filament":
            role_buffer(pg, role)[1].preset_id = preset_id
        else:
            setattr(pg, f"{role}_id", preset_id)


def parent_info(lib, kind: str, preset_id: str) -> tuple[dict[str, Any], str | None] | None:
    """``(parent's resolved config, name to write into "inherits")`` for the preset in use, or ``None``."""
    if preset_id.startswith("user:"):
        try:
            data = lib.store.load(kind, preset_id[len("user:"):])
        except ProfileError:
            return None
        parent_name = str(data.get("inherits") or "")
        if not parent_name:
            return dict(lib.resolver._defaults[kind]), None
        pid, _ = user_core.parent_id(lib.index, kind, parent_name)
        if pid is None:
            return None
        return dict(lib.resolver.resolve(kind, pid).config), parent_name
    entry = lib.index.get_or_renamed(kind, preset_id)
    if entry is None:
        return None
    return dict(lib.resolver.resolve(kind, entry.id).config), entry.name


def edited_flat(pg, role: str, selected_config: dict[str, Any]) -> dict[str, str]:
    """The role's current settings as FlatConfig text (for filaments: overrides laid over the selected preset)."""
    buffer, _ = role_buffer(pg, role)
    if role == "filament":
        base = edits.parent_flat(config_pg.schema(), selected_config)
        return {**base, **config_pg.overridden(buffer)}
    return edits.role_flat(config_pg.schema(), KIND_OF_ROLE[role], config_pg.to_flat(buffer))


def selected_config(lib, pg, role: str) -> dict[str, Any] | None:
    """The resolved config of the preset currently selected for ``role`` (user diff included)."""
    resolved = lib.resolve(KIND_OF_ROLE[role], preset_id_of(pg, role))
    return None if resolved is None else resolved.config


def changes(pg, role: str) -> list[tuple[str, str, str, str]]:
    """``(key, label, preset text, edited text)`` rows of the unsaved edits of ``role`` (the edits are
    compared with the selected preset, which for a user preset includes what it already changed)."""
    lib = library.get()
    buffer, _ = role_buffer(pg, role)
    if lib is None or buffer is None:
        return []
    base = selected_config(lib, pg, role)
    if base is None:
        return []
    return edits.rows(config_pg.schema(), KIND_OF_ROLE[role], base, edited_flat(pg, role, base))


def save(pg, role: str, name: str) -> str:
    """Save the edits of ``role`` as the user preset ``name``; selects it. Returns the new id."""
    lib = library.get()
    kind = KIND_OF_ROLE[role]
    info = parent_info(lib, kind, preset_id_of(pg, role))
    if info is None:
        raise ProfileError("the current preset cannot be resolved, so there is nothing to diff against")
    parent_config, parent_name = info
    selected = selected_config(lib, pg, role) or parent_config
    diff = edits.diff_for_file(config_pg.schema(), kind, parent_config, edited_flat(pg, role, selected))
    lib.store.save(kind, name, parent_name, diff)
    lib.compat.invalidate()
    new_id = lib.store.id_of(user_core.check_name(name))
    set_preset_id(pg, role, new_id)
    reload_buffer(pg, role)
    return new_id


def reload_buffer(pg, role: str) -> None:
    """Throw away the edits of ``role`` and load the selected preset into its buffer."""
    from . import picker
    if role == "filament":
        buffer, slot = role_buffer(pg, role)
        if buffer is not None:
            config_pg.clear(buffer)
    else:
        picker.load_edit_buffers(pg, printer=role == "printer", process=role == "process")


def revert(pg, role: str, key: str | None = None) -> None:
    """Back to the preset's values: one key, or every edit of ``role``."""
    lib = library.get()
    buffer, _ = role_buffer(pg, role)
    base = selected_config(lib, pg, role) if lib else None
    if buffer is None or base is None:
        return
    if key is None:
        reload_buffer(pg, role)
    elif role == "filament":
        config_pg.clear(buffer, key)
    else:
        parent = edits.parent_flat(config_pg.schema(), base)
        spec = config_pg.specs().get(key)
        if spec is not None:
            config_pg.load_flat(buffer, {key: parent.get(key, "") or spec.default}, reset=False)


def delete(pg, role: str, preset_id: str) -> None:
    """Delete a user preset; anything using it falls back to its system parent."""
    lib = library.get()
    kind = KIND_OF_ROLE[role]
    name = preset_id[len("user:"):]
    info = parent_info(lib, kind, preset_id)
    lib.store.delete(kind, name)
    lib.compat.invalidate()
    if info is not None and info[1]:
        pid, _ = user_core.parent_id(lib.index, kind, info[1])
        if pid:
            set_preset_id(pg, role, pid)
    else:
        set_preset_id(pg, role, "")
    reload_buffer(pg, role)


# -- embedding ---------------------------------------------------------------------------------

def name_of(preset_id: str) -> str:
    """The preset name inside ``sys:<vendor>/<name>`` or ``user:<name>``."""
    return preset_id[len("user:"):] if preset_id.startswith("user:") else preset_id.partition("/")[2]


def _item(kind: str, preset_id: str, name: str, inherits: str, config: dict[str, str]) -> dict:
    return {"kind": kind, "id": preset_id, "name": name, "inherits": inherits, "config": config}


def embed_text(pg) -> str | None:
    """JSON for ``embedded_presets``: the flattened modified presets, host keys and secrets stripped.
    ``None`` when the library is not loaded (the previous text is then kept)."""
    lib = library.get()
    if lib is None or not (pg.printer_id or pg.process_id):
        return None
    schema = config_pg.schema()
    out: dict[str, dict | None] = {"printer": None, "process": None}
    for role in ("printer", "process"):
        pid = preset_id_of(pg, role)
        info = parent_info(lib, KIND_OF_ROLE[role], pid) if pid else None
        base = selected_config(lib, pg, role) if info is not None else None
        if info is not None and base is not None:
            out[role] = _item(KIND_OF_ROLE[role], pid, name_of(pid), info[1] or "", edited_flat(pg, role, base))
    filaments = []
    for slot in pg.filaments:
        info = parent_info(lib, "filament", slot.preset_id)
        resolved = lib.resolve("filament", slot.preset_id) if info is not None else None
        if info is not None and resolved is not None:
            flat = {**edits.parent_flat(schema, resolved.config), **config_pg.overridden(slot.overrides)}
            filaments.append(_item("filament", slot.preset_id, name_of(slot.preset_id), info[1] or "", flat))
    return user_core.dumps_embedded(out["printer"], out["process"], filaments)


def embed_all_scenes() -> None:
    for scene in bpy.data.scenes:
        pg = getattr(scene, "slicewright", None)
        if pg is None:
            continue
        try:
            text = embed_text(pg)
        except Exception:  # noqa: BLE001 - a failing embed must never block saving the file
            logs.get_logger("presets").exception("could not embed presets for scene %s", scene.name)
            continue
        if text is not None:
            pg.embedded_presets = text


def embedded_config(pg, kind: str, preset_id: str) -> dict[str, str] | None:
    """The embedded flattened config of a preset that can no longer be found."""
    return user_core.embedded_fallback(user_core.loads_embedded(pg.embedded_presets), kind, preset_id)
