# SPDX-License-Identifier: GPL-3.0-or-later
"""Bambu Lab printers in LAN mode: **Developer Mode only, unsupported by Bambu** (03 section 8.4). No ``bpy``.

This talks to a printer on the local network with the printer's own **access code**, over two open protocols, and nothing
else: FTPS (implicit TLS, port 990, user ``bblp``; :mod:`~slicewright.core.network.ftps`) to put the ``.gcode.3mf`` into the
printer's storage and MQTT 3.1.1 over TLS (port 8883, same credentials; :mod:`~slicewright.core.network.mqtt`) to ask for its
state and to start a print. It is written from the public protocol descriptions (OpenBambuAPI and a community protocol
reference, see 03 section 8.4), never from another client's code.

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

The access code is a :class:`~slicewright.core.redact.SecretStr`; it becomes text in two places only, the ``PASS`` command
built by ``ftps`` and the ``CONNECT`` password built by ``mqtt``, and is never in a message, a log line or an exception.

**Starting a print** (``upload(..., start_print=True)``, only after the user's explicit confirmation, which the add-on
asks for before it gets here) sends ``print.project_file`` on ``device/<serial>/request`` and reports "selected" or
"printing" **only from what the printer says in a report** on ``device/<serial>/report``: a ``project_file`` answer with
``result`` ``success`` means *accepted*, a ``gcode_state`` of ``PREPARE`` or ``RUNNING`` for our file means *printing*. A
printer that is already busy is not asked; a command nothing confirms is reported as unconfirmed, never as started. A
refused MQTT login after a successful upload stops there (no retry, no other route) and says so.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Iterator

from ..redact import SecretStr
from ..ticking import IDLE
from .errors import InvalidRequest, NetError, PrinterBusy
from .ftps import DEFAULT_PORT as FTPS_PORT, FtpsEndpoint, check as ftps_check, upload as ftps_upload
from .mqtt import MqttClient, MqttEndpoint, open_client
from .nbsocket import CancelToken, Clock, Timeouts
from .resolve import Resolver, default_resolver
from .tls import TlsPolicy
from .webapi import Transfer

PRODUCT = "Bambu printer"
LABEL = "Bambu LAN mode (unsupported by Bambu)"
USER = "bblp"                           # the documented LAN user name (the password is the printer's access code)
MQTT_PORT = 8883
SERIAL_RE = re.compile(r"^[A-Za-z0-9]{4,32}$")
BUSY_STATES = ("PREPARE", "RUNNING", "PAUSE")
PRINTING_STATES = ("PREPARE", "RUNNING")
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

    def mqtt(self) -> MqttEndpoint:
        return MqttEndpoint(self.host, self.tls, self.mqtt_port, USER, self.access_code, PRODUCT, LOGIN_HINT,
                            self.timeouts, self.resolver, self.clock, self.cancel)

    def topics(self) -> tuple[str, str]:
        """``(report, request)``: the serial number is checked first (letters and digits only: no wildcard or separator
        can get into a topic)."""
        if not SERIAL_RE.match(self.serial or ""):
            raise InvalidRequest("Enter the printer's serial number (4 to 32 letters and digits) in its settings")
        return f"device/{self.serial}/report", f"device/{self.serial}/request"


@dataclass(frozen=True)
class PrinterStatus:
    """What the printer's reports said: ``state`` is ``gcode_state`` (``IDLE``, ``RUNNING``...), ``firmware`` the ``ota``
    module's version; either is ``None`` when it was not reported in time."""
    state: str | None = None
    firmware: str | None = None
    gcode_file: str = ""
    subtask_name: str = ""


