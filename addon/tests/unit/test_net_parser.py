# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network: URL parsing, the response parser (partial reads, limits), multipart bodies, header validation."""
from __future__ import annotations

import pytest
from slicewright.core.network import errors
from slicewright.core.network.httpclient import (Limits, Response, ResponseParser, Target, _validate_headers,
                                                 build_head, parse_url)
from slicewright.core.network.multipart import BytesBody, MultipartBody, content_disposition


def parse(raw: bytes, *, method="GET", limits=Limits(), step=None, eof=True):
    p = ResponseParser(method, limits, host="h", port=1)
    if step:
        for i in range(0, len(raw), step):
            p.feed(raw[i:i + step])
    else:
        p.feed(raw)
    if eof and not p.done:
        p.feed_eof()
    return p


OK = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nX-A: 1\r\nx-a: 2\r\n\r\nhello"


# -- URLs --------------------------------------------------------------------------------------------------------

def test_parse_url_defaults_and_query():
    assert parse_url("http://Octopi.local/api?x=1") == Target("http", "octopi.local", 80, "/api?x=1")
    assert parse_url("https://10.0.0.5:8443") == Target("https", "10.0.0.5", 8443, "/")
    t = parse_url("http://[::1]:7125/server/info")
    assert (t.host, t.port, t.authority) == ("::1", 7125, "[::1]:7125")
    assert parse_url("https://h").authority == "h" and parse_url("http://h:81").authority == "h:81"


@pytest.mark.parametrize("url", ["ftp://h/", "file:///etc/passwd", "http://", "http://u:p@h/", "http://h /x",
                                 "http://h/\r\nX: y", "http://h:99999/", "", "//h/x"])
def test_parse_url_rejects(url):
    with pytest.raises(errors.InvalidRequest):
        parse_url(url)


def test_url_credentials_error_does_not_echo_them():
    with pytest.raises(errors.InvalidRequest) as exc:
        parse_url("http://admin:hunter2@h/")
    assert "hunter2" not in str(exc.value) and "admin" not in str(exc.value)


def test_header_validation():
    assert _validate_headers({"X-Api-Key": "abc"}) == [("X-Api-Key", "abc")]
    for bad in ({"X\r\nEvil": "1"}, {"X": "a\r\nEvil: 1"}, {"Host": "evil"}, {"Content-Length": "1"},
                {"transfer-encoding": "chunked"}, {"": "x"}):
        with pytest.raises(errors.InvalidRequest):
            _validate_headers(bad)


def test_header_error_never_echoes_the_value():
    with pytest.raises(errors.InvalidRequest) as exc:
        _validate_headers({"X-Api-Key": "s3cret\nInjected: 1"})
    assert "s3cret" not in str(exc.value)


def test_build_head():
    t = parse_url("http://h:5000/api/files/local?a=b")
    head = build_head("post", t, [("X-Api-Key", "k")], BytesBody(b"abc", "text/plain")).decode()
    assert head.startswith("POST /api/files/local?a=b HTTP/1.1\r\nHost: h:5000\r\n")
    assert "Connection: close" in head and "Content-Length: 3" in head and "X-Api-Key: k" in head
    assert head.endswith("\r\n\r\n")
    assert "Content-Length: 0" in build_head("POST", t, [], None).decode()
    assert "Content-Length" not in build_head("GET", t, [], None).decode()


# -- the parser --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("step", [None, 1, 2, 3, 7])
def test_content_length_response_in_any_split(step):
    p = parse(OK, step=step)
    assert (p.done, p.status, p.reason, bytes(p.body)) == (True, 200, "OK", b"hello")
    assert p.headers == [("content-length", "5"), ("x-a", "1"), ("x-a", "2")]


@pytest.mark.parametrize("step", [None, 1, 5])
def test_chunked_response_with_extension_and_trailer(step):
    raw = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
           b"5;ext=1\r\nhello\r\n6\r\n world\r\n0\r\nX-Trailer: y\r\n\r\n")
    p = parse(raw, step=step)
    assert p.done and bytes(p.body) == b"hello world"


def test_read_until_close_needs_the_eof():
    raw = b"HTTP/1.0 200 OK\r\n\r\nabc"
    p = parse(raw, eof=False)
    assert not p.done and p.head_done
    p.feed(b"def")
    p.feed_eof()
    assert p.done and bytes(p.body) == b"abcdef"


