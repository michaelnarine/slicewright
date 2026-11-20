# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the add-on keeps its per-user files: the slice cache and the recovery copies (03 section 2.5).

Installed as an extension, that is ``bpy.utils.extension_path_user(<package>, path=...)``. Running from a source
checkout (tests, development) the package is not an extension and Blender refuses the call, so the files go to the
user's config directory (``user_resource('CONFIG')/slicewright/...``) instead, which ``BLENDER_USER_RESOURCES``
redirects. Tests point :func:`set_override` at a temporary directory.
"""
from __future__ import annotations

import os

import bpy

from ..names import PACKAGE_ID

_override: str | None = None


def set_override(root: str | None) -> None:
    """Use ``root`` as the base directory of every :func:`user_dir` (``None``: back to Blender's)."""
    global _override
    _override = root


def _package() -> str:
    """The add-on's root package: ``bl_ext.<repo>.slicewright`` as an extension, ``slicewright`` from source."""
    return __package__.rpartition(".")[0] or PACKAGE_ID


def user_dir(sub: str, *, create: bool = True) -> str | None:
    """The per-user directory ``sub`` (``"cache/slices"``, ``"recovery"``), or ``None`` if Blender has none."""
    if _override is not None:
        path = os.path.join(_override, *sub.split("/"))
        if create:
            os.makedirs(path, exist_ok=True)
        return path
    try:
        return bpy.utils.extension_path_user(_package(), path=sub, create=create)
    except (ValueError, OSError):                      # not an extension (source checkout, tests)
        pass
    try:
        base = bpy.utils.user_resource("CONFIG", path=f"{PACKAGE_ID}/{sub}", create=create)
    except (ValueError, OSError):
        return None
    return base or None