@dataclass(frozen=True)
class PrintOptions:
    """The fields of ``print.project_file`` the user can influence. ``ams_mapping`` has one entry per filament of the
    project: the AMS tray it comes from, ``-1`` for one the plate does not use. Calibration and inspection stay off: a
    plain start of the file as sliced."""
    plate: int = 1
    use_ams: bool = False
    ams_mapping: tuple[int, ...] = ()
    bed_leveling: bool = True
    timelapse: bool = False
    flow_cali: bool = False
    vibration_cali: bool = False
    layer_inspect: bool = False
    settle: float = 1.0                    # seconds between the end of the upload and the command (some firmware needs it)
    status_wait: float = 8.0               # how long to wait for the printer's first status report
    confirm_wait: float = 20.0             # how long to wait for the printer to confirm the command


@dataclass(frozen=True)
class PrintOutcome:
    """What the printer confirmed about a start. ``accepted``: it answered ``project_file`` with success. ``printing``: it
    reported ``PREPARE`` or ``RUNNING`` for this file. ``detail`` says what is missing or went wrong ("" when printing)."""
    accepted: bool
    printing: bool
    state: str | None
    detail: str = ""


@dataclass(frozen=True)
class ConnectionInfo:
    """What a connection test learned. ``data_ok`` is False when login worked but the FTPS data channel did not (sending may
    fail); ``problem`` says why. ``status_heard`` is False when MQTT logged in but the printer sent no report (wrong serial
    number, usually)."""
    product: str
    data_ok: bool
    entries: int = 0
    problem: str = ""
    state: str | None = None
    firmware: str | None = None
    status_heard: bool = True
    serial: str = ""

    def summary(self) -> str:
        text = f"{LABEL}: access code accepted (FTPS and MQTT), certificate trusted"
        if self.status_heard:
            text += f", printer state {self.state or 'unknown'}"
            if self.firmware:
                text += f", firmware {self.firmware}"
        else:
            text += (f", but the printer sent no status for serial number {self.serial} "
                     f"(check the serial number in its settings)")
        if self.data_ok:
            return text + f", storage reachable ({self.entries} entries in its root)"
        return text + f", but the FTPS data channel failed: {self.problem}"


@dataclass(frozen=True)
class UploadResult:
    name: str
    path: str
    size: int
    selected: bool = False                   # the printer accepted the print command (from its own report)
    printing: bool = False                   # the printer reports PREPARE or RUNNING for this file (from its own report)
    note: str = ""                           # why a requested print is not confirmed

    def summary(self) -> str:
        text = f"Sent {self.name} to the printer's storage"
        if self.printing:
            return text + " and the printer reports it is printing it"
        if self.selected:
            text += " and the printer accepted the print command"
        return text + (f" ({self.note})" if self.note else "")


def _text(value, limit: int = 60) -> str:
    return "".join(c for c in value if c.isprintable())[:limit] if isinstance(value, str) else ""


