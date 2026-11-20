# SPDX-License-Identifier: GPL-3.0-or-later
"""In-process fake servers for the network tests (03 section 9.3). TEST SUPPORT ONLY: not part of the add-on.

``FakeHttpServer`` listens on 127.0.0.1 on an ephemeral port, in a daemon thread (tests may use threads; the add-on may
not), plain or TLS, and records every request. Its behaviour is scripted per route with :class:`Reply`:
slow, dripping, redirecting, oversized, closing early, answering before the body is read, and so on.

TLS certificates are generated per test session with the ``openssl`` command line (a throw-away EC key in a temp
directory, valid a few days): no key material is checked in. Tests that need one skip when ``openssl`` is missing.
"""
from __future__ import annotations

import email
import hashlib
import json
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Reply:
    status: int = 200
    reason: str = "OK"
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    chunked: bool = False                    # send the body with chunked transfer encoding (in 3 pieces)
    delay: float = 0.0                       # sleep before answering
    drip: int = 0                            # send the whole reply this many bytes at a time ...
    drip_interval: float = 0.0               # ... pausing this long between pieces
    raw: bytes | None = None                 # send exactly these bytes instead of building a reply
    close_without_reply: bool = False        # close the connection after the request, saying nothing
    no_content_length: bool = False          # body runs to the end of the connection
    reply_before_body: bool = False          # answer right after the head, do not read the body, close
    abort_after: int | None = None           # close the connection once this many body bytes have arrived (no reply)
    finalize: Callable[["Recorded"], "Reply"] | None = None   # called once the whole body is in: returns the real reply


@dataclass
class Recorded:
    method: str
    target: str
    headers: dict[str, str]                  # lower-cased names
    body: bytes
    body_length_seen: int = 0


