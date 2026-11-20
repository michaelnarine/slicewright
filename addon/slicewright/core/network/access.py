# SPDX-License-Identifier: GPL-3.0-or-later
"""May the add-on touch the network right now? One function answers (03 section 8.6). No ``bpy``.

Everything that would open a socket asks :func:`network_allowed`; the operators and panels use it to grey out their
buttons and to say why, and :func:`~slicewright.core.network.nbsocket.connect` calls :func:`require_network` as the last
line of defence, so a path that forgot to ask still cannot connect. What counts as "allowed" is decided by a *provider*
that the Blender glue installs (``blender/online.py`` reads ``bpy.app.online_access``); with no provider installed, as in a
plain Python process, the answer is **no**, so pure-Python code never connects unless a test or a script says it may.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .errors import NetError

NO_PROVIDER = "Network access has not been enabled by the add-on (it is not running inside Blender)."


@dataclass(frozen=True)
class Access:
    """``allowed``, and when not, ``reason``: a sentence for the user that says what to change."""
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


class OnlineAccessDenied(NetError):
    """Raised by :func:`require_network`; the message is the reason."""
    kind = "online_access"


Provider = Callable[[], Access]


class _State:
    provider: Provider | None = None


_state = _State()


def set_provider(provider: Provider | None) -> None:
    """Install (or, with ``None``, remove) the function that decides."""
    _state.provider = provider


def network_allowed() -> Access:
    """The single gate: ``Access(True)`` or ``Access(False, reason)``. Never raises."""
    provider = _state.provider
    if provider is None:
        return Access(False, NO_PROVIDER)
    try:
        answer = provider()
    except Exception:  # noqa: BLE001 - when in doubt, no network
        return Access(False, "Network access could not be checked.")
    return answer if isinstance(answer, Access) else Access(False, "Network access could not be checked.")


def require_network() -> None:
    """Raise :class:`OnlineAccessDenied` unless :func:`network_allowed`."""
    access = network_allowed()
    if not access.allowed:
        raise OnlineAccessDenied(access.reason)


ALLOW = Access(True)
