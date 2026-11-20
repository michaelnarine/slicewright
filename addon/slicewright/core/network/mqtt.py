# SPDX-License-Identifier: GPL-3.0-or-later
"""A minimal MQTT 3.1.1 client over TLS as non-blocking generators (03 section 8.4). No ``bpy``, no threads, no third-party code.

Written from the OASIS MQTT 3.1.1 specification. It implements exactly what the Bambu LAN client needs: ``CONNECT`` (user
name, password, clean session, keep alive), ``CONNACK``, ``SUBSCRIBE``/``SUBACK``, ``PUBLISH`` at QoS 0 and 1 (``PUBACK``),
incoming ``PUBLISH`` at QoS 0 and 1, ``PINGREQ``/``PINGRESP`` and ``DISCONNECT``. QoS 2, retained-message handling, wills,
sessions and reconnecting are not implemented: an incoming QoS 2 message is a protocol error.

Like the other clients here, every operation is a generator whose ``yield`` is one bounded step (``nbsocket``), and the TLS
policy (a pin, for a self-signed printer) is checked in the handshake, **before the first byte** (the ``CONNECT`` with the
password) is sent. A refused ``CONNECT`` (return code 4 or 5) is :class:`LoginRejected` and is never retried.

The password is the access code as a :class:`~slicewright.core.redact.SecretStr`; ``reveal()`` is called once, to build the
``CONNECT`` packet, into a local that is dropped at once. The client id is our own random one (``slicewright-<hex>``),
never another program's. Error messages hold the target and a reason word, never a topic payload or a credential.
"""
from __future__ import annotations

import collections
import os
import struct
import time
from dataclasses import dataclass
from typing import Iterator

from ..redact import SecretStr
from ..ticking import IDLE
from .errors import (ConnectionLost, InvalidRequest, LimitExceeded, LoginRejected, NetError, ProtocolError,
                     Timeout, target)
from .nbsocket import CancelToken, Clock, Connection, Timeouts, connect
from .resolve import Resolver, default_resolver
from .tls import TlsPolicy

CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 1, 2, 3, 4, 8, 9, 12, 13, 14
MAX_PACKET = 4 * 1024 * 1024           # a full Bambu status report is tens of kilobytes
MAX_INBOX = 256
KEEP_ALIVE = 30
CONNACK_MESSAGES = {1: "does not speak MQTT 3.1.1", 2: "rejected the client identifier", 3: "is not accepting MQTT clients",
                    4: "refused the user name or password", 5: "refused the login"}


@dataclass(frozen=True)
class MqttEndpoint:
    """Where and how to talk to one broker. ``tls`` is required (there is no plain MQTT here)."""
    host: str
    tls: TlsPolicy
    port: int = 8883
    username: str = ""
    password: SecretStr | None = None
    product: str = "The printer"
    login_hint: str = ""
    timeouts: Timeouts = Timeouts(connect=5.0, idle=15.0)
    resolver: Resolver = default_resolver
    clock: Clock = time.monotonic
    cancel: CancelToken | None = None

    def __repr__(self) -> str:
        return f"MqttEndpoint({target(self.host, self.port)}, username={self.username!r}, password=***)"


@dataclass(frozen=True)
class Message:
    topic: str
    payload: bytes
    qos: int = 0


# -- packet encoding ----------------------------------------------------------------------------------------------

