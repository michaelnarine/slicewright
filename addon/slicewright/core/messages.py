# SPDX-License-Identifier: GPL-3.0-or-later
"""Plain-language wording for the engine's known messages (03 section 6.5a). No ``bpy``.

The engine's text is written for logs ("Object 'Far cube' is outside the printable area (printable_area minus
bed_exclude_area)."). The panels show :func:`plain` instead, which puts the object name first and drops the setting
names; the engine's own text stays in the Issue (``message``) and is shown in the row's tooltip. Anything not known is
returned unchanged, so no text is ever lost.
"""
from __future__ import annotations

import re

_QUOTED = re.compile(r"^Object '(?P<name>.+?)' ")
_SUBJECT = re.compile(r"^(?P<name>.+?) (?:is outside|overlaps|is taller|goes below)\b")


def object_of(issue: dict) -> str:
    """The object an issue is about: its ``object_name``, else the name the message starts with."""
    name = issue.get("object_name")
    if name:
        return str(name)
    message = str(issue.get("message") or "")
    m = _QUOTED.match(message) or _SUBJECT.match(message)
    return m.group("name") if m else ""


def plain(issue: dict) -> str:
    """The sentence the panel shows for ``issue``."""
    code, message = issue.get("code"), str(issue.get("message") or "")
    name = object_of(issue)
    if code == "object_outside_bed" and name:
        if "exclude area" in message and "printable_area minus" not in message:
            return f"{name} overlaps a no-print zone of the bed"
        return f"{name} is outside the print area"
    if code == "object_too_tall" and name:
        return f"{name} is too tall for this printer"
    if code == "validation" and issue.get("opt_key") == "printable_area":
        return "The printer's bed shape is not usable"
    m = _QUOTED.match(message)
    if m:                                       # "Object 'X' has ..." reads better as "X has ..."
        return f"{m.group('name')} {message[m.end():]}"
    return message


def tooltip(issue: dict) -> str:
    """``plain`` plus the engine's own text when it differs, for a row's tooltip."""
    text = plain(issue)
    raw = str(issue.get("message") or "")
    return text if raw == text or not raw else f"{text}\nEngine message: {raw}"
