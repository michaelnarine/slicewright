# SPDX-License-Identifier: GPL-3.0-or-later
"""core/redact.py: a secret never appears in str, repr, format, log output, tracebacks or error messages."""
from __future__ import annotations

import copy
import io
import logging
import pickle

import pytest
from slicewright.core import logs, redact
from slicewright.core.redact import MASK, RedactingFilter, SecretStr

SECRET = "hunter2-ACCESS-9f3a"


@pytest.fixture(autouse=True)
def clean():
    redact.clear()
    yield
    redact.clear()


def test_secretstr_never_prints_but_reveals_on_request():
    s = SecretStr(SECRET)
    for text in (str(s), repr(s), f"{s}", f"{s!r}", f"{s:>20}", "%s" % s, "%r" % s, format(s), str([s]), str({"k": s})):
        assert SECRET not in text
    assert s.reveal() == SECRET and bool(s) and len(s) == len(SECRET)
    assert s == SecretStr(SECRET) and s != SecretStr("other") and hash(s) == hash(SecretStr(SECRET))
    assert copy.copy(s) is s and copy.deepcopy(s) is s
    with pytest.raises(TypeError):
        pickle.dumps(s)


def test_redact_masks_registered_secrets_longest_first():
    redact.register(SECRET)
    redact.register(SECRET + "-longer")
    assert redact.redact(f"a {SECRET} b {SECRET}-longer c") == f"a {MASK} b {MASK} c"
    redact.forget(SECRET)
    assert SECRET in redact.redact(f"a {SECRET}")


def test_short_values_are_not_registered():
    redact.register("abc")
    redact.register(None)
    redact.register("")
    assert redact.known_count() == 0


@pytest.mark.parametrize("text,gone", [
    ("X-Api-Key: abcdef123456", "abcdef123456"),
    ("x-api-key=abcdef123456&x=1", "abcdef123456"),
    ('{"access_code": "12345678"}', "12345678"),
    ("{'password': 'p@ss word'}", "p@ss"),
    ("access-code: 87654321", "87654321"),
    ("Authorization: Bearer abc.def-ghi_123", "abc.def-ghi_123"),
    ("Authorization: Basic YWRtaW46cGFzcw==", "YWRtaW46cGFzcw=="),
    ("connect https://admin:s3cr3t@octopi.local/api", "s3cr3t"),
    ("api_key = ZZZZ-1234-ZZZZ", "ZZZZ-1234-ZZZZ"),
    ("token=eyJhbGciOi.payload.sig;", "eyJhbGciOi"),
])
def test_patterns_catch_credential_shaped_text(text, gone):
    out = redact.redact(text)
    assert gone not in out and MASK in out


def test_ordinary_text_is_left_alone():
    text = "Slicing took 12 s; the printer 'Voron' at 192.168.1.5:7125 answered HTTP 200 OK"
    assert redact.redact(text) == text


def capture(level=logging.DEBUG):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("slicewright.test_redact")
    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(handler)
    return logger, stream, handler


def test_log_message_args_tracebacks_and_stacks_are_scrubbed():
    redact.register(SECRET)
    logger, stream, handler = capture()
    try:
        logger.info("plain %s and %r and %d", SECRET, SECRET, 5)
        logger.warning("header X-Api-Key: %s", "differentSecretValue")
        logger.error("with %(s)s", {"s": SECRET})
        try:
            raise RuntimeError(f"failed with {SECRET} and password=otherpass99")
        except RuntimeError:
            logger.exception("boom %s", SECRET)
        logger.info("stack", stack_info=True, extra={})
        logger.info("secret object %s %r", SecretStr(SECRET), SecretStr(SECRET))
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    for leaked in (SECRET, "differentSecretValue", "otherpass99"):
        assert leaked not in out, leaked
    assert "boom" in out and "RuntimeError" in out and "5" in out          # still useful


def test_filter_never_raises():
    class Bad:
        def __str__(self):
            raise ValueError("no")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "%s", (Bad(),), None)
    assert RedactingFilter().filter(record) is True
    assert "withheld" in record.msg


def test_logs_setup_attaches_the_filter_to_every_handler(tmp_path):
    redact.register(SECRET)
    logs.setup("DEBUG", str(tmp_path))
    try:
        logger = logs.get_logger("net")
        logger.warning("connecting with access_code=%s then %s", "12345678", SECRET)
        for h in logging.getLogger("slicewright").handlers:
            h.flush()
        assert all(any(isinstance(f, RedactingFilter) for f in h.filters)
                   for h in logging.getLogger("slicewright").handlers)
        text = (tmp_path / logs.LOG_FILE_NAME).read_text()
    finally:
        logs.teardown()
    assert "12345678" not in text and SECRET not in text and "connecting with" in text