class FakeHttpServer:
    def __init__(self, routes: dict | Callable[[Recorded], Reply] | None = None, *, tls_files: tuple[str, str] | None = None,
                 read_delay: float = 0.0, read_chunk: int = 65536):
        """``routes``: ``{(method, path): Reply | callable(Recorded) -> Reply}`` or one callable for everything;
        anything unmatched is a 404. ``read_delay`` makes the server pause that long per ``read_chunk`` of request
        body, so an upload stays in flight."""
        self.routes = routes if routes is not None else {}
        self.requests: list[Recorded] = []
        self.errors: list[str] = []
        self.read_delay = read_delay
        self.read_chunk = read_chunk
        self.connections_closed_by_client = 0
        self._ctx = None
        if tls_files:
            self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self._ctx.load_cert_chain(*tls_files)
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self._sock.settimeout(0.05)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._threads: list[threading.Thread] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def scheme(self) -> str:
        return "https" if self._ctx else "http"

    def url(self, path: str = "/") -> str:
        return f"{self.scheme}://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self._stop = True
        self._thread.join(2)
        for t in self._threads:
            t.join(2)
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    # -- serving -----------------------------------------------------------------------------------------------

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                return
            t = threading.Thread(target=self._handle, args=(conn,), daemon=True)
            self._threads.append(t)
            t.start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(10)
            if self._ctx is not None:
                try:
                    conn = self._ctx.wrap_socket(conn, server_side=True)
                except (ssl.SSLError, OSError) as exc:
                    self.errors.append(f"handshake: {exc.__class__.__name__}")
                    return
            self._exchange(conn)
        except (OSError, ssl.SSLError) as exc:
            self.errors.append(f"{exc.__class__.__name__}")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _exchange(self, conn) -> None:
        buf = b""
        while b"\r\n\r\n" not in buf:
            data = conn.recv(65536)
            if not data:
                return
            buf += data
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, target, _version = lines[0].split(" ", 2)
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        rec = Recorded(method, target, headers, b"")
        self.requests.append(rec)
        reply = self._route(rec)
        if reply.close_without_reply:
            return
        if reply.reply_before_body:
            self._send(conn, reply)
            return
        want = int(headers.get("content-length", "0"))
        body = bytearray(rest)
        rec.body_length_seen = len(body)
        try:
            while len(body) < want:
                if self.read_delay:
                    time.sleep(self.read_delay)
                data = conn.recv(min(self.read_chunk, want - len(body)))
                if not data:
                    self.connections_closed_by_client += 1
                    rec.body = bytes(body)
                    return
                body += data
                rec.body_length_seen = len(body)
                if reply.abort_after is not None and len(body) >= reply.abort_after:
                    return                    # a dropped connection in the middle of an upload
        finally:
            rec.body = bytes(body)
        if reply.finalize is not None:
            reply = reply.finalize(rec)
        if reply.delay:
            time.sleep(reply.delay)
        self._send(conn, reply)

    def _route(self, rec: Recorded) -> Reply:
        route = self.routes
        if callable(route):
            return route(rec)
        path = rec.target.split("?", 1)[0]
        found = route.get((rec.method, path))
        if found is None:
            return Reply(404, "Not Found")
        return found(rec) if callable(found) else found

    def _send(self, conn, reply: Reply) -> None:
        if reply.raw is not None:
            payload = reply.raw
        else:
            lines = [f"HTTP/1.1 {reply.status} {reply.reason}", "Connection: close"]
            lines += [f"{k}: {v}" for k, v in reply.headers.items()]
            body = reply.body
            if reply.chunked:
                lines.append("Transfer-Encoding: chunked")
                third = max(1, len(body) // 3)
                pieces = [body[i:i + third] for i in range(0, len(body), third)] or []
                encoded = b"".join(b"%x\r\n" % len(p) + p + b"\r\n" for p in pieces) + b"0\r\n\r\n"
            else:
                if not reply.no_content_length:
                    lines.append(f"Content-Length: {len(body)}")
                encoded = body
            payload = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + encoded
        if reply.drip:
            for i in range(0, len(payload), reply.drip):
                conn.sendall(payload[i:i + reply.drip])
                time.sleep(reply.drip_interval)
        else:
            conn.sendall(payload)


class Silent:
    """A TCP server that accepts and never says anything (and never reads): for timeouts."""

    def __init__(self):
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self.held: list[socket.socket] = []
        self._stop = False
        self._sock.settimeout(0.05)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop:
            try:
                self.held.append(self._sock.accept()[0])
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                return

    def close(self):
        self._stop = True
        self._thread.join(2)
        for s in self.held:
            s.close()
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def free_port() -> int:
    """A port nothing listens on (bound and closed again): for connection-refused tests."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def have_openssl() -> bool:
    return shutil.which("openssl") is not None


def make_self_signed(directory: Path, name: str = "fake") -> tuple[str, str]:
    """``(certfile, keyfile)``: a throw-away self-signed EC certificate for 127.0.0.1 / localhost, valid 3 days, made
    in ``directory`` with the ``openssl`` command line. TEST ONLY: the key is never kept."""
    cert, key = directory / f"{name}.crt", directory / f"{name}.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "3", "-subj", f"/CN=slicewright-test-{name}",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        check=True, capture_output=True)
    return str(cert), str(key)


# -- fake printers -----------------------------------------------------------------------------------------------

@dataclass
class Upload:
    """One multipart upload as the fake printer received it."""
    fields: dict[str, str]
    filename: str
    content: bytes
    path: str                                 # where the request went (without the proxy prefix)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def parse_multipart(headers: dict[str, str], body: bytes) -> tuple[dict[str, str], list[tuple[str, str, bytes]]]:
    """``(text fields, [(field name, file name, bytes)])`` of a ``multipart/form-data`` request body."""
    raw = b"Content-Type: " + headers["content-type"].encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    msg = email.message_from_bytes(raw)              # compat32: headers stay raw text, so ``filename*`` is not dropped
    fields: dict[str, str] = {}
    files: list[tuple[str, str, bytes]] = []
    for part in msg.get_payload():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_param("filename", header="content-disposition")
        extended = re.search(r"filename\*=UTF-8''([^;]+)", str(part["content-disposition"]))
        if extended:                                                  # RFC 5987: wins over the ASCII fallback
            filename = urllib.parse.unquote(extended.group(1))
        payload = part.get_payload(decode=True) or b""
        if filename is not None:
            files.append((name, filename, payload))
        else:
            fields[name] = payload.decode("utf-8")
    return fields, files


class _FakePrinter(FakeHttpServer):
    """Common ground of the fake OctoPrint and Moonraker: an API key check, a reverse-proxy prefix, upload recording and
    the scripted failures (``upload_status``, ``abort_after``, ``read_delay``, ``echo_secret``)."""

    def __init__(self, api_key: str | None, *, prefix: str = "", tls_files=None, read_delay: float = 0.0,
                 read_chunk: int = 65536):
        self.api_key = api_key                # None: the printer asks for no key
        self.prefix = prefix.rstrip("/")
        self.auth_status = 403
        self.upload_status: int | None = None      # answer every upload with this status (after reading the body)
        self.upload_early_status: int | None = None   # ... or before reading it (413)
        self.abort_after: int | None = None        # drop the connection after this many body bytes
        self.echo_secret = False              # error bodies repeat the key they were sent (a hostile or sloppy server)
        self.uploads: list[Upload] = []
        super().__init__(self._dispatch, tls_files=tls_files, read_delay=read_delay, read_chunk=read_chunk)

    # -- helpers -----------------------------------------------------------------------------------------------

    def key_ok(self, rec: Recorded) -> bool:
        return self.api_key is None or rec.headers.get("x-api-key") == self.api_key

    def error(self, status: int, reason: str, rec: Recorded, **kw) -> Reply:
        body = {"error": reason}
        if self.echo_secret:
            body["you_sent"] = rec.headers.get("x-api-key", "")
        return Reply(status, reason, headers={"Content-Type": "application/json"}, body=json.dumps(body).encode(), **kw)

    @staticmethod
    def json_reply(data, status: int = 201, reason: str = "Created") -> Reply:
        return Reply(status, reason, headers={"Content-Type": "application/json"}, body=json.dumps(data).encode())

    def _dispatch(self, rec: Recorded) -> Reply:
        path = rec.target.split("?", 1)[0]
        if self.prefix:
            if not path.startswith(self.prefix + "/"):
                return Reply(404, "Not Found")
            path = path[len(self.prefix):]
        return self.route(rec, path)

    def route(self, rec: Recorded, path: str) -> Reply:           # pragma: no cover - overridden
        return Reply(404, "Not Found")

    def receive_upload(self, rec: Recorded, path: str, make_reply: Callable[[Upload], Reply]) -> Reply:
        """The reply for an upload: scripted failures first, then (once the body is in) record and answer it."""
        if not self.key_ok(rec):
            return self.error(self.auth_status, "Forbidden", rec, reply_before_body=True)
        if self.upload_early_status is not None:
            return self.error(self.upload_early_status, "Request Entity Too Large", rec, reply_before_body=True)

        def finish(done: Recorded) -> Reply:
            try:
                fields, files = parse_multipart(done.headers, done.body)
            except Exception:  # noqa: BLE001
                return self.error(400, "Bad Request", done)
            if len(files) != 1 or files[0][0] != "file":
                return self.error(400, "Bad Request", done)
            upload = Upload(fields, files[0][1], files[0][2], path)
            if self.upload_status is not None:
                return self.error(self.upload_status, "Scripted Failure", done)
            reply = make_reply(upload)
            if reply.status < 300:
                self.uploads.append(upload)
            return reply
        return Reply(200, "OK", abort_after=self.abort_after, finalize=finish)


class FakeOctoPrint(_FakePrinter):
    """The OctoPrint endpoints the add-on uses: ``GET /api/version``, ``GET /api/connection`` and
    ``POST /api/files/local``. ``busy`` makes a request to select or print answer 409, as a printing OctoPrint does;
    ``connection_status`` scripts the status of ``/api/connection`` (a key without that permission gets 403)."""

    def __init__(self, api_key: str | None = "fake-octoprint-key-0123", **kw):
        self.server_version = "1.10.2"
        self.api_version = "0.1"
        self.state = "Operational"
        self.connection_status = 200
        self.busy = False
        self.not_octoprint = False            # answer the version call with something else (a web UI, say)
        self.printed: str | None = None       # the file a print was last started for
        super().__init__(api_key, **kw)

    def route(self, rec: Recorded, path: str) -> Reply:
        if self.not_octoprint and path == "/api/version":
            return Reply(200, "OK", headers={"Content-Type": "text/html"}, body=b"<html>router login</html>")
        if (rec.method, path) == ("GET", "/api/version"):
            if not self.key_ok(rec):
                return self.error(self.auth_status, "Forbidden", rec)
            return self.json_reply({"api": self.api_version, "server": self.server_version,
                                    "text": f"OctoPrint {self.server_version}"}, 200, "OK")
        if (rec.method, path) == ("GET", "/api/connection"):
            if not self.key_ok(rec) or self.connection_status == 403:
                return self.error(403, "Forbidden", rec)
            if self.connection_status != 200:
                return self.error(self.connection_status, "Scripted Failure", rec)
            return self.json_reply({"current": {"state": self.state, "port": "/dev/ttyACM0", "baudrate": 115200},
                                    "options": {}}, 200, "OK")
        if (rec.method, path) == ("POST", "/api/files/local"):
            return self.receive_upload(rec, path, self._stored)
        return Reply(404, "Not Found")

    def _stored(self, up: Upload) -> Reply:
        want_print = up.fields.get("print") == "true"
        want_select = up.fields.get("select") == "true" or want_print
        if (want_print or want_select) and self.busy:
            return self.error(409, "Conflict", Recorded("POST", up.path, {}, b""))
        folder = up.fields.get("path", "").strip("/")
        stored_path = f"{folder}/{up.filename}" if folder else up.filename
        if want_print:
            self.printed = stored_path
        return self.json_reply({
            "files": {"local": {"name": up.filename, "path": stored_path, "origin": "local"}},
            "done": True, "effectiveSelect": want_select, "effectivePrint": want_print})


class FakeMoonraker(_FakePrinter):
    """The Moonraker endpoints the add-on uses: ``GET /server/info``, ``GET /printer/info`` and
    ``POST /server/files/upload`` (fields ``root``, ``path``, ``print``). ``api_key=None`` is a printer that asks for no
    key; with a key, a request without or with a wrong one gets 401. ``klippy_state`` is what Klipper reports (``/printer/info``
    answers 503 unless it is ``ready``, as Moonraker does); ``printing_file`` is the file being printed, which cannot be
    replaced (403). The answers follow Moonraker's public API documentation; they have not been compared with a real
    Moonraker."""

    def __init__(self, api_key: str | None = None, **kw):
        self.auth_status = 401
        self.moonraker_version = "v0.9.3-24-g1b2c3d4"
        self.klippy_state = "ready"
        self.state_message = "Printer is ready"
        self.printing_file: str | None = None
        self.not_moonraker = False
        self.wrap_in_result = False            # answer uploads inside a "result" object (an older shape)
        self.print_requested = False
        super().__init__(api_key, **kw)
        self.auth_status = 401

    def route(self, rec: Recorded, path: str) -> Reply:
        if self.not_moonraker and path == "/server/info":
            return Reply(200, "OK", headers={"Content-Type": "text/html"}, body=b"<html>nginx welcome</html>")
        if (rec.method, path) == ("GET", "/server/info"):
            if not self.key_ok(rec):
                return self.error(self.auth_status, "Unauthorized", rec)
            return self.json_reply({"result": {
                "klippy_connected": self.klippy_state == "ready", "klippy_state": self.klippy_state,
                "moonraker_version": self.moonraker_version, "api_version": [1, 0, 5], "api_version_string": "1.0.5",
                "components": ["file_manager"], "failed_components": [], "warnings": []}}, 200, "OK")
        if (rec.method, path) == ("GET", "/printer/info"):
            if not self.key_ok(rec):
                return self.error(self.auth_status, "Unauthorized", rec)
            if self.klippy_state != "ready":
                return self.json_reply({"error": {"code": 503, "message": "Klippy Host not connected"}}, 503,
                                       "Service Unavailable")
            return self.json_reply({"result": {"state": "ready", "state_message": self.state_message,
                                               "hostname": "voron", "software_version": "v0.12.0"}}, 200, "OK")
        if (rec.method, path) == ("POST", "/server/files/upload"):
            return self.receive_upload(rec, path, self._stored)
        return Reply(404, "Not Found")

    def _stored(self, up: Upload) -> Reply:
        folder = up.fields.get("path", "").strip("/")
        stored_path = f"{folder}/{up.filename}" if folder else up.filename
        if up.fields.get("root", "gcodes") != "gcodes":
            return self.error(400, "Bad Request", Recorded("POST", up.path, {}, b""))
        if self.printing_file == stored_path:
            return self.error(403, "Forbidden", Recorded("POST", up.path, {}, b""))
        started = up.fields.get("print") == "true" and self.klippy_state == "ready" and self.printing_file is None
        if started:
            self.printing_file = stored_path
        self.print_requested = up.fields.get("print") == "true"
        answer = {"item": {"path": stored_path, "root": "gcodes", "modified": 1_700_000_000.0,
                           "size": len(up.content), "permissions": "rw"},
                  "print_queued": False, "print_started": started, "action": "create_file"}
        return self.json_reply({"result": answer} if self.wrap_in_result else answer)


# -- fake FTPS server (Bambu LAN storage) ---------------------------------------------------------------------------

@dataclass
class FtpStored:
    """One file the fake FTPS server received on a data connection."""
    name: str
    content: bytes
    clean_end: bool                           # the data connection ended with a TLS close_notify (the end-of-file mark)
    session_reused: bool

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class FakeFtpsServer:
    """An implicit-TLS FTP server on 127.0.0.1 (an ephemeral port), in daemon threads: ``USER``/``PASS``, ``PBSZ``,
    ``PROT``, ``TYPE``, ``PASV`` (a TLS data channel on its own ephemeral port), ``STOR``, ``LIST``, ``NOOP``, ``QUIT``.
    It follows RFC 959 and 4217 and the public description of Bambu's LAN service; it has not been compared with a real
    printer.

    Recorded: ``commands`` (every control line, the ``PASS`` argument replaced by ``***``), ``logins`` (``(user, accepted)``),
    ``raw_received`` (every byte read from any client on the control channel: what an imposter learned), ``stored`` (a
    :class:`FtpStored` per data connection that ended), ``aborted_transfers`` (data connections that ended without a TLS
    close_notify), ``connections_closed_by_client`` (control connections that ended without ``QUIT``, plus aborted
    transfers) and ``errors``.

    Scripted behaviour: ``require_session_reuse`` (refuse a data channel that did not resume the control session, as
    vsftpd's ``require_ssl_reuse`` does), ``refuse_prot_p`` (534 to ``PROT P``), ``bad_pasv`` (a 227 with no address),
    ``pasv_ip`` (the address in the 227 reply), ``store_status`` (answer ``STOR`` with that code instead of ``150``),
    ``final_status`` (answer the end of the transfer with that code instead of ``226``), ``drop_after`` (close the data
    connection after that many bytes and answer ``drop_reply`` on the control connection), ``read_delay`` (pause per
    ``read_chunk`` of data, until ``slow_bytes`` have arrived when that is set), ``greeting`` (the lines of the ``220`` reply), ``list_names`` and ``reject_login`` (always
    ``530``)."""

    def __init__(self, password: str = "12345678", *, user: str = "bblp", tls_files: tuple[str, str],
                 require_session_reuse: bool = False, read_delay: float = 0.0, read_chunk: int = 16384,
                 slow_bytes: int | None = None):
        self.user, self.password = user, password
        self.slow_bytes = slow_bytes              # with ``read_delay``: pause only until this many bytes have arrived
        self.require_session_reuse = require_session_reuse
        self.refuse_prot_p = False
        self.bad_pasv = False
        self.pasv_ip = "127.0.0.1"
        self.store_status: int | None = None
        self.final_status: int | None = None
        self.drop_after: int | None = None
        self.drop_reply = "426 Connection closed; transfer aborted"
        self.read_delay, self.read_chunk = read_delay, read_chunk
        self.greeting = ["Fake FTPS ready"]
        self.list_names = ["cache", "model.gcode.3mf"]
        self.reject_login = False
        self.commands: list[str] = []
        self.logins: list[tuple[str, bool]] = []
        self.raw_received = bytearray()
        self.stored: list[FtpStored] = []
        self.aborted_transfers = 0
        self.connections_closed_by_client = 0
        self.errors: list[str] = []
        self.prot_levels: list[str] = []
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(*tls_files)
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self._sock.settimeout(0.05)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._threads: list[threading.Thread] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop = True
        self._thread.join(2)
        for t in list(self._threads):
            t.join(2)
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    # -- serving -----------------------------------------------------------------------------------------------

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                return
            t = threading.Thread(target=self._control, args=(conn,), daemon=True)
            self._threads.append(t)
            t.start()

    def _reply(self, conn, code: int, text: str = "") -> None:
        conn.sendall(f"{code} {text}\r\n".encode("latin-1"))

    def _control(self, raw: socket.socket) -> None:
        raw.settimeout(10)
        try:
            conn = self._ctx.wrap_socket(raw, server_side=True)           # implicit TLS: the handshake comes first
        except (ssl.SSLError, OSError) as exc:
            self.errors.append(f"handshake: {exc.__class__.__name__}")
            raw.close()
            return
        quit_seen = False
        passive: socket.socket | None = None
        state = {"user": None, "logged_in": False, "prot": "C"}
        buf = b""
        try:
            lines = self.greeting
            for i, line in enumerate(lines):
                conn.sendall(f"220{'-' if i < len(lines) - 1 else ' '}{line}\r\n".encode("latin-1"))
            while True:
                while b"\n" not in buf:
                    data = conn.recv(4096)
                    if not data:
                        return
                    self.raw_received += data
                    buf += data
                line, _, buf = buf.partition(b"\n")
                text = line.rstrip(b"\r").decode("latin-1")
                verb, _, arg = text.partition(" ")
                verb = verb.upper()
                self.commands.append(f"{verb} ***" if verb == "PASS" else text)
                if verb == "QUIT":
                    quit_seen = True
                    self._reply(conn, 221, "Goodbye")
                    return
                passive = self._command(conn, verb, arg, state, passive)
        except (OSError, ssl.SSLError) as exc:
            self.errors.append(f"control: {exc.__class__.__name__}")
        finally:
            if not quit_seen:
                self.connections_closed_by_client += 1
            if passive is not None:
                passive.close()
            try:
                conn.close()
            except OSError:
                pass

    def _command(self, conn, verb: str, arg: str, state: dict, passive):
        if verb == "USER":
            state["user"] = arg
            self._reply(conn, 331, "Password required")
        elif verb == "PASS":
            ok = not self.reject_login and state["user"] == self.user and arg == self.password
            self.logins.append((state["user"] or "", ok))
            state["logged_in"] = ok
            self._reply(conn, 230 if ok else 530, "Login successful" if ok else "Login incorrect")
        elif not state["logged_in"]:
            self._reply(conn, 530, "Please login with USER and PASS")
        elif verb == "PBSZ":
            self._reply(conn, 200, "PBSZ set to 0")
        elif verb == "PROT":
            if self.refuse_prot_p and arg.upper() == "P":
                self._reply(conn, 534, "Request denied for policy reasons")
            else:
                state["prot"] = arg.upper()
                self.prot_levels.append(state["prot"])
                self._reply(conn, 200, f"PROT now {arg.upper()}")
        elif verb == "TYPE":
            self._reply(conn, 200, "Switching to Binary mode")
        elif verb == "NOOP":
            self._reply(conn, 200, "NOOP ok")
        elif verb == "PASV":
            if passive is not None:
                passive.close()
            if self.bad_pasv:
                self._reply(conn, 227, "Entering Passive Mode (garbled)")
                return None
            passive = socket.socket()
            passive.bind(("127.0.0.1", 0))
            passive.listen(1)
            passive.settimeout(5)
            port = passive.getsockname()[1]
            self._reply(conn, 227, f"Entering Passive Mode ({self.pasv_ip.replace('.', ',')},{port >> 8},{port & 255})")
            return passive
        elif verb in ("STOR", "LIST"):
            self._transfer(conn, verb, arg, state, passive)
            return None
        else:
            self._reply(conn, 502, "Command not implemented")
        return passive

    def _transfer(self, conn, verb: str, arg: str, state: dict, passive) -> None:
        if passive is None:
            self._reply(conn, 425, "Use PASV first")
            return
        if state["prot"] != "P":
            self._reply(conn, 521, "Data connections must be encrypted")
            passive.close()
            return
        if verb == "STOR" and self.store_status is not None:
            self._reply(conn, self.store_status, "Scripted failure")
            passive.close()
            return
        self._reply(conn, 150, "Ok to send data")
        try:
            raw, _ = passive.accept()
        except OSError:
            self.errors.append("data: no connection")
            return
        finally:
            passive.close()
        raw.settimeout(10)
        try:
            data = self._ctx.wrap_socket(raw, server_side=True, suppress_ragged_eofs=False)
        except (ssl.SSLError, OSError) as exc:
            self.errors.append(f"data handshake: {exc.__class__.__name__}")
            raw.close()
            self._reply(conn, 425, "Cannot open the data connection")
            return
        reused = bool(data.session_reused)
        if self.require_session_reuse and not reused:
            data.close()
            self._reply(conn, 425, "TLS session of the data connection must be reused")
            return
        if verb == "LIST":
            try:
                data.sendall("".join(f"-rw-r--r-- 1 u g 1 Jan 1 00:00 {n}\r\n" for n in self.list_names).encode())
                data.unwrap()
            except (OSError, ssl.SSLError):
                pass
            data.close()
            self._reply(conn, 226, "Directory send OK")
            return
        content, clean = bytearray(), True
        try:
            while True:
                if self.read_delay and (self.slow_bytes is None or len(content) < self.slow_bytes):
                    time.sleep(self.read_delay)
                chunk = data.recv(self.read_chunk)
                if not chunk:
                    break
                content += chunk
                if self.drop_after is not None and len(content) >= self.drop_after:
                    data.close()
                    self.aborted_transfers += 1
                    self._reply(conn, int(self.drop_reply[:3]), self.drop_reply[4:])
                    return
        except (ssl.SSLEOFError, ConnectionResetError, OSError, ssl.SSLError):
            clean = False                                   # no close_notify: the client aborted
        try:
            data.close()
        except OSError:
            pass
        if not clean:
            self.aborted_transfers += 1
            self.connections_closed_by_client += 1
        self.stored.append(FtpStored(arg, bytes(content), clean, reused))
        if clean:
            code = self.final_status or 226
            self._reply(conn, code, "Transfer complete" if code == 226 else "Scripted failure")
        else:
            self._reply(conn, 426, "Transfer aborted")
