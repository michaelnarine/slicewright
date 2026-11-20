# SPDX-License-Identifier: GPL-3.0-or-later
"""Request bodies: ``multipart/form-data`` streamed from a file in chunks, and plain bytes. No ``bpy``.

A body knows its content type and exact length up front (so the request carries ``Content-Length`` and no chunked
encoding, which not every device accepts) and yields its bytes lazily from :meth:`chunks`; a file is read one chunk at a
time, never whole. :meth:`chunks` can be called again, which is how a ``307``/``308`` redirect replays an upload.
"""
from __future__ import annotations

import errno
import os
import re
import secrets
from typing import Iterator, Sequence
from urllib.parse import quote

from .errors import InvalidRequest, UploadError

CHUNK_SIZE = 256 * 1024                 # 03 section 8.6: streaming multipart in 256 KB chunks
_CTL = re.compile(r"[\x00-\x1f\x7f]")


class BytesBody:
    """A small body held in memory (a JSON document)."""

    def __init__(self, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._data = bytes(data)
        self.content_type = content_type
        self.length = len(self._data)

    def chunks(self) -> Iterator[bytes]:
        if self._data:
            yield self._data


def _check_token(value: str, what: str) -> str:
    if _CTL.search(value):
        raise InvalidRequest(f"The {what} contains a control character")
    return value


def _quote_param(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "%22")


def content_disposition(name: str, filename: str | None = None) -> str:
    """``form-data; name="..."[; filename="..."][; filename*=UTF-8''...]``: quotes escaped, no control characters, a
    non-ASCII file name given twice (ASCII fallback plus RFC 5987)."""
    out = f'form-data; name="{_quote_param(_check_token(name, "field name"))}"'
    if filename is not None:
        _check_token(filename, "file name")
        fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
        out += f'; filename="{_quote_param(fallback)}"'
        if fallback != filename:
            out += f"; filename*=UTF-8''{quote(filename, safe='')}"
    return out


class MultipartBody:
    """Text ``fields`` followed by one file part streamed from ``file_path``."""

    def __init__(self, fields: Sequence[tuple[str, str]], *, file_field: str, file_path: str, filename: str,
                 content_type: str = "application/octet-stream", chunk_size: int = CHUNK_SIZE,
                 boundary: str | None = None) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        self.chunk_size = chunk_size
        self.file_path = file_path
        self.boundary = boundary or f"slicewright{secrets.token_hex(16)}"
        _check_token(content_type, "content type")
        try:
            self.file_size = os.path.getsize(file_path)
        except OSError as exc:
            raise UploadError(f"Cannot read {os.path.basename(file_path)!r} to send it "
                              f"({errno.errorcode.get(exc.errno, 'error')})") from None
        b = self.boundary.encode("ascii")
        head = bytearray()
        for name, value in fields:
            head += b"--" + b + b"\r\nContent-Disposition: " + content_disposition(name).encode("ascii") + b"\r\n\r\n"
            head += _check_text(value).encode("utf-8") + b"\r\n"
        head += (b"--" + b + b"\r\nContent-Disposition: " + content_disposition(file_field, filename).encode("ascii")
                 + b"\r\nContent-Type: " + content_type.encode("ascii") + b"\r\n\r\n")
        self._head = bytes(head)
        self._tail = b"\r\n--" + b + b"--\r\n"
        self.content_type = f"multipart/form-data; boundary={self.boundary}"
        self.length = len(self._head) + self.file_size + len(self._tail)

    def chunks(self) -> Iterator[bytes]:
        """The body, from the start: the text parts, the file in ``chunk_size`` pieces, the closing boundary. Raises
        ``UploadError`` if the file cannot be read or is shorter than when the body was built; a file that grew is
        cut at its original size so ``Content-Length`` stays true."""
        yield self._head
        remaining = self.file_size
        try:
            f = open(self.file_path, "rb")
        except OSError as exc:
            raise UploadError(f"Cannot read {os.path.basename(self.file_path)!r} to send it "
                              f"({errno.errorcode.get(exc.errno, 'error')})") from None
        with f:
            while remaining:
                try:
                    data = f.read(min(self.chunk_size, remaining))
                except OSError as exc:
                    raise UploadError(f"Reading {os.path.basename(self.file_path)!r} failed "
                                      f"({errno.errorcode.get(exc.errno, 'error')})") from None
                if not data:
                    raise UploadError(f"{os.path.basename(self.file_path)!r} changed while it was being sent "
                                      f"(it became shorter)")
                remaining -= len(data)
                yield data
        yield self._tail


def _check_text(value: str) -> str:
    """A text field's value: anything but the boundary can be sent, but a NUL would end it early on some servers."""
    if "\x00" in value:
        raise InvalidRequest("A form field contains a NUL character")
    return value
