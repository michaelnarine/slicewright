# SPDX-License-Identifier: GPL-3.0-or-later
"""Physical printers: the records, their validation and their secret's life cycle (03 section 8.5). No ``bpy``.

A record says *where* a printer is and *how to talk to it*; it never holds a secret. The access code, API key or password
lives in the secret store under the record's ``secret_ref`` (an opaque id made when the record is created), and the
record only remembers *whether* one was saved (``secret_saved``), so drawing the UI never has to ask the keychain.
``options`` is a small, whitelisted dictionary; a key that looks like a credential is rejected by name, so a secret cannot
be tucked into it by mistake.

The records are stored in the add-on preferences (``blender/printers_pg.py`` holds the properties); this module is the
logic, tested without Blender.
"""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field, replace
from typing import Iterable

from .network.secrets import REF_RE, SecretManager, new_ref

OCTOPRINT, MOONRAKER, BAMBU_LAN = "octoprint", "moonraker", "bambu_lan"
KINDS = (OCTOPRINT, MOONRAKER, BAMBU_LAN)
KIND_LABELS = {OCTOPRINT: "OctoPrint", MOONRAKER: "Moonraker / Klipper",
               BAMBU_LAN: "Bambu Lab, LAN Developer Mode (unsupported by Bambu)"}
SECRET_LABELS = {OCTOPRINT: "API key", MOONRAKER: "API key (optional)", BAMBU_LAN: "Access code"}
DEFAULT_PORTS = {OCTOPRINT: 80, MOONRAKER: 7125, BAMBU_LAN: 990}     # Bambu: 990 (FTPS); MQTT is 8883
BAMBU_MQTT_PORT = 8883
SECRET_REQUIRED = {OCTOPRINT: True, MOONRAKER: False, BAMBU_LAN: True}

MAX_NAME, MAX_HOST, MAX_OPTIONS_TEXT, MAX_OPTION_VALUE = 64, 253, 1024, 128
TLS_MODES = ("verify", "pin")
_CTL = re.compile(r"[\x00-\x1f\x7f]")
_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
_SERIAL = re.compile(r"^[A-Za-z0-9]{4,32}$")
_SECRETY_KEY = re.compile(r"(?i)(key|code|pass|secret|token|auth|cred|pwd)")
_BOOL_OPTIONS = {"https"}
_STR_OPTIONS = {"tls", "base_path", "serial"}
_KIND_OPTIONS = {OCTOPRINT: {"https", "tls", "base_path"}, MOONRAKER: {"https", "tls", "base_path"},
                 BAMBU_LAN: {"serial"}}


@dataclass
class PrinterRecord:
    id: str = ""                              # stable identity (also the default pin key)
    name: str = "Printer"
    kind: str = OCTOPRINT
    host: str = ""
    port: int = 0                             # 0: the kind's default
    options: dict = field(default_factory=dict)
    preset: str = ""                          # the Slicewright printer preset (a ``printer_id``) it pairs with
    secret_ref: str = ""
    secret_saved: bool = False

    def __repr__(self) -> str:                # never anything secret here, but keep it short and stable
        return f"<PrinterRecord {self.name!r} {self.kind} {self.host}:{self.effective_port}>"

    @property
    def effective_port(self) -> int:
        """The port to use: the one set, or the kind's default (443 for an HTTP kind with ``https`` on)."""
        if self.port:
            return self.port
        if self.kind in (OCTOPRINT, MOONRAKER) and self.options.get("https"):
            return 443
        return DEFAULT_PORTS.get(self.kind, 0)


@dataclass(frozen=True)
class Problem:
    field: str
    message: str


# -- creating ----------------------------------------------------------------------------------------------------

def new_record(existing: Iterable[PrinterRecord] = (), *, kind: str = OCTOPRINT, **kw) -> PrinterRecord:
    """A new record with a fresh ``id`` and ``secret_ref`` and a name not used yet ("Printer", "Printer 2", ...)."""
    taken = {r.name.lower() for r in existing}
    base = kw.pop("name", None) or "Printer"
    name, n = base, 1
    while name.lower() in taken:
        n += 1
        name = f"{base} {n}"
    kw.setdefault("id", new_ref())
    kw.setdefault("secret_ref", new_ref())
    return PrinterRecord(name=name, kind=kind, **kw)


# -- validation --------------------------------------------------------------------------------------------------

def validate_host(host: str) -> str | None:
    """``None`` if ``host`` is a plausible host name or IP literal (no scheme, port, path, credentials or spaces)."""
    host = host.strip() if isinstance(host, str) else ""
    if not host:
        return "Enter the printer's host name or IP address"
    if len(host) > MAX_HOST or _CTL.search(host) or re.search(r"\s", host):
        return "The host contains spaces or control characters, or is too long"
    if "://" in host or any(c in host for c in "/?#@\\"):
        return "Enter only the host name or IP address, without http://, a path or a user name"
    if host.startswith("[") or host.endswith("]"):
        return "Enter an IPv6 address without square brackets"
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
            return None
        except ValueError:
            return "Enter the port in the Port field, not in the host"
    try:
        ipaddress.IPv4Address(host)
        return None
    except ValueError:
        pass
    if re.fullmatch(r"[0-9.]+", host):
        return "That is not a valid IP address"
    labels = host.rstrip(".").split(".")
    if not all(_LABEL.match(label) for label in labels):
        return "That is not a valid host name"
    return None