def test_truncated_body_is_a_lost_connection():
    with pytest.raises(errors.ConnectionLost, match="before the response was complete"):
        parse(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc")
    with pytest.raises(errors.ConnectionLost, match="without answering"):
        parse(b"")


def test_interim_100_continue_is_skipped():
    p = parse(b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n")
    assert p.done and p.status == 201


def test_no_body_for_204_304_and_head():
    assert parse(b"HTTP/1.1 204 No Content\r\nContent-Length: 4\r\n\r\n").done
    assert parse(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n", method="HEAD").done


def test_bytes_after_a_complete_response_are_ignored():
    p = parse(OK + b"GARBAGE")
    assert p.done and bytes(p.body) == b"hello"


@pytest.mark.parametrize("raw", [
    b"SMTP 220 hi\r\n\r\n",
    b"HTTP/2 200 OK\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nBad Header\r\n\r\n",
    b"HTTP/1.1 200 OK\r\n folded: x\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\nab",
    b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 1e3\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\n",
    b"HTTP/1.1 101 Switching Protocols\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nabXX",
])
def test_malformed_responses_are_protocol_errors(raw):
    with pytest.raises(errors.ProtocolError):
        parse(raw)


def test_oversized_head_is_rejected_before_the_end_arrives():
    p = ResponseParser("GET", Limits(max_head_bytes=1024))
    with pytest.raises(errors.LimitExceeded):
        for _ in range(100):
            p.feed(b"X-Pad: " + b"a" * 100 + b"\r\n")      # never a blank line: the buffer must not grow forever
    assert len(p._buf) <= 1024 + 200


def test_header_count_and_line_limits():
    many = b"HTTP/1.1 200 OK\r\n" + b"".join(b"X-%d: v\r\n" % i for i in range(30)) + b"\r\n"
    with pytest.raises(errors.LimitExceeded):
        parse(many, limits=Limits(max_headers=10))
    long = b"HTTP/1.1 200 OK\r\nX: " + b"a" * 300 + b"\r\n\r\n"
    with pytest.raises(errors.LimitExceeded):
        parse(long, limits=Limits(max_line_bytes=100))


def test_announced_and_actual_body_size_limits():
    with pytest.raises(errors.LimitExceeded, match="announced"):
        parse(b"HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n", limits=Limits(max_body_bytes=1000))
    with pytest.raises(errors.LimitExceeded):                # a chunked body gives no length up front
        parse(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + b"400\r\n" + b"a" * 1024 + b"\r\n0\r\n\r\n",
              limits=Limits(max_body_bytes=1000))
    p = ResponseParser("GET", Limits(max_body_bytes=100))    # read-until-close
    p.feed(b"HTTP/1.1 200 OK\r\n\r\n" + b"a" * 100)
    with pytest.raises(errors.LimitExceeded):
        p.feed(b"b")


def test_response_helpers():
    r = Response(404, "Not Found\x00\x01", [("x", "1"), ("x", "2")], b'{"a": 1}', "http://h:80")
    assert r.header("X") == "1, 2" and r.header("nope", "d") == "d" and not r.ok
    assert r.json() == {"a": 1}
    with pytest.raises(errors.HttpStatusError) as exc:
        r.raise_for_status()
    assert str(exc.value) == "http://h:80 answered HTTP 404 Not Found" and exc.value.status == 404
    with pytest.raises(errors.ProtocolError):
        Response(200, "OK", [], b"<html>", "http://h").json()
    assert Response(200, "OK").raise_for_status().ok
    redirect = Response(302, "Found", [], b"", "http://h")
    with pytest.raises(errors.HttpStatusError, match="redirects are not followed"):
        redirect.raise_for_status()


# -- multipart ---------------------------------------------------------------------------------------------------

def test_multipart_body_bytes_and_length(tmp_path):
    f = tmp_path / "part.gcode"
    f.write_bytes(b"G1 X1\n" * 1000)
    body = MultipartBody([("path", "sub"), ("print", "true")], file_field="file", file_path=str(f),
                         filename="part.gcode", content_type="application/octet-stream", chunk_size=1024,
                         boundary="BOUND")
    data = b"".join(body.chunks())
    assert len(data) == body.length and body.content_type == "multipart/form-data; boundary=BOUND"
    assert data.startswith(b'--BOUND\r\nContent-Disposition: form-data; name="path"\r\n\r\nsub\r\n')
    assert b'name="file"; filename="part.gcode"\r\nContent-Type: application/octet-stream\r\n\r\n' in data
    assert (b"G1 X1\n" * 1000) in data and data.endswith(b"\r\n--BOUND--\r\n")
    assert b"".join(body.chunks()) == data                    # replayable


def test_multipart_streams_in_chunks_not_whole(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 10_000)
    body = MultipartBody([], file_field="file", file_path=str(f), filename="big.bin", chunk_size=1000)
    sizes = [len(c) for c in body.chunks()]
    assert max(sizes[1:-1]) == 1000 and len(sizes) == 12       # head, ten file chunks, tail


def test_multipart_filename_escaping():
    assert content_disposition("file", 'a"b.gcode') == 'form-data; name="file"; filename="a%22b.gcode"'
    cd = content_disposition("file", "Würfel.gcode")
    assert 'filename="W_rfel.gcode"' in cd and "filename*=UTF-8''W%C3%BCrfel.gcode" in cd
    for bad in ("a\r\nb", "a\x00b"):
        with pytest.raises(errors.InvalidRequest):
            content_disposition("file", bad)
    with pytest.raises(errors.InvalidRequest):
        content_disposition("na\nme")


def test_multipart_file_problems(tmp_path):
    with pytest.raises(errors.UploadError):
        MultipartBody([], file_field="f", file_path=str(tmp_path / "missing"), filename="x")
    f = tmp_path / "shrinks.bin"
    f.write_bytes(b"y" * 5000)
    body = MultipartBody([], file_field="f", file_path=str(f), filename="x", chunk_size=1000)
    f.write_bytes(b"y" * 100)
    with pytest.raises(errors.UploadError, match="shorter"):
        list(body.chunks())
    f.write_bytes(b"y" * 9000)                                  # grew: cut at the original size
    assert len(b"".join(body.chunks())) == body.length
    with pytest.raises(errors.InvalidRequest):
        MultipartBody([("a", "x\x00y")], file_field="f", file_path=str(f), filename="x")


def test_error_messages_carry_only_the_target():
    e = errors.ConnectionRefused("x", host="::1", port=80)
    assert (e.host, e.port, str(e)) == ("::1", 80, "x") and errors.target("::1", 80) == "[::1]:80"
    assert errors.target("h", None) == "h"
