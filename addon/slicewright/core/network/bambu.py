# SPDX-License-Identifier: GPL-3.0-or-later
"""Bambu Lab printers in LAN mode: **Developer Mode only, unsupported by Bambu** (03 section 8.4). No ``bpy``.

This talks to a printer on the local network with the printer's own **access code**, over two open protocols, and nothing
else: FTPS (implicit TLS, port 990, user ``bblp``; :mod:`~slicewright.core.network.ftps`) to put the ``.gcode.3mf`` into the
printer's storage and MQTT over TLS (port 8883, same credentials; layer 2) to ask for its state and to start a print. It is
written from the public protocol descriptions (OpenBambuAPI, see 03 section 8.4), never from another client's code.

What this module and the code around it never do, in this layer or any other: use Bambu's cloud or any cloud address; load,
bundle, download or imitate Bambu's network plugin; extract or use Bambu Connect keys or certificates; identify as
Bambu Studio, Bambu Connect or Bambu Handy (the FTP user name and the MQTT user name ``bblp`` are the documented LAN
credentials, not a client identity; the MQTT client id is our own); work around, retry around or bypass a refused login.
A refused login **stops** with a message that points to LAN Only mode and Developer Mode.

The certificate is always **pinned** (trust on first use through the printer's pin store): the printer's certificate is
self-signed, there is no "verify" mode to choose and no way to switch checking off. The pin is compared right after the TLS
handshake, before the user name or the access code is sent, on the control and the data connection alike.

Where the file goes: the printer's storage **root** (``/``), under the name it is sent with. Community clients start a
print from ``ftp:///<name>`` for a file in the root; a file in ``/cache`` has been reported to start and then fail on some
firmware, so the root is the choice (03 section 8.4 records the sources).

The access code is a :class:`~slicewright.core.redact.SecretStr`; it becomes text in one place only, the ``PASS`` command
built by ``ftps`` (and the MQTT ``CONNECT`` password in layer 2), and is never in a message, a log line or an exception.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterator

from ..redact import SecretStr
from .errors import InvalidRequest
from .ftps import DEFAULT_PORT as FTPS_PORT, FtpsEndpoint, check as ftps_check, upload as ftps_upload
from .nbsocket import CancelToken, Clock, Timeouts
from .resolve import Resolver, default_resolver
from .tls import TlsPolicy
from .webapi import Transfer

PRODUCT = "Bambu printer"
LABEL = "Bambu LAN mode (unsupported by Bambu)"
USER = "bblp"                           # the documented LAN user name (the password is the printer's access code)
MQTT_PORT = 8883
LOGIN_HINT = (", and that the printer is in LAN Only mode with Developer Mode on (Bambu does not support third-party "
              "tools; the setting is on the printer's screen)")


@dataclass(frozen=True)
class BambuEndpoint:
    """One printer: its address, serial number (needed by MQTT), the access code and the pinning TLS policy."""
    host: str
    tls: TlsPolicy
    access_code: SecretStr | None = None
    serial: str = ""
    port: int = FTPS_PORT
    mqtt_port: int = MQTT_PORT
    timeouts: Timeouts = Timeouts(connect=5.0, idle=15.0)
    upload_timeouts: Timeouts = Timeouts(connect=5.0, idle=60.0)
    resolver: Resolver = default_resolver
    clock: Clock = time.monotonic
    cancel: CancelToken | None = None

    def __post_init__(self) -> None:
        if self.tls.mode != "pin":
            raise ValueError("a Bambu printer is always reached with certificate pinning")

    def __repr__(self) -> str:
        return f"BambuEndpoint({self.host}:{self.port}, serial={self.serial!r}, access_code=***)"

    def ftps(self) -> FtpsEndpoint:
        return FtpsEndpoint(self.host, self.tls, self.access_code, self.port, USER, PRODUCT, LOGIN_HINT, self.timeouts,
                            self.upload_timeouts, self.resolver, self.clock, self.cancel)


@dataclass(frozen=True)
class ConnectionInfo:
    """What a connection test learned. ``data_ok`` is False when login worked but the data channel did not (sending may
    fail); ``problem`` says why."""
    product: str
    data_ok: bool
    entries: int = 0
    problem: str = ""

    def summary(self) -> str:
        text = f"{LABEL}: access code accepted over FTPS, certificate trusted"
        if self.data_ok:
            return text + f", storage reachable ({self.entries} entries in its root)"
        return text + f", but the data channel failed: {self.problem}"


@dataclass(frozen=True)
class UploadResult:
    name: str
    path: str
    size: int
    selected: bool = False
    printing: bool = False                   # True only when the printer says so in a report (layer 2)

    def summary(self) -> str:
        return f"Sent {self.name} to the printer's storage"


def test_connection(ep: BambuEndpoint) -> Iterator:
    """Generator: connect over FTPS (the pin is checked first), log in with the access code and list the storage root
    over a TLS data channel. Returns :class:`ConnectionInfo`. A refused login is
    :class:`~slicewright.core.network.errors.LoginRejected` and is not retried."""
    found = yield from ftps_check(ep.ftps())
    return ConnectionInfo(PRODUCT, found.data_ok, found.entries, found.data_problem)


test_connection.__test__ = False


def upload(ep: BambuEndpoint, file_path: str, name: str, *, start_print: bool = False,
           transfer: Transfer | None = None) -> Iterator:
    """Generator: upload ``file_path`` to the printer's storage root as ``name`` over FTPS; returns
    :class:`UploadResult`. Starting a print is not part of this layer: ``start_print`` must be false."""
    if start_print is True:
        raise InvalidRequest("Starting a print on a Bambu printer is not available yet")
    sent = yield from ftps_upload(ep.ftps(), file_path, name, transfer=transfer)
    return UploadResult(sent.name, sent.path, sent.size)