def validate_options(kind: str, options: dict) -> list[Problem]:
    problems: list[Problem] = []
    if not isinstance(options, dict):
        return [Problem("options", "Options must be a set of name and value pairs")]
    allowed = _KIND_OPTIONS.get(kind, set())
    for key, value in options.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z_]{1,32}", key):
            problems.append(Problem("options", "An option has an invalid name"))
        elif _SECRETY_KEY.search(key):
            problems.append(Problem("options", f"Option '{key}' looks like a credential; credentials are stored in the "
                                    f"secret store, not in the printer"))
        elif key not in allowed:
            problems.append(Problem("options", f"Option '{key}' does not apply to this kind of printer"))
        elif key in _BOOL_OPTIONS:
            if not isinstance(value, bool):
                problems.append(Problem("options", f"Option '{key}' must be on or off"))
        elif not isinstance(value, str) or len(value) > MAX_OPTION_VALUE or _CTL.search(value):
            problems.append(Problem("options", f"Option '{key}' is not valid"))
        elif key == "tls" and value not in TLS_MODES:
            problems.append(Problem("options", "Option 'tls' must be 'verify' or 'pin'"))
        elif key == "base_path" and (not value.startswith("/") or any(c in value for c in "?# \\")):
            problems.append(Problem("options", "The base path must start with / and have no spaces, ? or #"))
        elif key == "serial" and not _SERIAL.match(value):
            problems.append(Problem("serial", "The serial number is 4 to 32 letters and digits"))
    if len(json.dumps(options, sort_keys=True, default=str)) > MAX_OPTIONS_TEXT:
        problems.append(Problem("options", "The options are too long"))
    return problems


def validate(record: PrinterRecord, others: Iterable[PrinterRecord] = (), *, complete: bool = False) -> list[Problem]:
    """What is wrong with ``record``. ``complete=True`` also demands what sending needs (a host, a Bambu serial);
    without it an unfinished record being edited is only checked for what it already says."""
    problems: list[Problem] = []
    name = record.name.strip() if isinstance(record.name, str) else ""
    if not name or len(name) > MAX_NAME or _CTL.search(record.name or ""):
        problems.append(Problem("name", f"The name is 1 to {MAX_NAME} characters"))
    elif any(o.id != record.id and o.name.strip().lower() == name.lower() for o in others):
        problems.append(Problem("name", "Another printer already has this name"))
    if record.kind not in KINDS:
        problems.append(Problem("kind", "Choose OctoPrint, Moonraker or Bambu Lab"))
    if record.host.strip() or complete:
        message = validate_host(record.host)
        if message:
            problems.append(Problem("host", message))
    if not isinstance(record.port, int) or isinstance(record.port, bool) or not 0 <= record.port <= 65535:
        problems.append(Problem("port", "The port is 1 to 65535 (0 uses the default)"))
    problems += validate_options(record.kind, record.options)
    if complete and record.kind == BAMBU_LAN and not record.options.get("serial"):
        problems.append(Problem("serial", "A Bambu printer needs its serial number to be sent to"))
    if record.secret_ref and not REF_RE.match(record.secret_ref):
        problems.append(Problem("secret_ref", "The secret reference is invalid"))
    if len(record.preset) > 200 or _CTL.search(record.preset):
        problems.append(Problem("preset", "The printer preset name is not valid"))
    if complete and SECRET_REQUIRED.get(record.kind) and not record.secret_saved:
        problems.append(Problem("secret", f"Set the {SECRET_LABELS.get(record.kind, 'secret').lower()}"))
    return problems


def base_url(record: PrinterRecord) -> str:
    """``scheme://host[:port][base_path]`` for the HTTP kinds (later layers' clients start from this)."""
    if record.kind not in (OCTOPRINT, MOONRAKER):
        raise ValueError("only OctoPrint and Moonraker printers have an HTTP address")
    https = bool(record.options.get("https"))
    host = f"[{record.host}]" if ":" in record.host else record.host
    port = record.effective_port
    authority = host if port == (443 if https else 80) else f"{host}:{port}"
    return f"{'https' if https else 'http'}://{authority}{record.options.get('base_path', '').rstrip('/')}"


# -- options as text (the preferences' storage) ------------------------------------------------------------------

def options_to_text(options: dict) -> str:
    return json.dumps(options, sort_keys=True, separators=(",", ":"))


def options_from_text(text: str) -> dict:
    """The options dict from stored text; an empty or damaged text is ``{}`` (never an exception in a draw call)."""
    if not text or len(text) > MAX_OPTIONS_TEXT:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# -- the secret's life cycle -------------------------------------------------------------------------------------

def save_secret(record: PrinterRecord, secret: str, manager: SecretManager) -> PrinterRecord:
    """Store ``secret`` under the record's ``secret_ref`` (made now if the record has none) and mark it saved."""
    ref = record.secret_ref or new_ref()
    manager.set(ref, secret)                  # raises on failure; the record is untouched then
    return replace(record, secret_ref=ref, secret_saved=True)


def clear_secret(record: PrinterRecord, manager: SecretManager) -> PrinterRecord:
    if record.secret_ref:
        manager.delete(record.secret_ref)
    return replace(record, secret_saved=False)


def remove_record(records: list[PrinterRecord], printer_id: str, manager: SecretManager | None) -> PrinterRecord | None:
    """Remove the record and its secret; returns the record, or ``None`` if there was none. The secret goes first, so
    a keychain that refuses leaves both in place (and the error to the caller) rather than an orphaned secret."""
    for index, record in enumerate(records):
        if record.id == printer_id:
            if manager is not None and record.secret_ref:
                manager.delete(record.secret_ref)
            del records[index]
            return record
    return None
