# SPDX-License-Identifier: GPL-3.0-or-later
"""TLS verification policy and certificate pinning (03 section 8.4 "TLS trust"). No ``bpy``.

Two modes:

* ``verify`` (the default): the platform's trusted roots (or ``cafile``, for a private CA) and the host name are
  checked like any HTTPS client. A device with a self-signed certificate fails with ``TlsVerificationError``.
* ``pin``: trust on first use, for self-signed LAN devices. No chain is checked; instead the SHA-256 of the **leaf
  certificate** is compared with the pin stored for ``pin_key``. No stored pin raises ``TlsUnknownCertificate`` with the
  fingerprint, so the UI can show it and store it **after the user confirms**; a different certificate than the pinned
  one raises ``TlsPinMismatch`` and is never replaced automatically.

The pin is checked right after the handshake and before the first application byte is sent, so no header (an API key,
say) reaches a device that failed its check. The pin store is an interface only here (``PinStore``); where it is saved
comes with the printer preferences.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import ssl
from dataclasses import dataclass
from typing import Protocol

from .errors import TlsPinMismatch, TlsUnknownCertificate, TlsVerificationError, TlsError

VERIFY, PIN = "verify", "pin"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class PinStore(Protocol):
    """Where leaf-certificate fingerprints are kept, by an opaque key (a printer's id, or ``host:port``)."""

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, fingerprint: str) -> None: ...
    def remove(self, key: str) -> None: ...


class MemoryPinStore:
    """A ``PinStore`` that forgets everything at exit: for tests, and the default until pins are persisted."""

    def __init__(self, pins: dict[str, str] | None = None) -> None:
        self._pins = {k: normalize_fingerprint(v) for k, v in (pins or {}).items()}

    def get(self, key: str) -> str | None:
        return self._pins.get(key)

    def set(self, key: str, fingerprint: str) -> None:
        self._pins[key] = normalize_fingerprint(fingerprint)

    def remove(self, key: str) -> None:
        self._pins.pop(key, None)


def normalize_fingerprint(value: str) -> str:
    """Lower-case hex without separators; ``ValueError`` unless it is a SHA-256 (64 hex digits)."""
    cleaned = re.sub(r"[\s:]", "", str(value)).lower()
    if not _HEX64.match(cleaned):
        raise ValueError("a SHA-256 fingerprint is 64 hexadecimal digits")
    return cleaned


def fingerprint_of(der_certificate: bytes) -> str:
    """The SHA-256 of a DER certificate, as lower-case hex."""
    return hashlib.sha256(der_certificate).hexdigest()


def format_fingerprint(fingerprint: str) -> str:
    """``AB:CD:...`` for showing to a user."""
    fp = normalize_fingerprint(fingerprint).upper()
    return ":".join(fp[i:i + 2] for i in range(0, 64, 2))


@dataclass(frozen=True)
class TlsPolicy:
    mode: str = VERIFY
    pin_key: str = ""                  # required in pin mode
    pin_store: PinStore | None = None  # required in pin mode
    cafile: str | None = None          # verify mode: extra trusted CA (a private CA, or a test certificate)
    min_version: ssl.TLSVersion = ssl.TLSVersion.TLSv1_2

    def __post_init__(self) -> None:
        if self.mode not in (VERIFY, PIN):
            raise ValueError(f"unknown TLS mode {self.mode!r}")
        if self.mode == PIN and (not self.pin_key or self.pin_store is None):
            raise ValueError("pin mode needs a pin_key and a pin_store")

    def make_context(self) -> ssl.SSLContext:
        """A client context: verifying (``verify``) or not verifying the chain (``pin``, which pins instead)."""
        if self.mode == VERIFY:
            ctx = ssl.create_default_context(cafile=self.cafile)
        else:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = self.min_version
        return ctx

    def check_peer(self, der_certificate: bytes | None, *, host: str, port: int) -> str | None:
        """Called once the handshake is done. In pin mode returns the fingerprint if it matches, raises
        ``TlsUnknownCertificate`` / ``TlsPinMismatch`` otherwise; in verify mode the handshake already did the work."""
        if self.mode == VERIFY:
            return None
        if not der_certificate:
            raise TlsError("The device presented no certificate", host=host, port=port)
        got = fingerprint_of(der_certificate)
        assert self.pin_store is not None
        expected = self.pin_store.get(self.pin_key)
        if expected is None:
            raise TlsUnknownCertificate(
                f"{host}:{port} presented a certificate that is not trusted yet "
                f"(SHA-256 {format_fingerprint(got)})", fingerprint=got, host=host, port=port)
        if not hmac.compare_digest(normalize_fingerprint(expected), got):
            raise TlsPinMismatch(
                f"{host}:{port} presented a different certificate than the one trusted before. If the device was "
                f"reset or replaced, remove the stored certificate and trust the new one on purpose",
                expected=normalize_fingerprint(expected), got=got, host=host, port=port)
        return got


def explain_ssl_error(exc: ssl.SSLError, *, host: str, port: int) -> TlsError:
    """Map an ``ssl.SSLError`` raised during the handshake to our taxonomy, with a message that carries no detail from
    the peer beyond the OpenSSL reason word."""
    reason = (getattr(exc, "reason", None) or "").upper()
    verify_failed = isinstance(exc, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in reason
    if verify_failed:
        return TlsVerificationError(
            f"{host}:{port} has a certificate that could not be verified (a self-signed device certificate, "
            f"or the host name does not match). Trust it by its fingerprint instead, if it is your own device",
            host=host, port=port)
    return TlsError(f"TLS handshake with {host}:{port} failed ({reason.lower().replace('_', ' ') or 'protocol error'})",
                    host=host, port=port)
