# SPDX-License-Identifier: GPL-3.0-or-later
"""Import user presets from OrcaSlicer / BambuStudio (03 section 3.9). Runs only when the user asks.

No ``from __future__ import annotations``: Blender evaluates property annotations without this
module's globals, so ``CollectionProperty(type=<our class>)`` must be a real object.

The operator scans the other apps' user preset folders (read-only), lists what it found with checkboxes,
and writes the chosen presets into this add-on's own user preset folder. Nothing is scanned at start-up.
"""
import os
import sys

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty

from ...core import logs
from ...core.profiles import importer
from ...names import PACKAGE_ID
from .. import library, picker

_dirs_override: list[list[tuple[str, str]] | None] = [None]
_pending: list[importer.Candidate] = []              # what the open dialog is listing, by index


def set_dirs_override(dirs: list[tuple[str, str]] | None) -> None:
    """Tests point the scan at synthetic folders; ``None`` goes back to the real locations."""
    _dirs_override[0] = dirs


def find_candidates() -> importer.ScanResult:
    """Scan every existing user data folder of the other apps."""
    dirs = _dirs_override[0]
    if dirs is None:
        dirs = importer.candidate_dirs(sys.platform, os.path.expanduser("~"), dict(os.environ))
    result = importer.ScanResult()
    for app, base in dirs:
        if os.path.isdir(base):
            found = importer.scan(base, app)
            result.candidates += found.candidates
            result.skipped += found.skipped
    return result


def run_import(candidates: list[importer.Candidate]) -> list[importer.ImportResult]:
    lib = library.get()
    items = importer.plan_import(candidates, lib.index, lib.resolver, lib.store)
    results = importer.apply_import(items, lib.store)
    lib.compat.invalidate()
    return results


class SLICEWRIGHT_PG_ImportItem(bpy.types.PropertyGroup):
    selected: BoolProperty(name="Import", default=True)
    label: StringProperty()
    note: StringProperty()


class SLICEWRIGHT_OT_import_presets(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.import_presets"
    bl_label = "Import Presets..."
    bl_description = ("Look for your user presets in the OrcaSlicer and BambuStudio folders and copy the "
                      "ones you tick into this add-on. Nothing in those folders is changed")
    bl_options = {"INTERNAL"}

    items: CollectionProperty(type=SLICEWRIGHT_PG_ImportItem)

    @classmethod
    def poll(cls, context):
        return library.get() is not None

    def _scan(self) -> importer.ScanResult:
        found = find_candidates()
        _pending[:] = found.candidates
        self.items.clear()
        for cand in found.candidates:
            item = self.items.add()
            item.label = f"{cand.app}: {cand.name}"
            item.note = cand.kind
        return found

    def invoke(self, context, event):
        found = self._scan()
        if not found.candidates:
            self.report({"INFO"}, "No OrcaSlicer or BambuStudio user presets found")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        col = self.layout.column()
        col.label(text=f"{len(self.items)} presets found. Tick the ones to import.")
        for item in self.items:
            row = col.row()
            row.prop(item, "selected", text=item.label)
            row.label(text=item.note)

    def execute(self, context):
        if not self.items:                               # run without the dialog: take everything found
            self._scan()
        chosen = [c for c, item in zip(_pending, self.items) if item.selected]
        if not chosen:
            _pending.clear()
            return {"CANCELLED"}
        results = run_import(chosen)
        _pending.clear()
        done = [r for r in results if r.error is None]
        for r in results:
            for warning in r.item.warnings:
                logs.get_logger("importer").warning("%s: %s", r.item.name, warning)
            if r.error:
                self.report({"WARNING"}, f"{r.item.name}: {r.error}")
        self.report({"INFO"}, f"Imported {len(done)} of {len(results)} presets")
        picker.load_edit_buffers(context.scene.slicewright)
        return {"FINISHED"}


classes = (SLICEWRIGHT_PG_ImportItem, SLICEWRIGHT_OT_import_presets)
