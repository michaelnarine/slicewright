# SPDX-License-Identifier: GPL-3.0-or-later
"""The runtime side of secret storage (03 section 8.5): which backend this session uses, and what the UI says about it.

The pure logic is ``core/network/secrets.py``; this module only picks the file location (the user config directory,
through ``paths.py``, so a source checkout and an installed extension both work and tests can redirect it) and keeps the
one :class:`~slicewright.core.network.secrets.SecretManager` of the session. Nothing here touches Blender properties:
a secret is never stored in a property, the preferences or the .blend. ``unregister`` forgets the manager and every secret
the redaction layer remembers.
"""
from __future__ import annotations

import os

from ..core import redact
from ..core.network import secrets as core_secrets
from . import paths

REQUIRES_ENGINE = False
FILE_NAME = "secrets.json"


class _Runtime:
    manager: core_secrets.SecretManager | None = None


_rt = _Runtime()


def secrets_file() -> str | None:
    """Where the unencrypted fallback would live: ``<user config>/slicewright/secrets/secrets.json``."""
    directory = paths.user_dir("secrets", create=False)
    return os.path.join(directory, FILE_NAME) if directory else None


def manager() -> core_secrets.SecretManager:
    """This session's :class:`SecretManager` (created on first use; no keychain call is made to create it)."""
    if _rt.manager is None:
        path = secrets_file()
        if path is None:
            raise core_secrets.SecretStoreError("Blender has no user folder to keep secrets in")
        _rt.manager = core_secrets.default_manager(path)
    return _rt.manager


def set_manager(mgr: core_secrets.SecretManager | None) -> None:
    """Use ``mgr`` instead of the platform default (tests; ``None`` goes back to the default)."""
    _rt.manager = mgr


def status_lines() -> list[tuple[str, bool]]:
    """``(text, is_warning)`` lines for the preferences' Secrets box. Never calls the keychain."""
    try:
        status = manager().status()
    except core_secrets.SecretStoreError as exc:
        return [(str(exc), True)]
    lines = [(status.text, not status.encrypted)]
    if not status.encrypted:
        lines.append(("Anyone who can read your files can read them. The file is readable only by you.", True))
    if status.permissions_repaired:
        lines.append(("The file was readable by others; its permissions were repaired.", True))
    return lines


def register() -> None:
    _rt.manager = None


def unregister() -> None:
    _rt.manager = None
    redact.clear()