def _report(message) -> dict | None:
    """The JSON object of a report message, or ``None`` (not JSON, not an object)."""
    try:
        value = json.loads(message.payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _absorb(report: dict, status: dict) -> None:
    """Fold one report into ``status`` (keys ``state``, ``firmware``, ``gcode_file``, ``subtask_name``)."""
    pr = report.get("print")
    if isinstance(pr, dict) and (pr.get("command") == "push_status" or "gcode_state" in pr):
        state = _text(pr.get("gcode_state"), 24).upper()
        if state:
            status["state"] = state
        status["gcode_file"] = _text(pr.get("gcode_file"), 200)
        status["subtask_name"] = _text(pr.get("subtask_name"), 200)
    info = report.get("info")
    if isinstance(info, dict) and info.get("command") == "get_version" and isinstance(info.get("module"), list):
        for module in info["module"]:
            if isinstance(module, dict) and module.get("name") == "ota" and _text(module.get("sw_ver")):
                status["firmware"] = _text(module.get("sw_ver"), 32)


def _open_session(ep: BambuEndpoint) -> Iterator:
    """Connect over MQTT (pin checked, login) and subscribe to the printer's report topic. Returns the client."""
    report, _request = ep.topics()
    client = yield from open_client(ep.mqtt())
    try:
        yield from client.subscribe(report)
    except BaseException:
        client.close()
        raise
    return client


def _request_status(client: MqttClient, ep: BambuEndpoint, wait: float) -> Iterator:
    """Ask for the full status and the version (``pushall``, ``get_version``) and collect the reports for up to ``wait``
    seconds (it stops early once both were heard). Returns :class:`PrinterStatus`."""
    report_topic, request_topic = ep.topics()
    yield from client.publish(request_topic, json.dumps(
        {"pushing": {"sequence_id": "1", "command": "pushall", "version": 1, "push_target": 1}}).encode())
    yield from client.publish(request_topic, json.dumps({"info": {"sequence_id": "2", "command": "get_version"}}).encode())
    status: dict = {}
    deadline = ep.clock() + wait
    while ep.clock() < deadline and not ("state" in status and "firmware" in status):
        message = yield from client.next_message(max(0.05, deadline - ep.clock()))
        if message is None:
            break
        if message.topic == report_topic:
            report = _report(message)
            if report is not None:
                _absorb(report, status)
    return PrinterStatus(status.get("state"), status.get("firmware"), status.get("gcode_file", ""),
                         status.get("subtask_name", ""))


def test_connection(ep: BambuEndpoint, status_wait: float = PrintOptions.status_wait) -> Iterator:
    """Generator: FTPS login and a listing of the storage root over a TLS data channel, then MQTT login, a subscription to
    the printer's report topic and a ``pushall`` / ``get_version`` request. Returns :class:`ConnectionInfo`. A refused login
    (either protocol) is :class:`~slicewright.core.network.errors.LoginRejected`, a certificate problem a TLS error; both
    stop at once and are never retried. A printer that logs in but does not report is *reported*, not an error."""
    ep.topics()                                     # a bad serial number is the user's to fix before any connection
    found = yield from ftps_check(ep.ftps())
    client = yield from _open_session(ep)
    try:
        status = yield from _request_status(client, ep, status_wait)
    finally:
        client.close()
    heard = status.state is not None or status.firmware is not None
    return ConnectionInfo(PRODUCT, found.data_ok, found.entries, found.data_problem, status.state, status.firmware,
                          heard, ep.serial)


test_connection.__test__ = False


def project_file_command(name: str, options: PrintOptions, sequence_id: int = 3) -> dict:
    """The ``print.project_file`` message for the ``.gcode.3mf`` ``name`` in the storage root (fields as in the
    OpenBambuAPI description and a community protocol reference; ``bed_leveling`` is spelt as that reference spells it).
    ``url`` is ``ftp:///<name>``, the form for a file in the root."""
    stem = name[:-len(".gcode.3mf")] if name.lower().endswith(".gcode.3mf") else name.rsplit(".", 1)[0]
    return {"print": {
        "sequence_id": str(sequence_id), "command": "project_file",
        "param": f"Metadata/plate_{int(options.plate)}.gcode",
        "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
        "subtask_name": stem, "file": "", "url": f"ftp:///{name}", "md5": "",
        "timelapse": bool(options.timelapse), "bed_type": "auto", "bed_leveling": bool(options.bed_leveling),
        "flow_cali": bool(options.flow_cali), "vibration_cali": bool(options.vibration_cali),
        "layer_inspect": bool(options.layer_inspect), "use_ams": bool(options.use_ams),
        "ams_mapping": [int(x) for x in options.ams_mapping] if options.use_ams else []}}


def _ours(status: dict, name: str) -> bool:
    """Whether a report that names a file names ours (one that names none does not contradict it)."""
    stem = name[:-len(".gcode.3mf")] if name.lower().endswith(".gcode.3mf") else name.rsplit(".", 1)[0]
    named = [v for v in (status.get("gcode_file", ""), status.get("subtask_name", "")) if v]
    return not named or any(stem in v or name in v for v in named)


def print_file(ep: BambuEndpoint, name: str, options: PrintOptions = PrintOptions()) -> Iterator:
    """Generator: ask the printer to print the ``.gcode.3mf`` ``name`` that is in its storage root. Returns a
    :class:`PrintOutcome` built **only from the printer's own reports**. Raises :class:`PrinterBusy` (without sending the
    command) when the printer reports that it is printing, preparing or paused, ``LoginRejected`` for a refused login
    (no retry), and the transport and TLS errors. The caller must already have the user's confirmation."""
    if not name.lower().endswith(".3mf"):
        raise InvalidRequest("Bambu printers print .gcode.3mf files")
    report_topic, request_topic = ep.topics()
    client = yield from _open_session(ep)
    try:
        before = yield from _request_status(client, ep, options.status_wait)
        if before.state is None:
            return PrintOutcome(False, False, None, "the printer sent no status, so no command was sent: check the "
                                                    "serial number in its settings")
        if before.state in BUSY_STATES:
            raise PrinterBusy(f"The printer reports that it is busy ({before.state})")
        yield from client.publish(request_topic, json.dumps(project_file_command(name, options)).encode(), qos=1)
        status: dict = {"state": before.state}
        accepted, failed = False, ""
        deadline = ep.clock() + options.confirm_wait
        while ep.clock() < deadline:
            message = yield from client.next_message(max(0.05, min(1.0, deadline - ep.clock())))
            if message is None or message.topic != report_topic:
                continue
            report = _report(message)
            if report is None:
                continue
            pr = report.get("print")
            if isinstance(pr, dict) and pr.get("command") == "project_file":
                if str(pr.get("result", "")).lower() == "success":
                    accepted = True
                else:
                    failed = _text(pr.get("reason"), 80) or "the printer refused the command"
                    break
                continue
            _absorb(report, status)
            if status.get("state") in PRINTING_STATES and _ours(status, name):
                return PrintOutcome(True, True, status["state"])
        if failed:
            return PrintOutcome(False, False, status.get("state"), f"the printer refused the print command: {failed}")
        if accepted:
            return PrintOutcome(True, False, status.get("state"), "the printer accepted the command but has not "
                                                                    "reported that it is printing")
        return PrintOutcome(False, False, status.get("state"), "the printer did not confirm the print command")
    finally:
        client.close()


def _pause(ep: BambuEndpoint, seconds: float) -> Iterator:
    deadline = ep.clock() + seconds
    while ep.clock() < deadline:
        if ep.cancel is not None:
            ep.cancel.check(ep.host, ep.port)
        yield IDLE


def upload(ep: BambuEndpoint, file_path: str, name: str, *, start_print: bool = False,
           options: PrintOptions | None = None, transfer: Transfer | None = None) -> Iterator:
    """Generator: upload ``file_path`` to the printer's storage root as ``name`` over FTPS; returns
    :class:`UploadResult`. With ``start_print=True`` (strict identity: only the caller who has the user's confirmation
    passes it) the printer is then asked to print the file and the result says only what the printer confirmed in a
    report. The file stays on the printer whatever happens to the start; a start that cannot be made (a refused MQTT
    login, a busy printer, silence) is described in ``note``, never raised, except for a cancel, the online-access gate
    and certificate decisions."""
    if start_print is True:
        if not name.lower().endswith(".3mf"):
            raise InvalidRequest("Bambu printers print .gcode.3mf files")
        ep.topics()
    sent = yield from ftps_upload(ep.ftps(), file_path, name, transfer=transfer)
    if start_print is not True:
        return UploadResult(sent.name, sent.path, sent.size)
    options = options or PrintOptions()
    if transfer is not None:
        transfer.phase = "starting"
    yield from _pause(ep, options.settle)
    try:
        outcome = yield from print_file(ep, sent.name, options)
    except NetError as exc:
        if exc.kind in ("cancelled", "online_access", "tls_unknown", "tls_pin_mismatch"):
            raise
        outcome = PrintOutcome(False, False, None, f"the print was not started: {exc}")
    if transfer is not None:
        transfer.phase = "done"
    return UploadResult(sent.name, sent.path, sent.size, outcome.accepted, outcome.printing, outcome.detail)
