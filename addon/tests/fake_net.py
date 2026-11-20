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
        self.printed = stored_path if want_print else getattr(self, "printed", None)
        return self.json_reply({
            "files": {"local": {"name": up.filename, "path": stored_path, "origin": "local"}},
            "done": True, "effectiveSelect": want_select, "effectivePrint": want_print})
