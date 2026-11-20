# SPDX-License-Identifier: GPL-3.0-or-later
"""The error taxonomy of the network code (03 section 8.6). No ``bpy``.

Every error is a :class:`NetError` whose ``str()`` is a short message fit for the Issues list and a status line. The
messages are built from the target (``host:port``), an error kind and, for HTTP, the status line, and from nothing
else: never a request header, a URL query, a request body or a credential. Callers that hold a secret must not put it
in an exception message; :mod:`slicewright.core.network.secrets` redacts what logging would otherwise print.
"""
from __future__ import annotations


class NetError(Exception):
    """Base of every network failure. ``kind`` is a stable word for tests and the UI; ``host``/``port`` name the
    target."""

    kind = "error"

    def __init__(self, message: str, *, host: str = "", port: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.host = host
        self.port = port

    def __str__(self) -> str:
        return self.message


class InvalidRequest(NetError):
    """A URL, header or file the caller handed in is unusable (bad scheme, credentials in the URL, a control
    character in a header). Nothing was sent."""
    kind = "invalid_request"


class ResolveError(NetError):
    """The host name did not resolve."""
    kind = "resolve"


class ConnectionRefused(NetError):
    """Nothing is listening on that port (or a firewall said no)."""
    kind = "refused"


class Unreachable(NetError):
    """No route to the host, or the network is down."""
    kind = "unreachable"


class ConnectionLost(NetError):
    """The connection was reset or closed before the exchange was complete."""
    kind = "lost"


class Timeout(NetError):
    """``phase`` is ``connect`` (TCP or TLS did not complete), ``idle`` (nothing moved for too long) or ``total``."""
    kind = "timeout"

    def __init__(self, message: str, *, phase: str = "idle", host: str = "", port: int | None = None) -> None:
        super().__init__(message, host=host, port=port)
        self.phase = phase


class TlsError(NetError):
    kind = "tls"


class TlsVerificationError(TlsError):
    """The certificate chain or host name did not verify (a self-signed LAN device, usually)."""
    kind = "tls_verify"


class TlsUnknownCertificate(TlsError):
    """Pinning mode and no pin is stored yet: ``fingerprint`` is the leaf SHA-256 the device presented. The caller
    shows it, and only after the user confirms does it store the pin (trust on first use) and try again."""
    kind = "tls_unknown"

    def __init__(self, message: str, *, fingerprint: str, host: str = "", port: int | None = None) -> None:
        super().__init__(message, host=host, port=port)
        self.fingerprint = fingerprint


class TlsPinMismatch(TlsError):
    """The device presents a different certificate than the pinned one. Never repaired automatically: the user must
    remove the pin on purpose."""
    kind = "tls_pin_mismatch"

    def __init__(self, message: str, *, expected: str, got: str, host: str = "", port: int | None = None) -> None:
        super().__init__(message, host=host, port=port)
        self.expected = expected
        self.got = got


class ProtocolError(NetError):
    """The peer said something that is not valid HTTP (or not what the protocol allows here)."""
    kind = "protocol"


class LimitExceeded(ProtocolError):
    """A response head or body is larger than the configured limit; reading stopped."""
    kind = "limit"


class RedirectRefused(NetError):
    """The server redirected somewhere the redirect policy does not allow, or a redirect that cannot be followed
    safely (a POST answered 302). ``location`` is the scheme and host of the target only."""
    kind = "redirect"

    def __init__(self, message: str, *, location: str = "", host: str = "", port: int | None = None) -> None:
        super().__init__(message, host=host, port=port)
        self.location = location


class HttpStatusError(NetError):
    """A well-formed HTTP answer that says no. ``response`` is kept for callers that look at the body; the message
    holds only the status line."""
    kind = "http_status"

    def __init__(self, message: str, *, status: int, reason: str = "", response=None, host: str = "",
                 port: int | None = None) -> None:
        super().__init__(message, host=host, port=port)
        self.status = status
        self.reason = reason
        self.response = response


class AuthRejected(HttpStatusError):
    """The device answered 401 or 403: the API key is missing, wrong or lacks the permission (never says which)."""
    kind = "auth_rejected"


class Conflict(HttpStatusError):
    """HTTP 409: the printer cannot take this now (busy, not ready, a file with that name is in use)."""
    kind = "conflict"


class PayloadTooLarge(HttpStatusError):
    """HTTP 413: the server, or a reverse proxy in front of it, limits the size of an upload."""
    kind = "too_large"


class NotThisServer(NetError):
    """The address answered, but not like the printer software it should be (wrong port, base path or proxy)."""
    kind = "not_this_server"


class LoginRejected(NetError):
    """A device that logs in with a user name and a password (FTPS, MQTT) refused the login: a wrong or missing
    access code, or the device is not in the mode that allows it. Never retried; the message never says which."""
    kind = "login_rejected"


class FtpError(NetError):
    """An FTP reply that says no. ``code`` is the three-digit reply code; the message holds the code and what it
    means, never the server's own text."""
    kind = "ftp"

    def __init__(self, message: str, *, code: int = 0, host: str = "", port: int | None = None) -> None:
        super().__init__(message, host=host, port=port)
        self.code = code


class PermissionDenied(FtpError):
    """FTP 550 or 553 to a store command: the printer will not write that file here."""
    kind = "permission"


class StorageFull(FtpError):
    """FTP 452 or 552: the printer's storage is full (or its quota is spent)."""
    kind = "storage_full"


class DataChannelError(NetError):
    """The FTP data connection could not be set up (passive mode refused, the data port unreachable, its TLS handshake
    refused) or broke."""
    kind = "data_channel"


class PrinterBusy(NetError):
    """The printer reports that it is already printing (or preparing, or paused), so no print is started."""
    kind = "busy"


class UploadError(NetError):
    """The file being uploaded could not be read, or changed while it was sent."""
    kind = "upload"


class Cancelled(NetError):
    """The user cancelled."""
    kind = "cancelled"

    def __init__(self, message: str = "Cancelled", *, host: str = "", port: int | None = None) -> None:
        super().__init__(message, host=host, port=port)


def target(host: str, port: int | None) -> str:
    """``host:port`` for messages (IPv6 literals bracketed)."""
    shown = f"[{host}]" if ":" in host else host
    return f"{shown}:{port}" if port else shown
