# SPDX-License-Identifier: GPL-3.0-or-later
"""File names, folders and atomic writes for exporting a slice (03 section 8.1). No ``bpy``.

The engine's ``output_filename(basename)`` (04 section 5.1) is Orca's ``filename_format`` evaluated verbatim: a dot in
the basename is not an extension, so the add-on must never run ``splitext`` over a name it did not make
(``bpy.path.ensure_ext`` would turn ``my.part_PLA`` into ``my.gcode``). Everything here only ever *appends* an extension,
and strips one only when it is exactly one of ours.
"""
from __future__ import annotations

import os
import re
from typing import Callable

GCODE_EXT = ".gcode"
GCODE_EXTS = (".gcode", ".gco")            # what a typed name may already end in
THREEMF_EXT = ".gcode.3mf"
UNTITLED = "untitled"
MAX_NAME = 180                              # bytes of a file name's stem: well under every file system's 255

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class ExportError(Exception):
    """An export that cannot go ahead; the message is for the user. ``exists`` marks "the file is already there"."""

    def __init__(self, message: str, *, exists: bool = False) -> None:
        super().__init__(message)
        self.exists = exists


# -- names -------------------------------------------------------------------------------------------------------

def safe_filename(name: str, fallback: str = "slicewright") -> str:
    """``name`` made usable as one file name on every OS: path separators and the characters Windows forbids become
    ``_``, control characters go, a trailing dot or space is dropped, a reserved device name (``CON``, ``NUL``, ...) gets
    a ``_`` prefix and an overlong name is cut. Never empty."""
    cleaned = _ILLEGAL.sub("_", str(name or "")).strip().rstrip(". ")
    cleaned = cleaned.lstrip(".")                      # no hidden files from a name the format made
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    while len(cleaned.encode("utf-8")) > MAX_NAME:
        cleaned = cleaned[:-1]
    return cleaned.rstrip(". ") or fallback


def _strip_suffix(name: str, suffixes) -> str:
    low = name.lower()
    for suffix in suffixes:
        if low.endswith(suffix):
            return name[:-len(suffix)]
    return name


def gcode_filename(name: str) -> str:
    """``name`` (the engine's ``output_filename``, or a typed name) as a G-code file name: sanitised, ending in
    ``.gcode`` (``.gco`` is kept). An extension is only added, never replaced."""
    safe = safe_filename(name)
    return safe if safe.lower().endswith(GCODE_EXTS) else safe + GCODE_EXT


def threemf_filename(name: str) -> str:
    """``name`` as a ``.gcode.3mf`` file name: the G-code extension of the engine's name (or a typed ``.3mf``) is replaced
    by ``.gcode.3mf``; any other dot stays part of the name."""
    safe = safe_filename(_strip_suffix(str(name or ""), (THREEMF_EXT, ".3mf", *GCODE_EXTS)))
    return safe + THREEMF_EXT


def with_extension(filepath: str, kind: str) -> str:
    """``filepath`` with its file name normalised for ``kind`` (``"gcode"`` or ``"3mf"``); the folder is untouched.
    This is what the file dialog's ``check`` hook applies to what the user typed."""
    folder, name = os.path.split(filepath)
    fixed = threemf_filename(name) if kind == "3mf" else gcode_filename(name)
    return os.path.join(folder, fixed)


def input_basename(blend_path: str | None) -> str:
    """The ``input_filename_base`` for the engine: the .blend's name without ``.blend`` and nothing else removed
    (``my.part.blend`` is ``my.part``); ``untitled`` for a file that was never saved."""
    name = os.path.basename(blend_path or "")
    base = name[:-len(".blend")] if name.lower().endswith(".blend") else name
    return base or UNTITLED


def start_directory(blend_path: str | None, last_dir: str | None, home: str) -> str:
    """Where the file dialog opens: the .blend's folder when it was saved, else the last export folder (when it is
    still there), else ``home``."""
    for candidate in (os.path.dirname(blend_path) if blend_path else "", last_dir or ""):
        if candidate and os.path.isdir(candidate):
            return candidate
    return home


def default_path(directory: str, engine_name: str, kind: str) -> str:
    """The dialog's initial file path: ``directory`` plus the engine's name made into a ``kind`` file name."""
    name = threemf_filename(engine_name) if kind == "3mf" else gcode_filename(engine_name)
    return os.path.join(directory, name)


# -- writing -----------------------------------------------------------------------------------------------------

def check_destination(dest: str, *, overwrite: bool = False) -> None:
    """Raise :class:`ExportError` when ``dest`` cannot be written: the folder is missing, ``dest`` is a folder, or the
    file exists and ``overwrite`` is not set (the user has not confirmed it)."""
    folder = os.path.dirname(dest) or "."
    if not os.path.isdir(folder):
        raise ExportError(f"The folder {folder} does not exist")
    if os.path.isdir(dest):
        raise ExportError(f"{os.path.basename(dest)} is a folder")
    if os.path.exists(dest) and not overwrite:
        raise ExportError(f"{os.path.basename(dest)} already exists", exists=True)


def temp_path(dest: str) -> str:
    """A hidden sibling of ``dest`` to write into (same folder, so the final rename is atomic)."""
    folder, name = os.path.split(dest)
    return os.path.join(folder, f".{name}.{os.getpid()}.part")


def atomic_write(dest: str, write: Callable[[str], None], *, overwrite: bool = False) -> int:
    """Run ``write(temp_path)`` and move the result onto ``dest`` in one rename, so a crash, a full disk or a cancelled
    export never leaves a half-written ``dest`` (and never damages the file it would have replaced). Returns the file
    size in bytes. The temp file is removed on any failure. Raises :class:`ExportError` for a bad destination, a
    write that produced nothing, or an OS error."""
    check_destination(dest, overwrite=overwrite)
    tmp = temp_path(dest)
    try:
        write(tmp)
        if not os.path.isfile(tmp):
            raise ExportError("The write produced no file")
        size = os.path.getsize(tmp)
        if not overwrite and os.path.exists(dest):            # appeared while we wrote
            raise ExportError(f"{os.path.basename(dest)} already exists", exists=True)
        os.replace(tmp, dest)
        return size
    except ExportError:
        raise
    except OSError as exc:
        raise ExportError(f"Could not write {os.path.basename(dest)}: {exc.strerror or exc}") from exc
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
