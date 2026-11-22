# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender's "Allow Online Access" as the add-on's one network gate (03 section 8.6).

``network_allowed()`` is the single question every network path asks (the implementation is
``core/network/access.py``; this module installs Blender's answer as its provider). It reads ``bpy.app.online_access``,
which is Blender's preference (Preferences > System > Network > Allow Online Access) as modified by the command line
(``--online-mode`` / ``--offline-mode``, reported by ``bpy.app.online_access_override``). The answer is read afresh on every
call, so switching the preference takes effect without restarting. ``--factory-startup`` and background mode start with
access off.

UI helpers: :func:`draw_gate` writes the status line, :func:`poll_network` is for an operator's ``poll`` (greys the button
and sets the tooltip to the reason). Panels grey a whole block with ``layout.enabled = network_allowed().allowed``.
"""
from __future__ import annotations

import bpy

from ..core.network import access
from ..core.network.access import Access, network_allowed, require_network
from ..names import PACKAGE_ID
from . import registry
from .labels import draw_wrapped

REQUIRES_ENGINE = False

OFF_PREFERENCE = ("Blender's \"Allow Online Access\" is off. Turn it on in Preferences > System > Network to talk to "
                  "printers; nothing is sent until you start it.")
OFF_PREFERENCE_SHORT = "Turn on Allow Online Access in Preferences > System > Network."
OFF_COMMAND_LINE = ("Blender was started with --offline-mode, which overrides the preference. Restart it without that "
                    "option to talk to printers.")
OFF_COMMAND_LINE_SHORT = "Blender was started with --offline-mode. Restart it without that option."
NOT_REPORTED = "This Blender does not report whether online access is allowed, so the add-on stays offline."
NOT_REPORTED_SHORT = "This Blender does not report online access, so the add-on stays offline."
HEADLINE = "Network features are off"

__all__ = ["Access", "network_allowed", "require_network", "blender_access", "draw_gate", "poll_network"]


def blender_access() -> Access:
    """The provider: Blender's own setting, with the reason when it says no."""
    try:
        allowed = bool(bpy.app.online_access)
    except AttributeError:
        return Access(False, NOT_REPORTED, NOT_REPORTED_SHORT)
    if allowed:
        return Access(True)
    if getattr(bpy.app, "online_access_override", False):
        return Access(False, OFF_COMMAND_LINE, OFF_COMMAND_LINE_SHORT)
    return Access(False, OFF_PREFERENCE, OFF_PREFERENCE_SHORT)


def poll_network(cls) -> bool:
    """For an operator ``poll`` classmethod: ``return online.poll_network(cls) and ...``. Sets the reason as the
    tooltip of the greyed-out button."""
    result = network_allowed()
    if not result.allowed:
        cls.poll_message_set(result.reason)
    return result.allowed


def draw_gate(layout) -> bool:
    """A status block: nothing when network features are allowed, else a red headline, one short instruction (wrapped
    to the panel) and, where it helps, an "Open Preferences" button whose tooltip is the full reason. Returns
    ``allowed`` so the caller can grey out what follows (``layout.enabled = allowed``)."""
    result = network_allowed()
    if not result.allowed:
        col = layout.column(align=True)
        col.alert = True
        col.label(text=HEADLINE, icon="INTERNET")
        draw_wrapped(col, result.short or result.reason, indent=2)
        col.alert = False
        if result.reason == OFF_PREFERENCE:
            col.operator(f"{PACKAGE_ID}.show_online_prefs", icon="PREFERENCES")
    return result.allowed


class SLICEWRIGHT_OT_show_online_prefs(bpy.types.Operator):
    bl_idname = f"{PACKAGE_ID}.show_online_prefs"
    bl_label = "Open Network Preferences"
    bl_options = {"INTERNAL"}

    @classmethod
    def description(cls, context, properties):
        return network_allowed().reason or "Open Blender's System preferences"

    def execute(self, context):
        try:
            bpy.ops.screen.userpref_show("INVOKE_DEFAULT", section="SYSTEM")       # opens the window; changes nothing
        except RuntimeError:
            self.report({"INFO"}, "Open Edit > Preferences > System > Network")
        return {"FINISHED"}


classes = (SLICEWRIGHT_OT_show_online_prefs,)


def register() -> None:
    registry.register_classes(classes)
    access.set_provider(blender_access)


def unregister() -> None:
    access.set_provider(None)
    registry.unregister_classes(classes)