def encode_length(n: int) -> bytes:
    """The MQTT variable-length "remaining length" (1 to 4 bytes)."""
    if not 0 <= n <= 268_435_455:
        raise ValueError("remaining length out of range")
    out = bytearray()
    while True:
        digit, n = n % 128, n // 128
        out.append(digit | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _string(text: str) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > 65535:
        raise InvalidRequest("An MQTT string is too long")
    return struct.pack(">H", len(raw)) + raw


def packet(kind: int, flags: int, body: bytes) -> bytes:
    return bytes([(kind << 4) | flags]) + encode_length(len(body)) + body


def connect_packet(client_id: str, username: str, password: bytes, keep_alive: int = KEEP_ALIVE) -> bytes:
    flags = 0x02 | (0x80 if username else 0) | (0x40 if username and password else 0)      # clean session
    body = _string("MQTT") + bytes([4, flags]) + struct.pack(">H", keep_alive) + _string(client_id)
    if username:
        body += _string(username)
        if password:
            body += struct.pack(">H", len(password)) + password
    return packet(CONNECT, 0, body)


def subscribe_packet(packet_id: int, topic: str, qos: int = 0) -> bytes:
    return packet(SUBSCRIBE, 0x02, struct.pack(">H", packet_id) + _string(topic) + bytes([qos]))


def publish_packet(topic: str, payload: bytes, qos: int = 0, packet_id: int = 0) -> bytes:
    if qos not in (0, 1):
        raise InvalidRequest("Only QoS 0 and 1 are supported")
    if "#" in topic or "+" in topic or not topic:
        raise InvalidRequest("A publish topic has no wildcards")
    body = _string(topic) + (struct.pack(">H", packet_id) if qos else b"") + payload
    return packet(PUBLISH, qos << 1, body)


class _Parser:
    """Splits a byte stream into packets ``(kind, flags, body)``."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> None:
        self.buf += data

    def next(self, where: str) -> tuple[int, int, bytes] | None:
        buf = self.buf
        if len(buf) < 2:
            return None
        length, mult, i = 0, 1, 1
        while True:
            if i >= len(buf):
                return None
            digit = buf[i]
            length += (digit & 0x7F) * mult
            i += 1
            if not digit & 0x80:
                break
            mult *= 128
            if mult > 128 ** 3:
                raise ProtocolError(f"{where} sent a malformed MQTT packet length")
        if length > MAX_PACKET:
            raise LimitExceeded(f"{where} sent an MQTT packet that is too large")
        if len(buf) < i + length:
            return None
        first = buf[0]
        body = bytes(buf[i:i + length])
        del buf[:i + length]
        return first >> 4, first & 0x0F, body


class MqttClient:
    """An open MQTT session. Build it with :func:`open_client`; close it with :meth:`close`."""

    def __init__(self, conn: Connection, ep: MqttEndpoint) -> None:
        self.conn, self.ep = conn, ep
        self.where = target(conn.host, conn.port)
        self.clock = ep.clock
        self.cancel = ep.cancel or CancelToken()
        self.parser = _Parser()
        self.inbox: collections.deque[Message] = collections.deque()
        self.out = bytearray()
        self.connack: int | None = None
        self.subacks: dict[int, int] = {}
        self.acked: set[int] = set()
        self._next_id = 0
        self._last_sent = ep.clock()
        self.closed = False

    # -- plumbing ---------------------------------------------------------------------------------------------

    def _packet_id(self) -> int:
        self._next_id = self._next_id % 65535 + 1
        return self._next_id

    def _flush(self) -> bool:
        """Push queued bytes to the socket; True if something moved."""
        moved = False
        while self.out:
            n = self.conn.send(memoryview(bytes(self.out[:65536])))
            if not n:
                break
            del self.out[:n]
            self._last_sent = self.clock()
            moved = True
        return moved

    def _handle(self, kind: int, flags: int, body: bytes) -> None:
        """Act on one incoming packet."""
        if kind == CONNACK:
            self.connack = body[1] if len(body) >= 2 else 255
        elif kind == SUBACK and len(body) >= 3:
            self.subacks[struct.unpack(">H", body[:2])[0]] = body[2]
        elif kind == PUBACK and len(body) >= 2:
            self.acked.add(struct.unpack(">H", body[:2])[0])
        elif kind == PINGREQ:
            self.out += packet(PINGRESP, 0, b"")
        elif kind == PUBLISH:
            qos = (flags >> 1) & 3
            if qos == 2:
                raise ProtocolError(f"{self.where} sent a QoS 2 message, which this client does not support",
                                    host=self.conn.host, port=self.conn.port)
            bad = ProtocolError(f"{self.where} sent a malformed MQTT message", host=self.conn.host, port=self.conn.port)
            if len(body) < 2:
                raise bad
            tlen = struct.unpack(">H", body[:2])[0]
            if len(body) < 2 + tlen:
                raise bad
            topic, rest = body[2:2 + tlen].decode("utf-8", "replace"), body[2 + tlen:]
            if qos == 1:
                if len(rest) < 2:
                    raise bad
                self.out += packet(PUBACK, 0, rest[:2])
                rest = rest[2:]
            if len(self.inbox) >= MAX_INBOX:
                self.inbox.popleft()                      # a flood: keep the newest
            self.inbox.append(Message(topic, rest, qos))

    def step(self, idle: float, last_move: list[float]) -> Iterator:
        """One service round (a generator that yields once): flush what is queued, send a keep-alive when due, read and
        handle at most one packet. ``last_move[0]`` is the time something last moved; ``idle`` seconds without
        movement is a :class:`Timeout`. Yields ``IDLE`` when nothing moved."""
        self.cancel.check(self.conn.host, self.conn.port)
        moved = self._flush()
        if not self.out and self.clock() - self._last_sent > KEEP_ALIVE / 2:
            self.out += packet(PINGREQ, 0, b"")
        item = self.parser.next(self.where)
        if item is None:
            data = self.conn.recv()
            if data == b"":
                raise ConnectionLost(f"{self.where} closed the MQTT connection", host=self.conn.host,
                                     port=self.conn.port)
            if data:
                self.parser.feed(data)
                moved = True
                item = self.parser.next(self.where)
        if item is not None:
            self._handle(*item)
            moved = True
        if moved:
            last_move[0] = self.clock()
        elif self.clock() - last_move[0] > idle:
            raise Timeout(f"{self.where} did not respond for {idle:g} s", phase="idle", host=self.conn.host,
                          port=self.conn.port)
        yield None if moved else IDLE

    # -- operations -------------------------------------------------------------------------------------------

    def subscribe(self, topic: str, qos: int = 0) -> Iterator:
        """Subscribe and wait for the ``SUBACK``; a refusal (return code 0x80) is a :class:`ProtocolError`."""
        pid = self._packet_id()
        self.out += subscribe_packet(pid, topic, qos)
        last = [self.clock()]
        while pid not in self.subacks:
            yield from self.step(self.ep.timeouts.idle, last)
        if self.subacks.pop(pid) == 0x80:
            raise ProtocolError(f"{self.where} refused the subscription", host=self.conn.host, port=self.conn.port)

    def publish(self, topic: str, payload: bytes, qos: int = 0) -> Iterator:
        """Publish ``payload``; at QoS 1 wait for the broker's ``PUBACK`` (the broker has it: that says nothing about the
        device that reads it)."""
        pid = self._packet_id() if qos else 0
        self.out += publish_packet(topic, payload, qos, pid)
        last = [self.clock()]
        while self.out or (qos and pid not in self.acked):
            yield from self.step(self.ep.timeouts.idle, last)
        self.acked.discard(pid)

    def next_message(self, timeout: float) -> Iterator:
        """The next incoming :class:`Message`, or ``None`` after ``timeout`` seconds with nothing."""
        deadline = self.clock() + timeout
        last = [self.clock()]
        while not self.inbox:
            if self.clock() >= deadline:
                return None
            yield from self.step(timeout + self.ep.timeouts.idle, last)
        return self.inbox.popleft()

    def close(self) -> None:
        """Best-effort ``DISCONNECT`` (one send attempt), then close. Never raises."""
        if self.closed:
            return
        self.closed = True
        try:
            self.conn.send(packet(DISCONNECT, 0, b""))
        except NetError:
            pass
        self.conn.close()


def open_client(ep: MqttEndpoint) -> Iterator:
    """Generator: connect (the pin is checked in the handshake), send ``CONNECT`` and wait for ``CONNACK``. Returns an
    :class:`MqttClient`. A refused login is :class:`LoginRejected` (one attempt, no retry)."""
    if not ep.username or ep.password is None or not ep.password.reveal():
        raise InvalidRequest("Set the access code of this printer before connecting")
    conn = yield from connect(ep.host, ep.port, tls=ep.tls, timeouts=ep.timeouts, cancel=ep.cancel, clock=ep.clock,
                              resolver=ep.resolver)
    client = MqttClient(conn, ep)
    try:
        secret = ep.password.reveal().encode("utf-8")               # the one place the secret is bytes
        try:
            client.out += connect_packet(f"slicewright-{os.urandom(4).hex()}", ep.username, secret)
        finally:
            secret = b""
        last = [ep.clock()]
        while client.connack is None:
            yield from client.step(ep.timeouts.idle, last)
        code = client.connack
        if code in (4, 5):
            raise LoginRejected(f"{client.where} {CONNACK_MESSAGES[code]} (MQTT {code}). Check the access code"
                                f"{ep.login_hint}", host=ep.host, port=ep.port)
        if code != 0:
            raise ProtocolError(f"{client.where} {CONNACK_MESSAGES.get(code, 'refused the connection')} "
                                f"(MQTT {code})", host=ep.host, port=ep.port)
    except BaseException:
        conn.close()
        raise
    return client
