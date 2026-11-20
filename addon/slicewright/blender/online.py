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

REQUIRES_ENGINE = False

OFF_PREFERENCE = ("Blender's \"Allow Online Access\" is off. Turn it on in Preferences > System > Network to talk to "
                  "printers; nothing is sent until you start it.")
OFF_COMMAND_LINE = ("Blender was started with --offline-mode, which overrides the preference. Restart it without that "
                    "option to talk to printers.")
NOT_REPORTED = "This Blender does not report whether online access is allowed, so the add-on stays offline."

__all__ = ["Access", "network_allowed", "require_network", "blender_access", "draw_gate", "poll_network"]


def blender_access() -> Access:
    """The provider: Blender's own setting, with the reason when it says no."""
    try:
        allowed = bool(bpy.app.online_access)
    except AttributeError:
        return Access(False, NOT_REPORTED)
    if allowed:
        return Access(True)
    if getattr(bpy.app, "online_access_override", False):
        return Access(False, OFF_COMMAND_LINE)
    return Access(False, OFF_PREFERENCE)


def poll_network(cls) -> bool:
    """For an operator ``poll`` classmethod: ``return online.poll_network(cls) and ...``. Sets the reason as the
    tooltip of the greyed-out button."""
    result = network_allowed()
    if not result.allowed:
        cls.poll_message_set(result.reason)
    return result.allowed


def draw_gate(layout) -> bool:
    """A status line: nothing when network features are allowed, else the reason in red. Returns ``allowed`` so the
    caller can grey out what follows (``layout.enabled = allowed``)."""
    result = network_allowed()
    if not result.allowed:
        col = layout.column(align=True)
        col.alert = True
        col.label(text="Network features are off", icon="INTERNET")
        for line in _wrap(result.reason):
            col.label(text=line)
    return result.allowed


def _wrap(text: str, width: int = 70) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + ([line] if line else [])


def register() -> None:
    access.set_provider(blender_access)


def unregister() -> None:
    access.set_provider(None)
